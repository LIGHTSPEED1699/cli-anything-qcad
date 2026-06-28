"""Generic markup pipeline: planner -> task-type engines -> verify.

This replaces the old pair-specific dispatch with a reusable architecture:
  1. Ingest PDF annotations.
  2. Calibrate PDF coordinates to DXF.
  3. Classify each annotation into a task type (rule + VLM).
  4. Route to the correct engine.
  5. Execute tasks in order, checkpointing after each.
  6. Verify per-task with render + pixel diff.
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
    }

    def __init__(
        self,
        dwg_path: str,
        pdf_path: str,
        output_dir: str,
        qcad_bin: Optional[str] = None,
    ):
        self.dwg_path = Path(dwg_path)
        self.pdf_path = Path(pdf_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.qcad_bin = Path(qcad_bin) if qcad_bin else None
        self.converter = DwgConverter(qcad_bin=str(self.qcad_bin) if self.qcad_bin else None)
        self.planner = MarkupPlanner()

    def run(self) -> Dict[str, Any]:
        work = self.output_dir / "work"
        work.mkdir(parents=True, exist_ok=True)

        dwg_work = work / self.dwg_path.name
        pdf_work = work / self.pdf_path.name
        shutil.copy2(self.dwg_path, dwg_work)
        shutil.copy2(self.pdf_path, pdf_work)

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
        for task in tasks:
            next_dxf = str(work / f"checkpoint_{task.task_id}.dxf")
            report = self._execute_task(task, current_dxf, next_dxf)
            if report.get("success", True):
                current_dxf = next_dxf
            task_reports.append(report)

        # Export final DWG
        final_dxf = work / "final.dxf"
        shutil.copy2(current_dxf, final_dxf)
        final_dwg = self.output_dir / (self.dwg_path.stem + "_modified.dwg")
        if not self.converter.dxf_to_dwg(str(final_dxf), str(final_dwg)):
            return {"success": False, "error": "Final DXF→DWG conversion failed"}

        # Final comparison
        comp = self._compare(str(self.dwg_path), str(final_dwg), self.output_dir / "comparison.png")

        report = {
            "success": True,
            "tasks": [t.to_dict() for t in tasks],
            "task_reports": task_reports,
            "final_dwg": str(final_dwg),
            "comparison": comp,
        }
        (self.output_dir / "pipeline_report.json").write_text(
            json.dumps(report, indent=2, default=str)
        )
        return report

    def _execute_task(self, task: Task, input_dxf: str, output_dxf: str) -> Dict[str, Any]:
        engine_cls = self._engines.get(task.task_type)
        if not engine_cls:
            shutil.copy2(input_dxf, output_dxf)
            return {"task_id": task.task_id, "success": False,
                    "error": f"no engine for {task.task_type}"}

        engine = engine_cls()
        params = dict(task.parameters)
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

    def _compare(self, original_dwg: str, modified_dwg: str, output_png: Path) -> Dict[str, Any]:
        renderer = QcadRenderer(qcad_dir=str(self.qcad_bin.parent) if self.qcad_bin else None)
        return renderer.compare(original_dwg, modified_dwg, str(output_png))
