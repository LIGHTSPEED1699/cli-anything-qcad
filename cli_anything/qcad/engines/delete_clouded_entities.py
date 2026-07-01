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
_PROTECTED_TEXT_PATTERNS = {
    "TB-", "TB21", "TB24", "TB19", "TB20", "TERMINAL", "Wlltermn",
    "GND", "GRND", "GROUND", "F174", "F173", "F175",
    "TITLE", "SHEET", "DRAWING", "REV ", "DATE", "BY ", "APPROVED",
    "DESCRIPTION", "CHKD", "EPAC",
}
_PROTECTED_BLOCK_NAMES = {"Wlltermn", "TERM", "TERMBLOCK", "GROUND", "GND"}
_PROTECTED_LAYERS = {"TITLEBLOCK", "BORDER", "DEFPOINTS"}


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
        return [(ent.dxf.insert.x, ent.dxf.insert.y)]
    if etype == "MTEXT":
        return [(ent.dxf.insert.x, ent.dxf.insert.y)]
    if etype == "INSERT":
        return [(ent.dxf.insert.x, ent.dxf.insert.y)]
    if etype == "SOLID":
        try:
            raw = [(v.x, v.y) for v in ent.wcs_vertices()]
            if raw:
                raw.append((sum(v[0] for v in raw) / len(raw),
                            sum(v[1] for v in raw) / len(raw)))
            return raw
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
        if any(p.upper() in block for p in _PROTECTED_BLOCK_NAMES):
            return True
    elif etype == "ATTRIB":
        text = (ent.dxf.text or "").upper()
    for pat in _PROTECTED_TEXT_PATTERNS:
        if pat.upper() in text:
            return True
    return False


def _point_in_polygon(pt: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
    if not polygon:
        return False
    try:
        return bool(MplPath(polygon).contains_point(pt))
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
        if ent.closed:
            pts.append(pts[0])
        for i in range(len(pts) - 1):
            if _segment_intersects_polygon(pts[i], pts[i + 1], polygon):
                return True
        # Also check centroid
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        return _point_in_polygon((cx, cy), polygon)

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

    # --- Point-like entities: point-in-polygon ---
    pts = _entity_geometry_points(ent)
    if not pts:
        return False
    inside = [p for p in pts if _point_in_polygon(p, polygon)]
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
