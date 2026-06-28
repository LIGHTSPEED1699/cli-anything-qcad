"""Geometry add/move engines: dimension, leader, move entity."""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import ezdxf
except ImportError as e:  # pragma: no cover
    raise ImportError("ezdxf is required for QCAD engines") from e


def _dxf_point(pt: Any) -> Tuple[float, float]:
    if isinstance(pt, (tuple, list)) and len(pt) >= 2:
        return (float(pt[0]), float(pt[1]))
    if hasattr(pt, "x"):
        return (float(pt.x), float(pt.y))
    raise ValueError(f"Cannot interpret point: {pt}")


class AddDimensionEngine:
    """Add a linear dimension between two points along a horizontal/vertical axis."""

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        p1 = _dxf_point(parameters.get("p1") or parameters.get("start_point"))
        p2 = _dxf_point(parameters.get("p2") or parameters.get("end_point"))
        axis = parameters.get("axis", "horizontal").lower()
        override = parameters.get("override_text")
        style = parameters.get("style", "Standard")

        # Dimension location: offset perpendicular to the measured line
        offset = float(parameters.get("offset", 5.0))
        if axis == "horizontal":
            dim_loc = ((p1[0] + p2[0]) / 2, max(p1[1], p2[1]) + offset)
        elif axis == "vertical":
            dim_loc = (max(p1[0], p2[0]) + offset, (p1[1] + p2[1]) / 2)
        else:
            dim_loc = ((p1[0] + p2[0]) / 2 + offset, (p1[1] + p2[1]) / 2 + offset)

        dim = msp.add_linear_dim(
            base=dim_loc,
            p1=p1,
            p2=p2,
            override=override,
            dxfattribs={"dimstyle": style},
        )
        # Required by ezdxf to finalize the dimension block
        dim.render()

        doc.saveas(out_dxf)
        return {
            "added_dimension_handle": dim.dxf.handle,
            "p1": p1, "p2": p2,
            "axis": axis,
        }


class AddLeaderEngine:
    """Add a simple leader (polyline) with text at the landing point."""

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        start = _dxf_point(parameters.get("start_point") or parameters.get("arrow_point"))
        end = _dxf_point(parameters.get("end_point") or parameters.get("landing_point"))
        text = parameters.get("text", "")
        text_height = float(parameters.get("text_height", 2.5))
        layer = parameters.get("layer", "0")

        # Leader as 2-point LWPOLYLINE with arrowhead at start
        msp.add_lwpolyline([start, end], dxfattribs={"layer": layer})

        if text:
            # Place text near the landing point, offset outward
            tx, ty = end[0] + text_height * 0.5, end[1]
            msp.add_text(text, dxfattribs={
                "insert": (tx, ty),
                "height": text_height,
                "layer": layer,
            })

        doc.saveas(out_dxf)
        return {"added_leader": True, "start": start, "end": end, "text": text}


class MoveEntityEngine:
    """Move an entity near a point by a translation vector."""

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        near_point = _dxf_point(parameters["near_point"])
        dx = float(parameters.get("dx", 0.0))
        dy = float(parameters.get("dy", 0.0))
        target_text = parameters.get("target_text")
        entity_type = parameters.get("entity_type")

        moved_handles: List[str] = []
        for ent in msp:
            match = False
            if target_text and ent.dxftype() == "TEXT":
                match = target_text in str(ent.dxf.text)
            elif target_text and ent.dxftype() == "MTEXT":
                match = target_text in str(ent.text)
            elif entity_type and ent.dxftype() == entity_type.upper():
                match = _entity_near_point(ent, near_point)
            else:
                match = _entity_near_point(ent, near_point)

            if match:
                _move_entity(ent, dx, dy)
                moved_handles.append(ent.dxf.handle)

        doc.saveas(out_dxf)
        return {"moved_handles": moved_handles, "dx": dx, "dy": dy}


def _entity_near_point(ent, point: Tuple[float, float], tol: float = 5.0) -> bool:
    """Rough proximity check using DXF extents."""
    try:
        e_type = ent.dxftype()
        if e_type in ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE"):
            pts = _sample_entity_points(ent)
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                return (min(xs) - tol <= point[0] <= max(xs) + tol and
                        min(ys) - tol <= point[1] <= max(ys) + tol)
        if e_type in ("TEXT", "MTEXT", "INSERT"):
            insert = getattr(ent.dxf, "insert", getattr(ent, "insert", None))
            if insert is not None:
                ix, iy = _dxf_point(insert)
                return abs(ix - point[0]) <= tol and abs(iy - point[1]) <= tol
    except Exception:
        pass
    return False


def _sample_entity_points(ent) -> List[Tuple[float, float]]:
    e_type = ent.dxftype()
    if e_type == "LINE":
        return [(ent.dxf.start.x, ent.dxf.start.y), (ent.dxf.end.x, ent.dxf.end.y)]
    if e_type == "LWPOLYLINE":
        return [(p[0], p[1]) for p in ent.get_points("xy")]
    if e_type == "ARC":
        return [(ent.dxf.center.x, ent.dxf.center.y)]
    if e_type == "CIRCLE":
        return [(ent.dxf.center.x, ent.dxf.center.y)]
    return []


def _move_entity(ent, dx: float, dy: float) -> None:
    e_type = ent.dxftype()
    if e_type == "LINE":
        ent.dxf.start = (ent.dxf.start.x + dx, ent.dxf.start.y + dy)
        ent.dxf.end = (ent.dxf.end.x + dx, ent.dxf.end.y + dy)
    elif e_type == "LWPOLYLINE":
        pts = [list(p) for p in ent.get_points("xy")]
        new_pts = [[p[0] + dx, p[1] + dy, *p[2:]] for p in pts]
        ent.set_points(new_pts)
    elif e_type in ("TEXT", "MTEXT"):
        ent.dxf.insert = (ent.dxf.insert.x + dx, ent.dxf.insert.y + dy)
    elif e_type == "INSERT":
        ent.dxf.insert = (ent.dxf.insert.x + dx, ent.dxf.insert.y + dy)
    elif e_type == "ARC":
        ent.dxf.center = (ent.dxf.center.x + dx, ent.dxf.center.y + dy)
    elif e_type == "CIRCLE":
        ent.dxf.center = (ent.dxf.center.x + dx, ent.dxf.center.y + dy)
    elif e_type == "DIMENSION":
        ent.dxf.text_midpoint = (ent.dxf.text_midpoint.x + dx, ent.dxf.text_midpoint.y + dy)
        # Block reference is moved separately by the dimension block transform
