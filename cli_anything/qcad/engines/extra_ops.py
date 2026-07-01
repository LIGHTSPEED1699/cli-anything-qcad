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


# ── Entity geometry helpers ──

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


def _compute_pitch(values: List[float]) -> Optional[float]:
    """Compute the median spacing between sorted unique values.

    Returns None if fewer than 3 unique values are available.
    """
    unique = sorted(set(round(v, 4) for v in values))
    if len(unique) < 3:
        return None
    spacings = [unique[i + 1] - unique[i] for i in range(len(unique) - 1)]
    spacings.sort()
    median = spacings[len(spacings) // 2]
    return median if median > 0.01 else None


class ResizeBoundingBoxEngine:
    """Trim a rectangular box to preserve original design margins after deletion.

    Standard pitch-based method (no trial-and-error):

    1. Find all INSERT (block reference) entities inside the box — these are
       the primary structural content (terminal strips, contacts, etc.).

    2. Compute the **pitch** — the median spacing between adjacent INSERT
       positions along each axis.  In a well-drawn schematic, terminals are
       placed at regular intervals and the box margin equals half the pitch.

    3. **Design margin** = half the pitch.  This is the standard CAD
       convention: each terminal has half a slot of clearance to the box edge.

    4. For each edge, trim inward only if the current gap exceeds
       ``margin + pitch`` — i.e. at least one full terminal slot has been
       emptied by deletion.  This prevents trimming edges where the gap is
       merely asymmetric (e.g. a wider left margin for an off-center component).

    5. When trimmed, the new edge = nearest entity ± design margin, preserving
       the same clearance the box originally had.

    Fallback: if fewer than 3 INSERTs are found (insufficient to compute
    pitch), use all entity points and the min-gap method with a 2x ratio
    threshold.
    """

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        label = parameters.get("label") or parameters.get("target_description", "")
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
        box_miny, box_maxy = min(ys), max(ys)

        msp = doc.modelspace()

        # ── Collect entities inside the box ──
        # Separate INSERT blocks (primary structural content) from all entities.
        # Group INSERTs by block name so pitch can be computed per block type.
        insert_by_type: Dict[str, List[Tuple[float, float]]] = {}
        all_points = []
        for ent in msp:
            if ent.dxftype() == "LWPOLYLINE" and ent is box:
                continue
            if ent.dxftype() == "LWPOLYLINE" and ent.closed:
                continue
            for px, py in _entity_points(ent):
                # Strict containment with small tolerance for entities
                # sitting exactly on the edge.
                if box_minx - 0.05 <= px <= box_maxx + 0.05 and \
                   box_miny - 0.05 <= py <= box_maxy + 0.05:
                    all_points.append((px, py))
                    if ent.dxftype() == "INSERT":
                        insert_by_type.setdefault(ent.dxf.name, []).append((px, py))

        if not all_points:
            doc.saveas(out_dxf)
            return {"engine": "resize_bounding_box", "success": False,
                    "error": "no entities found inside box"}

        ent_min_x = min(p[0] for p in all_points)
        ent_max_x = max(p[0] for p in all_points)
        ent_min_y = min(p[1] for p in all_points)
        ent_max_y = max(p[1] for p in all_points)

        # ── Compute pitch along each axis ──
        # Use the most frequent INSERT block type (typically terminal strips)
        # to compute the terminal pitch.  Mixing different block types
        # (terminals, contacts, coils) produces a bogus median spacing.
        pitch_x = None
        pitch_y = None
        if insert_by_type:
            # Pick the block type with the most instances
            dominant_type = max(insert_by_type, key=lambda k: len(insert_by_type[k]))
            dom_pts = insert_by_type[dominant_type]
            if len(dom_pts) >= 3:
                pitch_x = _compute_pitch([p[0] for p in dom_pts])
                pitch_y = _compute_pitch([p[1] for p in dom_pts])

        # ── Determine design margins ──
        if pitch_y is not None:
            margin_y = pitch_y / 2.0
        else:
            # Fallback: min-gap method (clamp negatives to 0)
            gap_top = max(box_maxy - ent_max_y, 0)
            gap_bottom = max(ent_min_y - box_miny, 0)
            margin_y = min(gap_top, gap_bottom)

        if pitch_x is not None:
            margin_x = pitch_x / 2.0
        else:
            gap_left = max(ent_min_x - box_minx, 0)
            gap_right = max(box_maxx - ent_max_x, 0)
            margin_x = min(gap_left, gap_right)

        # ── Compute new edges ──
        # Trim an edge only if the gap exceeds margin + pitch (at least one
        # full slot was emptied).  This prevents trimming asymmetric but
        # originally-intentional margins.
        new_minx = box_minx
        new_maxx = box_maxx
        new_miny = box_miny
        new_maxy = box_maxy

        gap_left = ent_min_x - box_minx
        gap_right = box_maxx - ent_max_x
        gap_bottom = ent_min_y - box_miny
        gap_top = box_maxy - ent_max_y

        threshold_y = margin_y + (pitch_y or margin_y)
        threshold_x = margin_x + (pitch_x or margin_x)

        if gap_bottom > threshold_y:
            new_miny = ent_min_y - margin_y
        if gap_top > threshold_y:
            new_maxy = ent_max_y + margin_y
        if gap_left > threshold_x:
            new_minx = ent_min_x - margin_x
        if gap_right > threshold_x:
            new_maxx = ent_max_x + margin_x

        # ── Apply new bounds ──
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
            "pitch_x": pitch_x,
            "pitch_y": pitch_y,
            "design_margin_x": margin_x,
            "design_margin_y": margin_y,
            "gaps": {"left": gap_left, "right": gap_right,
                     "bottom": gap_bottom, "top": gap_top},
            "thresholds": {"x": threshold_x, "y": threshold_y},
            "edges_moved": {
                "left": gap_left > threshold_x,
                "right": gap_right > threshold_x,
                "bottom": gap_bottom > threshold_y,
                "top": gap_top > threshold_y,
            },
            "output_dxf": out_dxf,
        }


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