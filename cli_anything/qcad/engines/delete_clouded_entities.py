"""Delete DXF entities whose geometry lies inside PDF cloud polygons.

This engine replaces the old text-based "clear matched TEXT to '.'" approach with
geometry-based deletion: any entity (TEXT, MTEXT, LINE, LWPOLYLINE, ARC, CIRCLE,
INSERT, SOLID) that is substantially inside a cloud region is removed.

Exclusion/preservation rules:
- Terminal blocks / ground-reference symbols are never deleted.
- Drawing border/title-block text is preserved.
- Handles can be protected via a restore list.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import math

try:
    import ezdxf
except ImportError as e:  # pragma: no cover
    raise ImportError("ezdxf is required") from e

try:
    from matplotlib.path import Path as MplPath
except ImportError as e:  # pragma: no cover
    raise ImportError("matplotlib is required") from e


# Terminal/ground/title patterns to preserve by text or block name.
# Only include generic title-block and ground patterns, not pair-specific
# labels like F175 or TB24.
_PROTECTED_TEXT_PATTERNS = {
    "TITLE", "SHEET", "DRAWING", "REV ", "DATE", "BY ", "APPROVED BY",
    "DESCRIPTION", "CHKD", "EPAC", "PLAINS MIDSTREAM",
}
# Use exact block name matching (not substring) to avoid false positives
# like "WLTERM1" matching "TERM".
_PROTECTED_BLOCK_NAMES = {"Wlltermn", "GROUND", "GND"}
_PROTECTED_LAYERS = {"TITLEBLOCK", "BORDER", "DEFPOINTS"}


def _text_geometry_points(ent) -> List[Tuple[float, float]]:
    """Return points sampling the bounding box of a TEXT or MTEXT entity.

    Uses the text height, rotation, and an approximate width based on
    character count to compute the 4 corners + center of the text.
    This catches text whose insert point is outside a cloud polygon
    but whose visible glyphs overlap it.
    """
    etype = ent.dxftype()
    ip = ent.dxf.insert
    x, y = ip.x, ip.y
    height = getattr(ent.dxf, "height", None) or 0.1
    if height <= 0:
        height = 0.1

    # Estimate text width: ~0.6 * height per character for typical CAD fonts
    if etype == "TEXT":
        text = ent.dxf.text or ""
    else:  # MTEXT
        text = ent.text or ""
    # Strip MTEXT formatting codes
    import re
    text = re.sub(r"\\[A-Za-z][^;]*;", "", text)
    text = re.sub(r"[{}]", "", text)
    nchars = max(len(text), 1)
    width = nchars * height * 0.6

    rot = math.radians(getattr(ent.dxf, "rotation", 0.0) or 0.0)
    cos_r, sin_r = math.cos(rot), math.sin(rot)

    # Compute corners of the text bounding box relative to insert point
    # TEXT default alignment is left-justified, bottom-baseline
    # Corners: (0,0), (width,0), (width,height), (0,height)
    corners_local = [
        (0, 0),
        (width, 0),
        (width, height),
        (0, height),
    ]
    # Also add midpoints of edges and center for better coverage
    corners_local.extend([
        (width / 2, 0),
        (width, height / 2),
        (width / 2, height),
        (0, height / 2),
        (width / 2, height / 2),
    ])

    # For MTEXT with align_point, use it as the second anchor
    points = []
    for lx, ly in corners_local:
        rx = lx * cos_r - ly * sin_r + x
        ry = lx * sin_r + ly * cos_r + y
        points.append((rx, ry))

    # Also add the raw insert point
    points.append((x, y))

    # For MTEXT, also add the align_point if present
    if etype == "MTEXT":
        try:
            ap = ent.dxf.align_point
            if ap:
                points.append((ap.x, ap.y))
        except Exception:
            pass

    return points


def _entity_geometry_points(ent) -> List[Tuple[float, float]]:
    """Sample points on an entity for inside-polygon testing."""
    etype = ent.dxftype()
    if etype == "LINE":
        s = ent.dxf.start
        e = ent.dxf.end
        return [(s.x, s.y), (e.x, e.y), ((s.x + e.x) / 2, (s.y + e.y) / 2)]
    if etype == "LWPOLYLINE":
        pts = [(p[0], p[1]) for p in ent.get_points("xy")]
        if ent.closed:
            pts.append(pts[0])
        extras = []
        for i in range(len(pts) - 1):
            p1, p2 = pts[i], pts[i + 1]
            extras.append(((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2))
        if pts:
            extras.append((sum(p[0] for p in pts) / len(pts),
                           sum(p[1] for p in pts) / len(pts)))
        return pts + extras
    if etype == "ARC":
        cx, cy = ent.dxf.center.x, ent.dxf.center.y
        r = ent.dxf.radius
        sa = math.radians(ent.dxf.start_angle)
        ea = math.radians(ent.dxf.end_angle)
        pts = [(cx + r * math.cos(a), cy + r * math.sin(a))
               for a in [sa + i * (ea - sa) / 16 for i in range(17)]]
        pts.append((cx, cy))
        return pts
    if etype == "CIRCLE":
        cx, cy = ent.dxf.center.x, ent.dxf.center.y
        r = ent.dxf.radius
        return [(cx + r * math.cos(a), cy + r * math.sin(a))
                for a in [i * math.pi / 8 for i in range(16)]] + [(cx, cy)]
    if etype == "TEXT":
        return _text_geometry_points(ent)
    if etype == "MTEXT":
        return _text_geometry_points(ent)
    if etype == "INSERT":
        # Return insert point plus sampled points from block sub-entity geometry
        # (scaled and translated to world coordinates).  This catches terminal
        # symbols whose insert point is outside the cloud but whose visible
        # geometry (circle, lines) overlaps it.
        pts = [(ent.dxf.insert.x, ent.dxf.insert.y)]
        try:
            sx = ent.dxf.xscale or 1.0
            sy = ent.dxf.yscale or 1.0
            blk = ent.block()
            if blk is not None:
                for be in blk:
                    if be.dxftype() == "LINE":
                        s, e2 = be.dxf.start, be.dxf.end
                        pts.append((ent.dxf.insert.x + s.x * sx, ent.dxf.insert.y + s.y * sy))
                        pts.append((ent.dxf.insert.x + e2.x * sx, ent.dxf.insert.y + e2.y * sy))
                        pts.append((ent.dxf.insert.x + (s.x + e2.x) / 2 * sx,
                                    ent.dxf.insert.y + (s.y + e2.y) / 2 * sy))
                    elif be.dxftype() == "CIRCLE":
                        cx, cy, r = be.dxf.center.x, be.dxf.center.y, be.dxf.radius
                        for a_idx in range(8):
                            a = a_idx * math.pi / 4
                            pts.append((ent.dxf.insert.x + (cx + r * math.cos(a)) * sx,
                                        ent.dxf.insert.y + (cy + r * math.sin(a)) * sy))
                    elif be.dxftype() == "ARC":
                        cx, cy, r = be.dxf.center.x, be.dxf.center.y, be.dxf.radius
                        sa = math.radians(be.dxf.start_angle)
                        ea = math.radians(be.dxf.end_angle)
                        for i in range(8):
                            a = sa + i * (ea - sa) / 7
                            pts.append((ent.dxf.insert.x + (cx + r * math.cos(a)) * sx,
                                        ent.dxf.insert.y + (cy + r * math.sin(a)) * sy))
                    elif be.dxftype() == "LWPOLYLINE":
                        for p in be.get_points("xy"):
                            pts.append((ent.dxf.insert.x + p[0] * sx,
                                        ent.dxf.insert.y + p[1] * sy))
        except Exception:
            pass
        return pts
    if etype == "SOLID":
        try:
            raw = [(v.x, v.y) for v in ent.wcs_vertices()]
            if raw:
                raw.append((sum(v[0] for v in raw) / len(raw),
                            sum(v[1] for v in raw) / len(raw)))
            return raw
        except Exception:
            return []
    if etype == "LEADER":
        try:
            return [(v[0], v[1]) for v in ent.vertices]
        except Exception:
            return []
    if etype == "DIMENSION":
        try:
            return [(ent.dxf.text_midpoint.x, ent.dxf.text_midpoint.y)]
        except Exception:
            return []
    # Fallback: any point-like attribute
    try:
        return [(ent.dxf.insert.x, ent.dxf.insert.y)]
    except Exception:
        return []


def _entity_is_protected(ent) -> bool:
    """Return True if entity text or block name indicates it should be preserved."""
    etype = ent.dxftype()
    layer = getattr(ent.dxf, "layer", "").upper()
    if any(p.upper() in layer for p in _PROTECTED_LAYERS):
        return True
    text = ""
    if etype == "TEXT":
        text = (ent.dxf.text or "").upper()
    elif etype == "MTEXT":
        text = (ent.text or "").upper()
    elif etype == "INSERT":
        block = (ent.dxf.name or "").upper()
        if any(p.upper() == block for p in _PROTECTED_BLOCK_NAMES):
            return True
    elif etype == "ATTRIB":
        text = (ent.dxf.text or "").upper()
    for pat in _PROTECTED_TEXT_PATTERNS:
        if pat.upper() in text:
            return True
    return False


def _point_in_polygon(pt: Tuple[float, float], polygon: List[Tuple[float, float]],
                       radius: float = 0.0) -> bool:
    if not polygon:
        return False
    try:
        return bool(MplPath(polygon).contains_point(pt, radius=radius))
    except Exception:
        return False


def _segments_intersect(p1, p2, p3, p4) -> bool:
    """Return True if segment p1-p2 properly crosses segment p3-p4."""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1 = cross(p3, p4, p1)
    d2 = cross(p3, p4, p2)
    d3 = cross(p1, p2, p3)
    d4 = cross(p1, p2, p4)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    # Collinear / on-segment cases omitted for simplicity; the point-in-polygon
    # check already catches entities with endpoints inside the polygon.
    return False


def _segment_intersects_polygon(p1, p2, polygon) -> bool:
    """True if segment p1-p2 crosses any edge of the polygon."""
    # Fast check: if either endpoint is inside, it intersects
    if _point_in_polygon(p1, polygon) or _point_in_polygon(p2, polygon):
        return True
    for i in range(len(polygon)):
        p3 = polygon[i]
        p4 = polygon[(i + 1) % len(polygon)]
        if _segments_intersect(p1, p2, p3, p4):
            return True
    return False


def _entity_inside_polygon(ent, polygon: List[Tuple[float, float]],
                           min_points_inside: int = 1,
                           require_all_endpoints: bool = False) -> bool:
    """Test whether an entity is inside a cloud polygon.

    Uses three strategies depending on entity type:
    1. Point-like entities (TEXT, MTEXT, INSERT): point-in-polygon.
    2. Line-segment entities (LINE, LWPOLYLINE, ARC, CIRCLE): segment-polygon
       intersection — catches wires that cross through the cloud without
       having endpoints inside.
    3. Area entities (SOLID, HATCH): centroid + vertex point-in-polygon.
    """
    etype = ent.dxftype()

    # --- Segment-based entities: use segment-polygon intersection ---
    if etype == "LINE":
        s = ent.dxf.start
        e = ent.dxf.end
        return _segment_intersects_polygon((s.x, s.y), (e.x, e.y), polygon)

    if etype == "LWPOLYLINE":
        pts = [(p[0], p[1]) for p in ent.get_points("xy")]
        if not pts:
            return False
        # Compute centroid (of unique points, excluding closing duplicate)
        raw_pts = pts[:-1] if ent.closed and pts[0] == pts[-1] else pts
        if not raw_pts:
            raw_pts = pts
        cx = sum(p[0] for p in raw_pts) / len(raw_pts)
        cy = sum(p[1] for p in raw_pts) / len(raw_pts)
        centroid_inside = _point_in_polygon((cx, cy), polygon)

        if ent.closed:
            # For closed polylines (boxes, rectangles, borders): require the
            # centroid to be inside the cloud.  This prevents large containers
            # like the RELAY 15 box that merely cross through a cloud from
            # being deleted — only entities whose center is inside the cloud
            # are considered "inside".
            if centroid_inside:
                return True
            # If centroid is outside, don't delete even if segments cross.
            return False

        # Open polylines (wires): use segment intersection to catch wires
        # crossing through the cloud.
        pts.append(pts[0]) if False else None  # keep open
        for i in range(len(pts) - 1):
            if _segment_intersects_polygon(pts[i], pts[i + 1], polygon):
                return True
        return centroid_inside

    if etype == "ARC":
        cx, cy = ent.dxf.center.x, ent.dxf.center.y
        r = ent.dxf.radius
        sa = math.radians(ent.dxf.start_angle)
        ea = math.radians(ent.dxf.end_angle)
        pts = [(cx + r * math.cos(a), cy + r * math.sin(a))
               for a in [sa + i * (ea - sa) / 16 for i in range(17)]]
        for i in range(len(pts) - 1):
            if _segment_intersects_polygon(pts[i], pts[i + 1], polygon):
                return True
        return _point_in_polygon((cx, cy), polygon)

    if etype == "CIRCLE":
        cx, cy = ent.dxf.center.x, ent.dxf.center.y
        r = ent.dxf.radius
        pts = [(cx + r * math.cos(a), cy + r * math.sin(a))
               for a in [i * math.pi / 8 for i in range(16)]]
        for i in range(len(pts) - 1):
            if _segment_intersects_polygon(pts[i], pts[i + 1], polygon):
                return True
        return _point_in_polygon((cx, cy), polygon)

    if etype == "LEADER":
        # LEADER entities have vertices (arrowhead + leader line points).
        # The arrow tip is usually outside the cloud, but the text-end
        # vertices may be right on the cloud boundary.  Use a small
        # radius tolerance so leaders whose text labels are inside the
        # cloud (and thus deleted) are also deleted.
        try:
            pts = [(v[0], v[1]) for v in ent.vertices]
        except Exception:
            return False
        if not pts:
            return False
        # Check vertices inside polygon (with tolerance for boundary cases)
        for p in pts:
            if _point_in_polygon(p, polygon):
                return True
            if _point_in_polygon(p, polygon, radius=0.2):
                return True
        # Check segment crossings
        for i in range(len(pts) - 1):
            if _segment_intersects_polygon(pts[i], pts[i + 1], polygon):
                return True
        return False

    if etype == "ELLIPSE":
        cx, cy = ent.dxf.center.x, ent.dxf.center.y
        rx = ent.dxf.major_axis[0]
        ry = ent.dxf.major_axis[1]
        pts = [(cx + rx * math.cos(a), cy + ry * math.sin(a))
               for a in [i * math.pi / 8 for i in range(16)]]
        for i in range(len(pts) - 1):
            if _segment_intersects_polygon(pts[i], pts[i + 1], polygon):
                return True
        return _point_in_polygon((cx, cy), polygon)

    if etype == "HATCH":
        # HATCH entities use EdgePath or PolylinePath boundary paths.
        # Extract points from edges and check if any are inside the polygon.
        try:
            for path in ent.paths:
                # PolylinePath has .vertices
                if hasattr(path, "vertices"):
                    for v in path.vertices:
                        if _point_in_polygon((v[0], v[1]), polygon):
                            return True
                # EdgePath has .edges
                elif hasattr(path, "edges"):
                    for edge in path.edges:
                        if hasattr(edge, "start") and _point_in_polygon(
                            (edge.start[0], edge.start[1]), polygon):
                            return True
                        if hasattr(edge, "end") and _point_in_polygon(
                            (edge.end[0], edge.end[1]), polygon):
                            return True
                        if hasattr(edge, "center") and _point_in_polygon(
                            (edge.center[0], edge.center[1]), polygon):
                            return True
        except Exception:
            pass
        return False

    # --- Point-like entities: point-in-polygon with boundary tolerance ---
    # Use a small negative radius to slightly expand the polygon boundary,
    # catching entities that visually overlap the cloud but whose geometry
    # points fall just outside due to PDF→DXF calibration imprecision
    # (typically < 0.1 units).  matplotlib's radius is a shrink factor:
    # positive values make the test stricter, negative values expand.
    pts = _entity_geometry_points(ent)
    if not pts:
        return False
    inside = [p for p in pts if _point_in_polygon(p, polygon, radius=-0.1)]
    if require_all_endpoints:
        return len(inside) == len(pts) and len(pts) > 0
    return len(inside) >= min_points_inside


class DeleteCloudedEntitiesEngine:
    """Delete all DXF entities inside PDF cloud polygons, with protection rules."""

    def __init__(self, restore_handles: Optional[Set[str]] = None,
                 protected_text_patterns: Optional[Set[str]] = None,
                 protected_block_names: Optional[Set[str]] = None):
        self.restore_handles = set(h.upper() for h in (restore_handles or []))
        if protected_text_patterns:
            _PROTECTED_TEXT_PATTERNS.update(protected_text_patterns)
        if protected_block_names:
            _PROTECTED_BLOCK_NAMES.update(protected_block_names)

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        regions = parameters.get("regions", [])
        if isinstance(regions, dict):
            regions = [regions]
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        # First pass: collect protected entity handles (including block sub-entities)
        protected_handles: Set[str] = set()
        for ent in msp:
            if _entity_is_protected(ent):
                protected_handles.add(ent.dxf.handle)
                # For INSERTs, also protect the block definition's text entities visually
                if ent.dxftype() == "INSERT":
                    try:
                        block = doc.blocks.get(ent.dxf.name)
                        for be in block:
                            protected_handles.add(be.dxf.handle)
                    except Exception:
                        pass
        # Always protect restore handles
        protected_handles.update(self.restore_handles)

        deleted_handles: List[str] = []
        for region in regions:
            verts = region.get("verts", [])
            if len(verts) < 3:
                continue
            for ent in list(msp):
                h = ent.dxf.handle
                if h in protected_handles:
                    continue
                if _entity_inside_polygon(ent, verts):
                    try:
                        msp.delete_entity(ent)
                        deleted_handles.append(h)
                    except Exception:
                        pass

        # Restore any protected handles that were accidentally removed (via backup-reinsert)
        restored = self._restore_handles(doc, msp, deleted_handles, protected_handles)

        doc.saveas(out_dxf)
        return {
            "engine": "delete_clouded_entities",
            "regions": len(regions),
            "deleted_handles": deleted_handles,
            "restored_handles": restored,
            "output_dxf": out_dxf,
        }

    def _restore_handles(self, doc, msp, deleted_handles: List[str],
                         protected_handles: Set[str]) -> List[str]:
        """If a protected handle was deleted, re-insert from original DXF backup."""
        # This is a placeholder: real restore needs an original copy.
        # For now, we report which protected handles were in the deleted list.
        bad = [h for h in deleted_handles if h in protected_handles]
        return bad
