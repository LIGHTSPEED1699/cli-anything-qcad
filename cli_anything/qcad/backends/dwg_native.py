"""T3 backend: DWG-native editing, the tier that skips the DXF round-trip.

T1/T2 work on a DXF that was produced by converting the DWG, then convert back. That
round-trip is where BLOCK/ATTDEF loss, handle drift and coordinate drift come from.

T3 instead runs the whole task list against the original DWG in QCAD's own scripting
engine and writes a DWG directly. The DXF stays as a *planning* artifact: the planner
still reads the DXF to understand the drawing, but nothing is written back through it.

Because a DXF handle does not survive the DWG->DXF conversion (DWG 888 came back as
378), T3 resolves targets by CAD geometry and text, never by a handle from the DXF.
"""
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli_anything.qcad.backends.qcad_headless import QcadHeadless
from cli_anything.qcad.utils.region_query import (entities_in_region,
                                                  entities_in_rect,
                                                  entities_near_point,
                                                  region_rect)


def _task_ops(task: Dict[str, Any], index_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Translate one planned task into headless ops."""
    task_type = task.get("task_type")
    params = task.get("parameters") or {}
    ops: List[Dict[str, Any]] = []

    if task_type in ("change_text_value", "text_change"):
        target = params.get("target_text") or params.get("old_text") or ""
        new_value = params.get("new_value") or params.get("new_text") or ""
        if not new_value:
            return []
        # Resolve in DWG space. A DXF handle is not usable here: handles do not
        # survive the DWG->DXF conversion (DWG 888 came back as 378).
        if target:
            candidates = [r for r in index_rows
                          if r.get("t") and target.lower() in r["t"].lower()]
        else:
            # No explicit target: the annotation points at a region instead.
            region = params.get("dxf_region") or task.get("dxf_region")
            candidates = entities_in_region(index_rows, region) if region else []
        return [{"op": "set_text", "handle": r["h"], "text": new_value}
                for r in candidates if r.get("t")]

    if task_type in ("delete_clouded_entities", "delete"):
        region = params.get("dxf_region") or task.get("dxf_region")
        if not region:
            return []
        for row in entities_in_region(index_rows, region):
            ops.append({"op": "delete", "handle": row["h"]})
        return ops

    if task_type in ("add_text_label", "add"):
        content = (params.get("new_value") or params.get("text")
                   or params.get("content") or params.get("label") or "")
        # The planner puts the position in dxf_region (a polygon or bbox).
        region = params.get("dxf_region") or task.get("dxf_region")
        pos = params.get("position") or params.get("point")
        if not content:
            return []
        if pos:
            x, y = pos.get("x", 0), pos.get("y", 0)
        elif region:
            rect = region_rect(region)
            if rect:
                x, y = rect["x"], rect["y"] + rect["h"] + 0.5
            else:
                verts = region.get("verts") or []
                x = min(v[0] for v in verts) if verts else 0
                y = max(v[1] for v in verts) + 0.5 if verts else 0
        else:
            return []
        op = {"op": "add_text", "text": content, "x": x, "y": y,
              "layer": params.get("layer", "E-TEXT")}
        if params.get("height"):
            op["height"] = params["height"]
        return [op]

    if task_type in ("move_entity", "move"):
        target = params.get("target_text") or ""
        dest = params.get("destination") or {}
        if not target or not dest:
            return []
        ops = []
        for row in index_rows:
            t = row.get("t")
            if t and target.lower() in t.lower():
                ops.append({"op": "move", "handle": row["h"],
                            "dx": dest.get("x", 0) - row.get("x", 0),
                            "dy": dest.get("y", 0) - row.get("y", 0)})
        return ops

    return []


class DwgNativeEngine:
    """Run a list of planned tasks against a DWG, in DWG space."""

    def __init__(self, qcad_bin: Optional[str] = None):
        self.qcad = QcadHeadless(qcad_bin=qcad_bin)

    def index(self, dwg_path: str) -> Dict[str, Any]:
        return self.qcad.index(dwg_path)

    def query_rect(self, dwg_path: str, rect: Dict[str, float]) -> Dict[str, Any]:
        """Entities inside a CAD rectangle - the chat portal's 'what's in this box'."""
        rows = self.index(dwg_path).get("rows", [])
        return {"entities": entities_in_rect(rows, rect)}

    def query_point(self, dwg_path: str, x: float, y: float,
                    radius: float = 1.0) -> Dict[str, Any]:
        """Entities near a CAD point."""
        rows = self.index(dwg_path).get("rows", [])
        return {"entities": entities_near_point(rows, x, y, radius)}

    def run(self, dwg_path: str, tasks: List[Dict[str, Any]], output_dwg: str
            ) -> Dict[str, Any]:
        """Apply planned tasks to a DWG and write a new DWG."""
        index = self.index(dwg_path)
        rows = index.get("rows", [])
        all_ops: List[Dict[str, Any]] = []
        per_task = []
        for task in tasks:
            ops = _task_ops(task, rows)
            per_task.append({"task_id": task.get("task_id"),
                             "task_type": task.get("task_type"),
                             "ops": len(ops),
                             "supported": bool(ops) or task.get("task_type") in
                             ("change_text_value", "delete_clouded_entities",
                              "add_text_label", "move_entity")})
            all_ops.extend(ops)

        if not all_ops:
            return {"success": False,
                    "error": "no T3-compatible operations were produced",
                    "tasks": per_task}

        result = self.qcad.apply(dwg_path, output_dwg, all_ops)
        if "error" in result:
            return {"success": False, "error": result["error"], "tasks": per_task}
        audit = result.get("audit") or {}
        # Success comes from the audit, not from the script's per-op "ok".
        # "ok" only means the call did not throw.
        failed = audit.get("failed", [])
        return {
            "success": bool(result.get("success")),
            "audit_passed": audit.get("passed"),
            "ops_checked": audit.get("checked"),
            "ops_verified": audit.get("verified"),
            "output_dwg": output_dwg,
            "applied": audit.get("verified", 0),
            "failed": failed,
            "audit_results": audit.get("results"),
            "ops": result.get("ops"),
            "tasks": per_task,
        }
