"""Resolve a CAD-space region to the entities inside it.

This is the primitive the chat portal needs: the user drags a box on a rendered
window, the browser sends back CAD coordinates, and this returns the exact entities
inside - with handles the agent can act on.

Handles are the DWG's own, and they do NOT survive an ODA DWG->DXF conversion (a DWG
handle of 888 came back as 378). So resolve by geometry, never by a handle that came
from a DXF.
"""
from typing import Any, Dict, List, Optional, Tuple


def point_in_rect(x: float, y: float, rect: Dict[str, float]) -> bool:
    """Is (x, y) inside the rect? rect = {x, y, w, h}, lower-left origin."""
    return (rect["x"] <= x <= rect["x"] + rect["w"]
            and rect["y"] <= y <= rect["y"] + rect["h"])


def point_in_polygon(x: float, y: float, verts: List[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test. verts is a list of (x, y)."""
    n = len(verts)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = verts[i]
        xj, yj = verts[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _bbox_tuple_to_rect(bbox) -> Optional[Dict[str, float]]:
    """The planner's region bbox is a tuple (xmin, xmax, ymin, ymax)."""
    if not bbox or len(bbox) != 4:
        return None
    xmin, xmax, ymin, ymax = bbox
    return {"x": min(xmin, xmax), "y": min(ymin, ymax),
            "w": abs(xmax - xmin), "h": abs(ymax - ymin)}


def region_polygon(region: Optional[Dict[str, Any]]) -> Optional[List[Tuple[float, float]]]:
    """Extract a polygon's vertices from a planner region, if it has any."""
    if not region:
        return None
    verts = region.get("verts")
    if verts:
        return [(float(v[0]), float(v[1])) for v in verts]
    return None


def region_rect(region: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Extract a rectangle from a planner region, whatever shape it uses."""
    if not region:
        return None
    if isinstance(region.get("bbox"), (list, tuple)):
        return _bbox_tuple_to_rect(region["bbox"])
    if all(k in region for k in ("x", "y", "w", "h")):
        return {k: float(region[k]) for k in ("x", "y", "w", "h")}
    return None


def _entity_points(row: Dict[str, Any]) -> List[Tuple[float, float]]:
    """All known CAD points of an indexed entity."""
    pts: List[Tuple[float, float]] = []
    if row.get("x") is not None and row.get("y") is not None:
        pts.append((row["x"], row["y"]))
    for a, b in (("x1", "y1"), ("x2", "y2")):
        if row.get(a) is not None and row.get(b) is not None:
            pts.append((row[a], row[b]))
    return pts


def entity_in_rect(row: Dict[str, Any], rect: Dict[str, float],
                   include_text: bool = True) -> bool:
    """Is the entity inside the rect?

    An entity counts as inside when any of its known points is inside. For text that
    is the insertion point; for a line it is either endpoint.
    """
    if not include_text and row.get("t"):
        return False
    for x, y in _entity_points(row):
        if point_in_rect(x, y, rect):
            return True
    return False


def entities_in_rect(rows: List[Dict[str, Any]], rect: Dict[str, float],
                     include_text: bool = True) -> List[Dict[str, Any]]:
    """Entities inside a CAD rectangle, each annotated with its handle and text."""
    out = []
    for row in rows:
        if entity_in_rect(row, rect, include_text):
            r = dict(row)
            r["in_rect"] = True
            out.append(r)
    return out


def entities_near_point(rows: List[Dict[str, Any]], x: float, y: float,
                         radius: float = 1.0) -> List[Dict[str, Any]]:
    """Entities within `radius` of a CAD point, nearest first."""
    hits = []
    for row in rows:
        best = None
        for px, py in _entity_points(row):
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if best is None or d < best:
                best = d
        if best is not None and best <= radius:
            r = dict(row)
            r["dist"] = round(best, 4)
            hits.append(r)
    hits.sort(key=lambda r: r["dist"])
    return hits


def entities_in_region(rows: List[Dict[str, Any]], region: Optional[Dict[str, Any]],
                       include_text: bool = True) -> List[Dict[str, Any]]:
    """Entities inside a planner region (polygon preferred, bbox as fallback).

    The planner produces {"type": "polygon", "verts": [...], "bbox": (xmin, xmax, ymin, ymax)}.
    Use the polygon when present: a cloud's bbox can cover a lot more than the cloud.
    """
    if not region:
        return []
    poly = region_polygon(region)
    rect = region_rect(region)
    out = []
    for row in rows:
        if not include_text and row.get("t"):
            continue
        pts = _entity_points(row)
        if not pts:
            continue
        hit = False
        if poly:
            hit = any(point_in_polygon(x, y, poly) for x, y in pts)
        if not hit and rect:
            hit = any(point_in_rect(x, y, rect) for x, y in pts)
        if hit:
            r = dict(row)
            r["in_region"] = True
            out.append(r)
    return out


def summarise(rows: List[Dict[str, Any]], limit: int = 40) -> Dict[str, Any]:
    """A compact, LLM-friendly description of a set of entities."""
    texts = [r["t"] for r in rows if r.get("t")]
    layers: Dict[str, int] = {}
    for r in rows:
        layers[r.get("layer", "?")] = layers.get(r.get("layer", "?"), 0) + 1
    return {
        "count": len(rows),
        "text_count": len(texts),
        "texts": texts[:limit],
        "layers": layers,
        "handles": [r.get("h") for r in rows[:limit]],
    }
