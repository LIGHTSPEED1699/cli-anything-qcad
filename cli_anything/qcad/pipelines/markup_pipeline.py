"""Generic markup pipeline: planner -> task-type engines -> verify.

This replaces the old pair-specific dispatch with a reusable architecture:
  1. Ingest PDF annotations.
  2. Calibrate PDF coordinates to DXF.
  3. Classify each annotation into a task type (rule + VLM).
  4. Route to the correct engine.
  5. Execute tasks in order, checkpointing after each.
  6. Verify per-task with render + VLM.
  7. Export final DXF back to DWG.
"""
import json
import shutil
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli_anything.qcad.backends.dwg_converter import DwgConverter
from cli_anything.qcad.core.planner import MarkupPlanner, Task
from cli_anything.qcad.engines.delete_clouded_entities import DeleteCloudedEntitiesEngine
from cli_anything.qcad.engines.text_value import ChangeTextValueEngine, AddTextLabelEngine
from cli_anything.qcad.engines.clone_terminal_wires import CloneTerminalWiresEngine
from cli_anything.qcad.engines.extra_ops import ResizeBoundingBoxEngine, MarkSpareWiresEngine
from cli_anything.qcad.engines.geometry_ops import AddDimensionEngine, AddLeaderEngine, MoveEntityEngine
from cli_anything.qcad.utils.visual_verify import QcadRenderer


class MarkupPipeline:
    """Run the full DWG markup pipeline using reusable task-type engines."""

    _engines = {
        "delete_clouded_entities": DeleteCloudedEntitiesEngine,
        "change_text_value": ChangeTextValueEngine,
        "add_text_label": AddTextLabelEngine,
        "clone_terminal_wires": CloneTerminalWiresEngine,
        "resize_bounding_box": ResizeBoundingBoxEngine,
        "mark_spare_wires": MarkSpareWiresEngine,
        "add_dimension": AddDimensionEngine,
        "add_leader": AddLeaderEngine,
        "move_entity": MoveEntityEngine,
    }

    def __init__(
        self,
        pdf_parser=None,
        converter=None,
        verifier=None,
        qcad_bin: Optional[str] = None,
        per_task_verify: bool = False,
        max_verify_retries: int = 1,
    ):
        self.pdf_parser = pdf_parser
        self.converter = converter or DwgConverter(qcad_bin=qcad_bin)
        self.verifier = verifier
        self.qcad_bin = Path(qcad_bin) if qcad_bin else None
        self.per_task_verify = per_task_verify
        self.max_verify_retries = max_verify_retries
        self.max_engine_retries = 1
        self.planner = MarkupPlanner()

    def run(
        self,
        dwg_path: str,
        pdf_path: str,
        output_dwg: Optional[str] = None,
        overrides=None,
        artifacts_dir: Optional[str] = None,
        skip_vlm: bool = False,
    ) -> Dict[str, Any]:
        output_dir = Path(artifacts_dir) if artifacts_dir else Path(output_dwg).parent if output_dwg else Path.cwd()
        output_dir.mkdir(parents=True, exist_ok=True)
        work = output_dir / "work"
        work.mkdir(parents=True, exist_ok=True)

        dwg_work = work / Path(dwg_path).name
        pdf_work = work / Path(pdf_path).name
        shutil.copy2(dwg_path, dwg_work)
        shutil.copy2(pdf_path, pdf_work)

        original_dxf = work / "original.dxf"
        if not self.converter.dwg_to_dxf(str(dwg_work), str(original_dxf)):
            return {"success": False, "error": "DWG→DXF conversion failed"}

        # Plan tasks
        tasks = self.planner.plan(str(pdf_work), str(original_dxf))
        if not tasks:
            return {"success": False, "error": "No tasks generated from PDF annotations"}

        # Sort tasks: deletions first, then changes/adds, then mark spare last
        order = {
            "delete_clouded_entities": 0,
            "resize_bounding_box": 1,
            "change_text_value": 2,
            "add_text_label": 3,
            "clone_terminal_wires": 4,
            "mark_spare_wires": 9,
        }
        tasks.sort(key=lambda t: (order.get(t.task_type, 5), t.task_id))

        # Execute tasks with checkpoints
        current_dxf = str(original_dxf)
        task_reports: List[Dict[str, Any]] = []
        qcad_dir = str(self.qcad_bin.parent) if self.qcad_bin else None
        renderer = QcadRenderer(qcad_dir=qcad_dir)

        for task in tasks:
            next_dxf = str(work / f"checkpoint_{task.task_id}.dxf")
            report, attempt = self._execute_task_with_retry(current_dxf, task, next_dxf)
            task_reports.append(report)

            if report.get("success") and self.per_task_verify and self.verifier and not skip_vlm:
                verify_png = str(work / f"verify_{task.task_id}.png")
                renderer.compare(current_dxf, next_dxf, verify_png, converter=self.converter)
                vq = self._task_verification_prompt(task)
                ok, vlm = self._verify_with_retry(next_dxf, vq)
                report["verify"] = {"pass": ok, "answer": vlm, "png": verify_png}
                if not ok:
                    report["success"] = False
                    report["error"] = report.get("error") or f"per-task VLM verification failed for {task.task_id}"

            current_dxf = next_dxf

        final_dxf = str(work / "final.dxf")
        shutil.copy2(current_dxf, final_dxf)

        # Export to DWG
        if output_dwg:
            out_dwg = Path(output_dwg)
        else:
            out_dwg = output_dir / f"{Path(dwg_path).stem}_modified.dwg"
        if not self.converter.dxf_to_dwg(final_dxf, str(out_dwg)):
            return {"success": False, "error": "DXF→DWG export failed"}

        # Compare original vs modified
        comparison_png = str(output_dir / "comparison.png")
        comparison = renderer.compare(str(dwg_work), str(out_dwg), comparison_png)

        # Optional final VLM verification
        vlm_report = None
        if not skip_vlm and self.verifier:
            try:
                vlm_report = self.verifier.verify(str(out_dwg), self._verification_prompt(tasks))
            except Exception:
                vlm_report = {"error": traceback.format_exc()}

        report_data = {
            "success": all(r.get("success", False) for r in task_reports),
            "input_dwg": str(dwg_path),
            "input_pdf": str(pdf_path),
            "output_dwg": str(out_dwg),
            "tasks": [t.to_dict() for t in tasks],
            "task_reports": task_reports,
            "comparison": comparison,
            "vlm_report": vlm_report,
        }
        (output_dir / "pipeline_report.json").write_text(
            json.dumps(report_data, indent=2, default=str)
        )
        return report_data

    def _execute_task(self, input_dxf: str, task: Task, output_dxf: str,
                        params_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        engine_cls = self._engines.get(task.task_type)
        if not engine_cls:
            shutil.copy2(input_dxf, output_dxf)
            return {"task_id": task.task_id, "success": False,
                    "error": f"no engine for {task.task_type}"}

        engine = engine_cls()
        params = dict(task.parameters)
        if params_override:
            params.update(params_override)
        if task.dxf_region:
            if task.task_type == "delete_clouded_entities":
                params.setdefault("regions", []).append(task.dxf_region)
            elif task.task_type == "mark_spare_wires":
                params["region"] = task.dxf_region
            elif task.task_type in ("change_text_value", "add_text_label"):
                if "point" not in params:
                    reg = task.dxf_region
                    if reg.get("type") == "bbox":
                        c = reg["coords"]
                        params["point"] = (c[0], c[3])
                        params["near_point"] = params["point"]
                    elif reg.get("type") == "polygon":
                        bbox = reg.get("bbox")
                        if bbox:
                            params["point"] = (bbox[0], bbox[3])
                            params["near_point"] = params["point"]
                    elif reg.get("type") == "point":
                        params["point"] = reg["coords"]
                        params["near_point"] = reg["coords"]

        try:
            result = engine.run(input_dxf, params, output_dxf)
            return {"task_id": task.task_id, "task_type": task.task_type,
                    "success": True, **result}
        except Exception as e:
            shutil.copy2(input_dxf, output_dxf)
            return {"task_id": task.task_id, "task_type": task.task_type,
                    "success": False, "error": str(e),
                    "traceback": traceback.format_exc()}

    def _execute_task_with_retry(self, input_dxf: str, task: Task,
                                  output_dxf: str) -> tuple:
        """Run task. If it fails, optionally perturb parameters and retry once."""
        report = self._execute_task(input_dxf, task, output_dxf)
        attempt = 1
        if not report.get("success") and self.max_engine_retries > 0:
            perturbed = self._perturb_params(task.task_type, dict(task.parameters), attempt)
            report = self._execute_task(input_dxf, task, output_dxf, perturbed)
            report["retried"] = True
            report["retry_params"] = perturbed
            attempt = 2
        return report, attempt

    def _perturb_params(self, task_type: str, params: Dict[str, Any],
                        attempt: int) -> Dict[str, Any]:
        """Adjust engine parameters to recover from failures or VLM rejection."""
        p = dict(params)
        if task_type == "delete_clouded_entities":
            # Be more conservative: shrink tolerance to avoid over-deletion
            p["tolerance"] = p.get("tolerance", 0.0) - 0.5 * attempt
        elif task_type == "resize_bounding_box":
            # Expand the search margin
            p["margin"] = p.get("margin", 0.0) + 2.0 * attempt
        elif task_type == "change_text_value":
            p["fuzzy"] = True
            p["threshold"] = max(0.5, p.get("threshold", 0.9) - 0.2 * attempt)
        elif task_type == "mark_spare_wires":
            p["label_offset"] = p.get("label_offset", 2.0) + 2.0 * attempt
        elif task_type == "clone_terminal_wires":
            p["tolerance"] = p.get("tolerance", 1.0) + 1.0 * attempt
        elif task_type == "add_text_label":
            p["height"] = p.get("height", 2.5) * (1 + 0.2 * attempt)
        elif task_type in ("add_dimension", "add_leader"):
            p["offset"] = p.get("offset", 5.0) + 2.0 * attempt
        elif task_type == "move_entity":
            p["tol"] = p.get("tol", 5.0) + 3.0 * attempt
        return p

    def _verify_with_retry(self, dwg_path: str, question: str) -> tuple:
        for attempt in range(self.max_verify_retries):
            try:
                result = self.verifier.verify(dwg_path, question)
                answer = str(result.get("answer", "")).lower()
                ok = "yes" in answer[:80]
                return ok, result.get("answer")
            except Exception as e:
                if attempt == self.max_verify_retries - 1:
                    return False, f"VLM verification error after {self.max_verify_retries} attempts: {e}"
        return False, "verification failed"

    def _task_verification_prompt(self, task: Task) -> str:
        action = task.task_type.replace("_", " ")
        region = task.dxf_region
        region_desc = ""
        if region:
            bbox = region.get("bbox")
            if bbox:
                region_desc = f" in region {bbox}"
        return (
            f"Verify the CAD drawing after this operation: {action}{region_desc}. "
            f"Instruction was: '{task.text}'. Confirm the change looks correct and "
            "no unintended entities were modified. Answer YES or NO."
        )

    def _verification_prompt(self, tasks: List[Task]) -> str:
        actions = ", ".join(sorted({t.task_type for t in tasks}))
        return (
            f"Verify this CAD drawing after applying these operations: {actions}. "
            "Confirm no over-deletion, terminal blocks and title blocks preserved, "
            "text changes applied, and cloned geometry is clean with no duplicate arcs. "
            "Answer YES or NO."
        )
