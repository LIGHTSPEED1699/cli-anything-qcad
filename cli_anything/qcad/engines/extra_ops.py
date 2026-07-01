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
    """Tighten a rectangular box to fit remaining entities after deletion.

    After clouded entities are deleted, the box may have empty space on one
    or more sides.  This engine finds all entities still inside the box,
    computes their bounding box, and trims each box edge inward to just
    outside the nearest remaining entity.  Edges that already have entities
    close to them are left unchanged.
    """

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        label = parameters.get("label") or parameters.get("target_description", "")
        margin = parameters.get("margin", 0.25)  # padding between entity bbox and box edge
        doc = ezdxf.readfile(dxf_path)
        box = _find_box_around_text(doc, label)
        if not box:
            doc.saveas(out_dxf)
            return {"engine": "resize_bounding_box", "success": False,
                    "error": f"box not found for {label}"}

        pts = [(p[0], p[1]) for p in box.get_points("xy")]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        box_minx, box_maxx = min(xs), max(xs)
        box_miny, box_miny = min(ys), max(ys)
        box_miny = min(ys)
        box_maxy = max(ys)
        box_minx = min(xs)
        box_maxx = max(xs)

        # Find bounding box of all entities inside the box
        ent_min_x = None
        ent_max_x = None
        ent_min_y = None
        ent_max_y = None
        msp = doc.modelspace()
        for ent in msp:
            if ent.dxftype() == "LWPOLYLINE" and ent is box:
                continue
            if ent.dxftype() == "LWPOLYLINE" and ent.closed:
                # Skip other box rectangles
                continue
            pts_ent = _entity_points(ent)
            for px, py in pts_ent:
                if box_minx - 1.0 <= px <= box_maxx + 1.0 and \
                   box_miny - 1.0 <= py <= box_maxy + 1.0:
                    if ent_min_x is None or px < ent_min_x:
                        ent_min_x = px
                    if ent_max_x is None or px > ent_max_x:
                        ent_max_x = px
                    if ent_min_y is None or py < ent_min_y:
                        ent_min_y = py
                    if ent_max_y is None or py > ent_max_y:
                        ent_max_y = py

        if ent_min_x is None:
            # No entities found inside box, leave as-is
            doc.saveas(out_dxf)
            return {"engine": "resize_bounding_box", "success": False,
                    "error": "no entities found inside box"}

        # Trim each edge inward only if there's a gap (entity bbox + margin < box edge)
        new_minx = max(box_minx, ent_min_x - margin) if ent_min_x - margin > box_minx else box_minx
        new_maxx = min(box_maxx, ent_max_x + margin) if ent_max_x + margin < box_maxx else box_maxx
        new_miny = max(box_miny, ent_min_y - margin) if ent_min_y - margin > box_miny else box_miny
        new_maxy = min(box_maxy, ent_max_y + margin) if ent_max_y + margin < box_maxy else box_maxy

        # Apply new bounds to the box vertices
        new_pts = []
        for x, y in pts:
            nx = new_minx if abs(x - box_minx) < 0.01 else new_maxx
            ny = new_miny if abs(y - box_miny) < 0.01 else new_maxy
            new_pts.append((nx, ny))
        box.set_points(new_pts)
        doc.saveas(out_dxf)

        return {
            "engine": "resize_bounding_box",
            "label": label,
            "original_bbox": [box_minx, box_miny, box_maxx, box_maxy],
            "new_bbox": [new_minx, new_miny, new_maxx, new_maxy],
            "entity_bbox": [ent_min_x, ent_min_y, ent_max_x, ent_max_y],
            "output_dxf": out_dxf,
        }


def _entity_points(ent) -> List[Tuple[float, float]]:
    """Extract representative points from a DXF entity."""
    t = ent.dxftype()
    if t == "TEXT":
        return [(ent.dxf.insert.x, ent.dxf.insert.y)]
    elif t == "MTEXT":
        return [(ent.dxf.insert.x, ent.dxf.insert.y)]
    elif t == "INSERT":
        pts = [(ent.dxf.insert.x, ent.dxf.insert.y)]
        for attrib in ent.attribs:
            ax = attrib.dxf.insert.x + ent.dxf.insert.x
            ay = attrib.dxf.insert.y + ent.dxf.insert.y
            pts.append((ax, ay))
        return pts
    elif t == "LINE":
        return [(ent.dxf.start.x, ent.dxf.start.y), (ent.dxf.end.x, ent.dxf.end.y)]
    elif t == "LWPOLYLINE":
        return [(p[0], p[1]) for p in ent.get_points("xy")]
    elif t == "CIRCLE":
        c = ent.dxf.center
        r = ent.dxf.radius
        return [(c.x - r, c.y), (c.x + r, c.y), (c.x, c.y - r), (c.x, c.y + r)]
    elif t == "ARC":
        import math
        c = ent.dxf.center
        r = ent.dxf.radius
        a1, a2 = ent.dxf.start_angle, ent.dxf.end_angle
        return [(c.x + r * math.cos(math.radians(a)), c.y + r * math.sin(math.radians(a)))
                for a in [a1, a2, (a1 + a2) / 2]]
    elif t == "POINT":
        return [(ent.dxf.location.x, ent.dxf.location.y)]
    elif t == "HATCH":
        pts = []
        for boundary in ent.paths:
            if hasattr(boundary, "vertices") and boundary.vertices:
                pts.extend((v[0], v[1]) for v in boundary.vertices)
            elif hasattr(boundary, "edges"):
                for edge in boundary.edges:
                    if edge.type == "LineEdge":
                        pts.append((edge.start[0], edge.start[1]))
                        pts.append((edge.end[0], edge.end[1]))
                    elif edge.type == "ArcEdge":
                        pts.append((edge.center[0], edge.center[1]))
                    elif edge.type == "EllipseEdge":
                        pts.append((edge.center[0], edge.center[1]))
                    elif edge.type == "SplineEdge":
                        for cp in edge.control_points:
                            pts.append((cp[0], cp[1]))
        return pts
    return []


class MarkSpareWiresEngine:
    """Mark clouded wire runs as spare.

    The PDF markup instruction 'mark spare on both ends' means the wires
    in the clouded region should be labeled or annotated as spare at both
    ends of the circuit.  The exact representation depends on drawing
    conventions (e.g. adding 'SPARE' text labels, changing line type,
    or crossing out terminal numbers).

    The previous implementation drew dashed HIDDEN rectangles around the
    clouded region, which created unwanted geometry not requested in the
    markup.  This engine is now a pass-through until a proper spare-marking
    routine is implemented.
    """

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        doc = ezdxf.readfile(dxf_path)
        doc.saveas(out_dxf)
        return {"engine": "mark_spare_wires", "added_rectangles": 0,
                "note": "pass-through; spare marking not yet implemented",
                "output_dxf": out_dxf}
