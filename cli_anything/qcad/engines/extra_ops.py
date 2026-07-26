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
    """Mark clouded wire runs as spare by adding SPARE text at both terminal ends.

    PDF markup instruction 'mark spare on both ends' means: for each wire
    that passes through the clouded region, add 'SPARE' text adjacent to
    the wire's terminal label at both the left and right terminal blocks.

    Drawing topology (validated against real instrument loop drawings):

    - Wires are horizontal 2-vertex POLYLINEs (or LINEs) at a fixed Y.
    - Terminal blocks are 5-vertex POLYLINE rectangles (left+right+top+bottom+close).
      Left block:  X 8.37-9.17   Right block: X 13.93-14.73
    - Wire labels (F176, A233, etc.) are TEXT entities inside terminal boxes,
      offset slightly below the wire Y.
    - The cloud polygon marks which wire(s) are spare.

    Algorithm:

    1. Extract the cloud polygon from task parameters (region/regions).
    2. Find all horizontal wires (2-vert POLYLINEs, LINEs) whose Y falls
       within the cloud's Y-bbox AND whose X-span crosses the cloud interior.
    3. For each wire, find the terminal box it enters on each end (by
       checking which 5-vert POLYLINE rectangle's X-range contains the
       wire's left/right endpoint X).
    4. For each terminal box, find the TEXT entity inside that box closest
       to the wire's Y coordinate — that's the wire's terminal label.
    5. Add 'SPARE' TEXT adjacent to each terminal label (offset to the left
       of the label, matching the label's text height and style).

    If no wires are found in the cloud, fall back to adding SPARE text next
    to any TEXT entity inside the cloud polygon.
    """

    _SPARE_TEXT = "SPARE"
    # X-offset from terminal label to SPARE text (to the left of the label)
    _SPARE_OFFSET_X = 0.5
    # Y match tolerance for wire-to-label association (drawing units)
    _WIRE_Y_TOLERANCE = 0.3
    # Max distance from wire endpoint to terminal box edge
    _BOX_PROXIMITY = 1.0

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        regions = _normalize_regions(parameters)
        if not regions:
            doc.saveas(out_dxf)
            return {"engine": "mark_spare_wires", "success": False,
                    "error": "no cloud region provided"}

        added_labels: List[Dict[str, Any]] = []
        total_wires = 0

        for region in regions:
            cloud_verts = region.get("verts") or []
            cloud_bbox = region.get("bbox")

            if cloud_verts and len(cloud_verts) >= 3:
                verts = cloud_verts
            elif cloud_bbox:
                x0, x1, y0, y1 = cloud_bbox
                verts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            else:
                continue

            cloud_ys = [v[1] for v in verts]
            cloud_ymin, cloud_ymax = min(cloud_ys), max(cloud_ys)
            cloud_xs = [v[0] for v in verts]
            cloud_xmin, cloud_xmax = min(cloud_xs), max(cloud_xs)
            region_width = cloud_xmax - cloud_xmin
            region_height = cloud_ymax - cloud_ymin

            # Step 1: Find horizontal wires crossing the cloud
            wires = self._find_wires_in_cloud(msp, cloud_xmin, cloud_xmax,
                                              cloud_ymin, cloud_ymax)

            # Step 2: Find terminal box rectangles
            terminal_boxes = self._find_terminal_boxes(msp)

            # Callout-arrow fallback: if no wires found and the region is
            # small (callout arrow, not a cloud strip), use the arrow tip
            # as a point probe to find the nearest wire, then trace it to
            # both terminal blocks.  The arrow tip is the last vertex in
            # the callout's vertex list (text box → arrow start → arrow tip).
            if not wires and region_width < 2.0 and len(verts) >= 3:
                tip_x, tip_y = verts[-1]
                wires = self._find_wire_at_point(
                    msp, tip_x, tip_y, search_radius=1.5)

            total_wires += len(wires)

            if wires and terminal_boxes:
                # Wire-based approach: add SPARE at both ends of each wire
                for wire_y, wire_xmin, wire_xmax in wires:
                    left_box = self._find_box_for_endpoint(
                        terminal_boxes, wire_xmin, wire_y, side="left")
                    right_box = self._find_box_for_endpoint(
                        terminal_boxes, wire_xmax, wire_y, side="right")

                    for box, side in [(left_box, "left"), (right_box, "right")]:
                        if not box:
                            continue
                        label_ent = self._find_label_in_box(
                            msp, box, wire_y)
                        if label_ent:
                            spare_pos = self._compute_spare_position(
                                label_ent, side)
                            self._add_spare_text(
                                msp, spare_pos, label_ent)
                            added_labels.append({
                                "wire_y": round(wire_y, 3),
                                "side": side,
                                "label": label_ent.dxf.text,
                                "position": [round(spare_pos[0], 3),
                                             round(spare_pos[1], 3)],
                            })
            else:
                # Fallback: add SPARE next to any text inside the cloud
                for ent in msp:
                    if ent.dxftype() not in ("TEXT", "MTEXT"):
                        continue
                    x = ent.dxf.insert.x
                    y = ent.dxf.insert.y
                    if (cloud_xmin <= x <= cloud_xmax and
                            cloud_ymin <= y <= cloud_ymax):
                        spare_pos = self._compute_spare_position(ent, "left")
                        self._add_spare_text(msp, spare_pos, ent)
                        added_labels.append({
                            "fallback": True,
                            "label": ent.dxf.text,
                            "position": [round(spare_pos[0], 3),
                                         round(spare_pos[1], 3)],
                        })

        doc.saveas(out_dxf)
        return {
            "engine": "mark_spare_wires",
            "success": True,
            "added_labels": added_labels,
            "num_wires_found": total_wires,
            "output_dxf": out_dxf,
        }

    def _find_wires_in_cloud(
        self, msp, cloud_xmin: float, cloud_xmax: float,
        cloud_ymin: float, cloud_ymax: float
    ) -> List[Tuple[float, float, float]]:
        """Find horizontal wires crossing the cloud region.

        Returns list of (y, xmin, xmax) for each wire.
        """
        wires: List[Tuple[float, float, float]] = []
        seen_ys = set()

        for ent in msp:
            t = ent.dxftype()
            if t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y)
                       for v in ent.vertices]
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in ent.get_points("xy")]
            elif t == "LINE":
                pts = [(ent.dxf.start.x, ent.dxf.start.y),
                       (ent.dxf.end.x, ent.dxf.end.y)]
            else:
                continue

            if len(pts) != 2:
                continue

            (x1, y1), (x2, y2) = pts
            # Must be horizontal (same Y)
            if abs(y1 - y2) > 0.01:
                continue

            wy = (y1 + y2) / 2
            wxmin, wxmax = min(x1, x2), max(x1, x2)

            # Wire Y must be within cloud Y-bbox
            if not (cloud_ymin - 0.2 <= wy <= cloud_ymax + 0.2):
                continue

            # Wire must cross the cloud interior (span at least 50% of cloud width)
            if wxmax < cloud_xmin or wxmin > cloud_xmax:
                continue
            # Skip very short segments (terminal-internal connections < 1 unit)
            if wxmax - wxmin < 1.0:
                continue

            # Deduplicate by Y (wires have multiple segments at same Y)
            wy_key = round(wy, 2)
            if wy_key in seen_ys:
                # Merge segments: extend the existing wire's X range
                for i, (sy, sxmin, sxmax) in enumerate(wires):
                    if round(sy, 2) == wy_key:
                        wires[i] = (sy, min(sxmin, wxmin), max(sxmax, wxmax))
                        break
            else:
                wires.append((wy, wxmin, wxmax))
                seen_ys.add(wy_key)

        return wires

    def _find_wire_at_point(
        self, msp, tip_x: float, tip_y: float,
        search_radius: float = 1.5,
    ) -> List[Tuple[float, float, float]]:
        """Find the nearest horizontal wire to a callout arrow tip point.

        Used when the annotation is a callout arrow (not a cloud strip).
        Scans all horizontal wires within search_radius DXF units of the
        tip point and returns the closest one as (y, xmin, xmax), with
        the X range extended to cover all segments at that Y.

        Returns a list with 0 or 1 entries.
        """
        best_y = None
        best_dist = float("inf")

        for ent in msp:
            t = ent.dxftype()
            if t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y)
                       for v in ent.vertices]
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in ent.get_points("xy")]
            elif t == "LINE":
                pts = [(ent.dxf.start.x, ent.dxf.start.y),
                       (ent.dxf.end.x, ent.dxf.end.y)]
            else:
                continue

            if len(pts) != 2:
                continue

            (x1, y1), (x2, y2) = pts
            if abs(y1 - y2) > 0.01:
                continue

            wy = (y1 + y2) / 2
            # Distance from tip to this wire segment
            # Vertical distance is primary; horizontal distance matters
            # only if the tip is outside the segment's X range
            dy = abs(wy - tip_y)
            wxmin, wxmax = min(x1, x2), max(x1, x2)
            if tip_x < wxmin:
                dx = wxmin - tip_x
            elif tip_x > wxmax:
                dx = tip_x - wxmax
            else:
                dx = 0.0
            dist = (dy * dy + dx * dx) ** 0.5

            if dist < search_radius and dist < best_dist:
                best_dist = dist
                best_y = wy

        if best_y is None:
            return []

        # Now collect ALL segments at this Y to get the full wire span
        all_xmins = []
        all_xmaxs = []
        for ent in msp:
            t = ent.dxftype()
            if t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y)
                       for v in ent.vertices]
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in ent.get_points("xy")]
            elif t == "LINE":
                pts = [(ent.dxf.start.x, ent.dxf.start.y),
                       (ent.dxf.end.x, ent.dxf.end.y)]
            else:
                continue

            if len(pts) != 2:
                continue

            (x1, y1), (x2, y2) = pts
            if abs(y1 - y2) > 0.01:
                continue
            wy = (y1 + y2) / 2
            if abs(wy - best_y) < 0.05:
                all_xmins.append(min(x1, x2))
                all_xmaxs.append(max(x1, x2))

        if not all_xmins:
            return []

        return [(best_y, min(all_xmins), max(all_xmaxs))]

    def _find_terminal_boxes(self, msp) -> List[Dict[str, Any]]:
        """Find terminal box rectangles (5-vertex closed POLYLINEs or closed LWPOLYLINEs).

        Returns list of dicts: {xmin, xmax, ymin, ymax, cx, cy, entity}
        """
        boxes: List[Dict[str, Any]] = []

        for ent in msp:
            t = ent.dxftype()
            if t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y)
                       for v in ent.vertices]
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in ent.get_points("xy")]
            else:
                continue

            # Terminal boxes are 5-vertex (4 corners + close) or 4-vertex closed
            if len(pts) not in (4, 5):
                continue

            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)

            # Must be roughly square (terminal boxes are small, < 1 unit per side)
            w = xmax - xmin
            h = ymax - ymin
            if w < 0.3 or w > 2.0 or h < 0.05 or h > 2.0:
                continue
            # Skip the outer drawing border (very large rectangles)
            if w > 10 and h > 5:
                continue

            boxes.append({
                "xmin": xmin, "xmax": xmax,
                "ymin": ymin, "ymax": ymax,
                "cx": (xmin + xmax) / 2,
                "cy": (ymin + ymax) / 2,
            })

        return boxes

    def _find_box_for_endpoint(
        self, boxes: List[Dict[str, Any]], wire_x: float,
        wire_y: float, side: str
    ) -> Optional[Dict[str, Any]]:
        """Find the terminal box that the wire endpoint connects to.

        For 'left' side: the box should be to the left of or at the wire start.
        For 'right' side: the box should be to the right of or at the wire end.
        """
        best = None
        best_dist = float("inf")

        for box in boxes:
            # The wire endpoint should be near the box's edge
            if side == "left":
                # Wire starts at left side; box should contain or be near wire_x
                if box["xmax"] < wire_x - self._BOX_PROXIMITY:
                    continue
                if wire_x < box["xmin"] - self._BOX_PROXIMITY:
                    continue
            else:
                if box["xmin"] > wire_x + self._BOX_PROXIMITY:
                    continue
                if wire_x > box["xmax"] + self._BOX_PROXIMITY:
                    continue

            # Wire Y should pass through the box
            if not (box["ymin"] - 0.1 <= wire_y <= box["ymax"] + 0.1):
                continue

            # Distance from wire endpoint to nearest box edge
            if side == "left":
                dist = abs(wire_x - box["xmax"])
            else:
                dist = abs(wire_x - box["xmin"])

            if dist < best_dist:
                best_dist = dist
                best = box

        return best

    def _find_label_in_box(self, msp, box: Dict[str, Any],
                           wire_y: float):
        """Find the TEXT entity inside a terminal box closest to the wire's Y.

        Returns the TEXT entity, or None.
        """
        best_ent = None
        best_dist = float("inf")

        for ent in msp:
            if ent.dxftype() not in ("TEXT", "MTEXT"):
                continue
            x = ent.dxf.insert.x
            y = ent.dxf.insert.y

            # Text must be inside the box
            if not (box["xmin"] - 0.1 <= x <= box["xmax"] + 0.1 and
                    box["ymin"] - 0.1 <= y <= box["ymax"] + 0.1):
                continue

            dist = abs(y - wire_y)
            if dist < best_dist:
                best_dist = dist
                best_ent = ent

        return best_ent

    def _compute_spare_position(self, label_ent, side: str) -> Tuple[float, float]:
        """Compute where to place SPARE text relative to the terminal label.

        For 'left' end: SPARE goes to the LEFT of the label.
        For 'right' end: SPARE goes to the RIGHT of the label.
        Y is aligned with the label's Y.
        """
        lx = label_ent.dxf.insert.x
        ly = label_ent.dxf.insert.y

        # Estimate label width for offset
        try:
            txt = label_ent.dxf.text if label_ent.dxftype() == "TEXT" else (label_ent.text or "")
            h = label_ent.dxf.height if label_ent.dxftype() == "TEXT" else 0.13
            label_width = max(len(txt), 1) * h * 0.7
        except Exception:
            label_width = 0.5

        if side == "left":
            x = lx - self._SPARE_OFFSET_X
        else:
            # Push right-side SPARE past the label with a consistent gap
            x = lx + label_width + 0.3

        return (x, ly)

    def _add_spare_text(self, msp, pos: Tuple[float, float],
                        ref_ent) -> None:
        """Add SPARE text entity matching the reference label's style."""
        try:
            h = ref_ent.dxf.height if ref_ent.dxftype() == "TEXT" else 0.13
            style = ref_ent.dxf.style if ref_ent.dxftype() == "TEXT" else "STANDARD"
        except Exception:
            h = 0.13
            style = "STANDARD"

        try:
            layer = ref_ent.dxf.layer
        except Exception:
            layer = "0"

        msp.add_text(
            self._SPARE_TEXT,
            dxfattribs={
                "insert": (pos[0], pos[1]),
                "height": h,
                "style": style,
                "layer": layer,
            },
        )