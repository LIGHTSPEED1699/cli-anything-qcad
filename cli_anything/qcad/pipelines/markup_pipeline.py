"""Unified markup pipeline using categorized engines."""
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List

from cli_anything.qcad.core.session import JobSession
from cli_anything.qcad.core.categories import classify
from cli_anything.qcad.backends.dwg_converter import DwgConverter
from cli_anything.qcad.backends.ezdxf_backend import EzdxfBackend
from cli_anything.qcad.backends.qcad_ecma_backend import QcadEcmaBackend
from cli_anything.qcad.backends.vlm_x11_backend import VlmX11Backend
from cli_anything.qcad.engines.cloud_deletion import CloudDeletionEngine
from cli_anything.qcad.engines.terminal_clone import TerminalCloneEngine
from cli_anything.qcad.utils.visual_verify import VisualVerifier


class MarkupPipeline:
    def __init__(
        self,
        pdf_parser: Any = None,
        converter: DwgConverter = None,
        verifier: VisualVerifier = None,
        qcad_bin: str = None,
    ):
        self.pdf_parser = pdf_parser
        self.converter = converter or DwgConverter(qcad_bin=qcad_bin)
        self.verifier = verifier or VisualVerifier()
        self.ezdxf = EzdxfBackend()
        self.qcad_ecma = QcadEcmaBackend(qcad_bin=qcad_bin)
        self.vlm_x11 = VlmX11Backend(qcad_bin=qcad_bin)
        self.cloud = CloudDeletionEngine()
        self.clone = TerminalCloneEngine()
        self._pdf_path: str = ""

    def run(self, dwg_path: str, pdf_path: str, output_dwg: str = None,
            overrides: Dict[str, Any] = None, artifacts_dir: str = None,
            skip_vlm: bool = False) -> JobSession:
        self._pdf_path = pdf_path
        job = JobSession(job_id=str(uuid.uuid4())[:8])
        job.artifacts_dir = artifacts_dir
        annotations = self._parse_pdf(pdf_path)
        job.annotations = annotations

        artifact_root = Path(artifacts_dir) if artifacts_dir else None
        if artifact_root:
            artifact_root.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dxf = str(Path(tmpdir) / "working.dxf")
            success = self.converter.dwg_to_dxf(dwg_path, working_dxf)
            if not success:
                raise RuntimeError(f"Failed to convert DWG to DXF: {dwg_path}")
            job.set_project(dwg_path, pdf_path, working_dxf)
            self._original_dxf = working_dxf  # for cloud calibration

            if artifact_root:
                stage0 = str(artifact_root / "stage_00_dwg_to_dxf.dxf")
                shutil.copy(working_dxf, stage0)
                job.add_artifact("dxf", stage0, "Initial DWG→DXF conversion")

            # Run cloud-deletion tasks first on the clean converted DXF. ezdxf's saveas
            # (used by text_change) drops/re-numbers many handles, so deletion must
            # happen before any ezdxf round-trip and on the original clean DXF.
            def _task_priority(annot):
                cat = classify(annot.get("text", "")).name
                text = annot.get("text", "").lower()
                is_cloud_delete = (cat in ("delete", "property_change") and "cloud" in text)
                return 0 if is_cloud_delete else 1

            sorted_annotations = sorted(enumerate(annotations), key=lambda item: _task_priority(item[1]))

            for sort_idx, (orig_idx, annot) in enumerate(sorted_annotations):
                category = classify(annot.get("text", ""))
                task = self._execute_task(sort_idx, annot, category, working_dxf, overrides, job, artifact_root, tmpdir)
                task["task_id"] = orig_idx
                job.add_task(task)

            output = output_dwg or str(Path(tmpdir) / "out.dwg")
            success = self.converter.dxf_to_dwg(working_dxf, output)
            if not success:
                raise RuntimeError("Failed to convert DXF to DWG")
            job.output_dwg = output

            if artifact_root:
                final_dxf = str(artifact_root / "stage_final_pre_export.dxf")
                shutil.copy(working_dxf, final_dxf)
                job.add_artifact("dxf", final_dxf, "Final DXF before DWG export")
                if Path(output).resolve() != (artifact_root / Path(output).name).resolve():
                    out_copy = str(artifact_root / Path(output).name)
                    shutil.copy(output, out_copy)
                    job.add_artifact("dwg", out_copy, "Output DWG")

            original_png = str(Path(tmpdir) / "orig.png")
            modified_png = str(Path(tmpdir) / "mod.png")
            self.verifier.render(dwg_path, original_png)
            self.verifier.render(output, modified_png)
            verification = self.verifier.compare(original_png, modified_png, job.annotations)
            job.set_verification(verification.to_dict())

            if artifact_root:
                for label, src in [("original", original_png), ("modified", modified_png),
                                   ("diff", verification.diff_png)]:
                    if src and Path(src).exists():
                        dst = str(artifact_root / f"verify_{label}.png")
                        shutil.copy(src, dst)
                        job.add_artifact("png", dst, f"Verification render: {label}")

                report_path = str(artifact_root / "pipeline_report.json")
                with open(report_path, "w") as f:
                    json.dump(job.to_dict(), f, indent=2, default=str)
                job.add_artifact("json", report_path, "Machine-readable pipeline report")

            if not skip_vlm and annotations and Path(modified_png).exists():
                q = annotations[0].get("text", "")
                try:
                    vlm_result = self.verifier.vlm_verify(modified_png,
                        f"Does this drawing show the requested change: {q}?")
                except Exception as e:
                    vlm_result = {"pass": None, "error": str(e)}
                job.verification["vlm"] = vlm_result
                if artifact_root:
                    vlm_path = str(artifact_root / "vlm_verification.json")
                    with open(vlm_path, "w") as f:
                        json.dump(vlm_result, f, indent=2, default=str)
                    job.add_artifact("json", vlm_path, "VLM verification result")

        return job

    def _execute_task(self, idx: int, annot: Dict[str, Any], category,
                      working_dxf: str, overrides: Dict[str, Any],
                      job: JobSession, artifact_root: Path, tmpdir: str) -> Dict[str, Any]:
        text = annot.get("text", "")
        task = {
            "task_id": idx,
            "text": text,
            "category": category.name,
            "tier": category.default_tier,
        }

        out_dxf = str(Path(working_dxf).with_suffix("")) + f"_task{idx}.dxf"
        result = None

        # Cloud deletion runs on the *original* clean DXF, not the ezdxf-rewritten working_dxf.
        if category.name in ("delete", "property_change") and "cloud" in text.lower():
            cloud_overrides = dict(overrides or {})
            cloud_overrides.setdefault("preserve", False)
            result = self.cloud.run(self._original_dxf, self._pdf_path, out_dxf, cloud_overrides)
            if result and result.get("success"):
                # Build the cloud-deleted DXF from the *clean* original so the handles actually exist.
                from cli_anything.qcad.engines.delete_by_handle import delete_handles
                from cli_anything.qcad.utils.layer_fix import fix_layer_visibility
                cloud_deleted = str(Path(tmpdir) / f"working_task{idx}_cloud_deleted.dxf")
                delete_handles(self._original_dxf, cloud_deleted, set(result.get("deletion", [])))
                fix_layer_visibility(cloud_deleted, working_dxf)
                shutil.copy(working_dxf, out_dxf)
                result["dxf"] = out_dxf

        elif category.default_tier == "T1":
            annot["category"] = category.name
            result = self.ezdxf.execute(annot, working_dxf, out_dxf)
        elif category.default_tier in ("T2", "T3"):
            annot["category"] = category.name
            result = self.qcad_ecma.execute(annot, working_dxf)
        else:
            annot["category"] = category.name
            result = self.vlm_x11.execute(annot, working_dxf)

        if result and result.get("success"):
            shutil.copy(out_dxf, working_dxf)

        task["result"] = result or {"backend": "none", "success": False}
        task["success"] = result.get("success", False) if result else False

        if artifact_root:
            pre_stage = str(artifact_root / f"stage_{idx:02d}_{category.name}_pre.dxf")
            post_stage = str(artifact_root / f"stage_{idx:02d}_{category.name}_post.dxf")
            shutil.copy(working_dxf, pre_stage)
            if Path(out_dxf).exists():
                shutil.copy(out_dxf, post_stage)
                job.add_artifact("dxf", post_stage, f"Task {idx} {category.name} output DXF")
            job.add_artifact("dxf", pre_stage, f"Task {idx} {category.name} input DXF")

            result_path = str(artifact_root / f"task_{idx:02d}_result.json")
            with open(result_path, "w") as f:
                json.dump({"task": task, "result": result}, f, indent=2, default=str)
            job.add_artifact("json", result_path, f"Task {idx} result details")

        return task

    def _infer_clone_pairs(self, text: str, annot: Dict[str, Any]) -> List:
        pairs = annot.get("clone_pairs")
        if pairs:
            return pairs
        # Default Pair 3 wiring clone triple
        return [
            (4, 7, -0.75, {"PLC21": "PLC22", "CA-1451": "CA-1452", "02732": "02733"}),
            (5, 8, -0.75, {"PLC21": "PLC22", "CA-1451": "CA-1452", "02732": "02733"}),
            (6, 9, -0.75, {"PLC21": "PLC22", "CA-1451": "CA-1452", "02732": "02733"}),
        ]

    def _parse_pdf(self, pdf_path: str) -> List[Dict[str, Any]]:
        if self.pdf_parser is None:
            return []
        return self.pdf_parser.parse(pdf_path)
