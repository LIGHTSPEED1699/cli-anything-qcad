"""Text value modification engine: replace TEXT/MTEXT/ATTRIB content."""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re

try:
    import ezdxf
except ImportError as e:  # pragma: no cover
    raise ImportError("ezdxf is required") from e

from cli_anything.qcad.utils.dxf_entity_index import DxfEntityIndex


def _find_text_entities(doc, target: str, near_point: Optional[Tuple[float, float]] = None,
                        near_tol: float = 2.0, regex: bool = False) -> List[Any]:
    """Find TEXT/MTEXT/ATTRIB entities matching target, optionally near a point."""
    msp = doc.modelspace()
    matches = []
    target_upper = target.upper()
    for ent in msp:
        etype = ent.dxftype()
        text = ""
        pt = None
        if etype == "TEXT":
            text = (ent.dxf.text or "").upper()
            pt = (ent.dxf.insert.x, ent.dxf.insert.y)
        elif etype == "MTEXT":
            text = (ent.text or "").upper()
            pt = (ent.dxf.insert.x, ent.dxf.insert.y)
        elif etype == "ATTRIB":
            text = (ent.dxf.text or "").upper()
            try:
                pt = (ent.dxf.insert.x, ent.dxf.insert.y)
            except Exception:
                pass
        if not text:
            continue
        matched = (target_upper == text) or (target_upper in text) if not regex else bool(re.search(target, text, re.IGNORECASE))
        if not matched:
            continue
        if near_point and pt:
            dx = pt[0] - near_point[0]
            dy = pt[1] - near_point[1]
            if dx * dx + dy * dy > near_tol * near_tol:
                continue
        matches.append(ent)
    return matches


def _nearest_text_style(doc, point: Tuple[float, float], tol: float = 1.0) -> Dict[str, Any]:
    """Find nearest TEXT/MTEXT to point and return style properties."""
    msp = doc.modelspace()
    best = None
    best_dist = float("inf")
    for ent in msp:
        etype = ent.dxftype()
        if etype not in ("TEXT", "MTEXT"):
            continue
        try:
            pt = (ent.dxf.insert.x, ent.dxf.insert.y)
        except Exception:
            continue
        d = (pt[0] - point[0]) ** 2 + (pt[1] - point[1]) ** 2
        if d < best_dist:
            best_dist = d
            best = ent
    if not best or best_dist > tol * tol:
        return {"height": 0.125, "layer": "0", "style": "Standard"}
    if best.dxftype() == "TEXT":
        return {
            "height": getattr(best.dxf, "height", 0.125),
            "layer": best.dxf.layer,
            "style": getattr(best.dxf, "style", "Standard"),
            "rotation": getattr(best.dxf, "rotation", 0.0),
        }
    return {
        "height": getattr(best.dxf, "text_height", 0.125),
        "layer": best.dxf.layer,
        "style": getattr(best.dxf, "style", "Standard"),
    }


class ChangeTextValueEngine:
    """Replace existing text/MTEXT/ATTRIB content."""

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        target = parameters.get("target_text") or parameters.get("target_description")
        new_value = parameters.get("new_value", "")
        near = parameters.get("near_point")
        regex = parameters.get("regex", False)
        if not target or new_value is None:
            return {"engine": "change_text_value", "success": False, "error": "missing target or new_value"}

        doc = ezdxf.readfile(dxf_path)
        matches = _find_text_entities(doc, target, near_point=near, regex=regex)
        changed = 0
        for ent in matches:
            etype = ent.dxftype()
            try:
                if etype == "TEXT" or etype == "ATTRIB":
                    ent.dxf.text = new_value
                elif etype == "MTEXT":
                    ent.text = new_value
                changed += 1
            except Exception:
                pass

        doc.saveas(out_dxf)
        return {
            "engine": "change_text_value",
            "target": target,
            "new_value": new_value,
            "matches_found": len(matches),
            "changed": changed,
            "output_dxf": out_dxf,
        }


class AddTextLabelEngine:
    """Add a new TEXT/MTEXT label at a specified location, matching nearby style."""

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        text = parameters.get("text", "")
        point = parameters.get("point")
        layer = parameters.get("layer")
        height = parameters.get("height")
        region = parameters.get("region")

        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        if not point and region:
            # Pick a sensible point: upper-left of bbox
            bbox = region.get("bbox")
            if bbox:
                point = (bbox[0], bbox[3])
        if not point:
            return {"engine": "add_text_label", "success": False, "error": "no insertion point"}

        style = _nearest_text_style(doc, point)
        if layer:
            style["layer"] = layer
        if height:
            style["height"] = height

        msp.add_text(text, dxfattribs={
            "insert": point,
            "height": style["height"],
            "layer": style["layer"],
            "style": style["style"],
            "rotation": style.get("rotation", 0.0),
        })
        doc.saveas(out_dxf)
        return {
            "engine": "add_text_label",
            "text": text,
            "insert": point,
            "output_dxf": out_dxf,
        }
