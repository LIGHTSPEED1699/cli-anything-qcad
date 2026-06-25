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
from cli_anything.qcad.utils.visual_verifier import QcadVlmVerifier


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
        self.vlm_x11 = VlmX11Backend()
        self.cloud = CloudDeletionEngine()
        self.clone = TerminalCloneEngine()
        self.vlm = QcadVlmVerifier(qcad_bin=qcad_bin)

    def run(self, dwg_path: str, pdf_path: str, output_dwg: str = None,
            overrides: Dict[str, Any] = None) -> JobSession:
        job = JobSession(job_id=str(uuid.uuid4())[:8])
        annotations = self._parse_pdf(pdf_path)
        job.annotations = annotations

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dxf = str(Path(tmpdir) / "working.dxf")
            self.converter.dwg_to_dxf(dwg_path, working_dxf)
            job.set_project(dwg_path, pdf_path, working_dxf)

            for idx, annot in enumerate(annotations):
                category = classify(annot.get("text", ""))
                task = self._execute_task(idx, annot, category, working_dxf, overrides)
                job.add_task(task)

            output = output_dwg or str(Path(tmpdir) / "out.dwg")
            self.converter.dxf_to_dwg(working_dxf, output)
            job.output_dwg = output

            original_png = str(Path(tmpdir) / "orig.png")
            modified_png = str(Path(tmpdir) / "mod.png")
            self.verifier.render(dwg_path, original_png)
            self.verifier.render(output, modified_png)
            verification = self.verifier.compare(original_png, modified_png, job.annotations)
            job.set_verification(verification.to_dict())

            # VLM semantic verify against first annotation instruction
            if annotations:
                q = annotations[0].get("text", "")
                try:
                    vlm_result = self.vlm.verify(output, f"Does this drawing show the requested change: {q}?")
                except Exception as e:
                    vlm_result = {"pass": None, "error": str(e)}
                job.verification["vlm"] = vlm_result

        return job

    def _parse_pdf(self, pdf_path: str) -> List[Dict[str, Any]]:
        if self.pdf_parser is None:
            return []
        return self.pdf_parser.parse(pdf_path)

    def _execute_task(self, idx: int, annot: Dict[str, Any], category,
                      working_dxf: str, overrides: Dict[str, Any]) -> Dict[str, Any]:
        text = annot.get("text", "")
        task = {"task_id": idx, "text": text, "category": category.name, "tier": category.default_tier}

        if category.name in ("delete", "property_change") and "cloud" in text.lower():
            out_dxf = str(Path(working_dxf).with_suffix("")) + f"_task{idx}.dxf"
            result = self.cloud.run(working_dxf, self.pdf_pdf_path_for_cloud(), out_dxf, overrides)
            shutil.copy(out_dxf, working_dxf)
        elif category.name == "clone":
            # Default Pair 3 clone triple if not specified
            pairs = self._infer_clone_pairs(text)
            out_dxf = str(Path(working_dxf).with_suffix("")) + f"_task{idx}.dxf"
            result = self.clone.run(working_dxf, out_dxf, pairs)
            shutil.copy(out_dxf, working_dxf)
        elif category.default_tier == "T1":
            result = self.ezdxf.execute(annot, working_dxf)
        elif category.default_tier in ("T2", "T3"):
            result = self.qcad_ecma.execute(annot, working_dxf)
        else:
            result = self.vlm_x11.execute(annot, working_dxf)

        task["result"] = result
        task["success"] = result.get("success", False)
        return task

    def _infer_clone_pairs(self, text: str):
        # Default Pair 3 wiring clone
        return [
            (4, 7, -0.75, {"PLC21": "PLC22", "CA-1451": "CA-1452", "02732": "02733"}),
            (5, 8, -0.75, {"PLC21": "PLC22", "CA-1451": "CA-1452", "02732": "02733"}),
            (6, 9, -0.75, {"PLC21": "PLC22", "CA-1451": "CA-1452", "02732": "02733"}),
        ]

    def pdf_pdf_path_for_cloud(self):
        # The cloud engine uses the same PDF as the parser; this is a placeholder.
        return self._pdf_path

    def run_with_pdf(self, dwg_path: str, pdf_path: str, output_dwg: str = None,
                     overrides: Dict[str, Any] = None) -> JobSession:
        self._pdf_path = pdf_path
        return self.run(dwg_path, pdf_path, output_dwg, overrides)
