"""Additional task-type engines: resize bounding box, mark spare wires."""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re

try:
    import ezdxf
except ImportError as e:  # pragma: no cover
    raise ImportError("ezdxf is required") from e


def _find_box_around_text(doc, label: str, tol: float = 2.0) -> Optional[Any]:
    """Find a closed LWPOLYLINE/LINE rectangle near a text label."""
    msp = doc.modelspace()
    # Tokenize label so "RELAY 15 box" matches text containing "RELAY" or "15"
    label_parts = [p.strip() for p in re.split(r"[^A-Z0-9]+", label.upper()) if p.strip()]
    label_pos = None
    for ent in msp:
        if ent.dxftype() in ("TEXT", "MTEXT"):
            txt = (ent.dxf.text if ent.dxftype() == "TEXT" else ent.text or "").upper()
            if label in txt or any(part in txt for part in label_parts):
                label_pos = (ent.dxf.insert.x, ent.dxf.insert.y)
                break
    if not label_pos:
        return None

    candidates = []
    for ent in msp:
        if ent.dxftype() == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in ent.get_points("xy")]
            if ent.closed and len(pts) in (4, 5):
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
                # Distance from label to box rectangle (or center)
                dx = max(minx - label_pos[0], 0, label_pos[0] - maxx)
                dy = max(miny - label_pos[1], 0, label_pos[1] - maxy)
                dist = (dx * dx + dy * dy) ** 0.5
                # Box must be plausible rectangle and near label
                aspect = (maxx - minx) / max(maxy - miny, 1e-6)
                if dist < tol and 0.1 < aspect < 10:
                    candidates.append((dist, ent))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _normalize_regions(parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
    regions = parameters.get("regions", [])
    if isinstance(regions, dict):
        regions = [regions]
    if not regions and parameters.get("region"):
        regions = [parameters["region"]]
    return regions


def _point_in_region(pt: Tuple[float, float], verts: List[Tuple[float, float]]) -> bool:
    from matplotlib.path import Path as MplPath
    try:
        return bool(MplPath(verts).contains_point(pt))
    except Exception:
        return False


class ResizeBoundingBoxEngine:
    """Shrink a rectangular box around a component label."""

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        label = parameters.get("label") or parameters.get("target_description", "")
        shrink = parameters.get("shrink_factor", 0.8)
        doc = ezdxf.readfile(dxf_path)
        box = _find_box_around_text(doc, label)
        if not box:
            doc.saveas(out_dxf)
            return {"engine": "resize_bounding_box", "success": False,
                    "error": f"box not found for {label}"}

        pts = [(p[0], p[1]) for p in box.get_points("xy")]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        new_pts = []
        for x, y in pts:
            nx = cx + (x - cx) * shrink
            ny = cy + (y - cy) * shrink
            new_pts.append((nx, ny))
        box.set_points(new_pts)
        doc.saveas(out_dxf)
        return {
            "engine": "resize_bounding_box",
            "label": label,
            "shrink": shrink,
            "output_dxf": out_dxf,
        }


class MarkSpareWiresEngine:
    """Mark clouded wire runs as spare by drawing a dashed HIDDEN rectangle
    around each clouded region. This mirrors the accepted reference style
    for Pair 1."""

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        regions = _normalize_regions(parameters)
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        added = 0
        for region in regions:
            verts = region.get("verts", [])
            if len(verts) < 3:
                continue
            xs = [v[0] for v in verts]
            ys = [v[1] for v in verts]
            minx, maxx = min(xs), max(xs)
            miny, maxy = min(ys), max(ys)
            rect = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)]
            msp.add_lwpolyline(rect, close=True, dxfattribs={
                "linetype": "HIDDEN",
                "layer": "0",
            })
            added += 1
        doc.saveas(out_dxf)
        return {"engine": "mark_spare_wires", "added_rectangles": added, "output_dxf": out_dxf}
