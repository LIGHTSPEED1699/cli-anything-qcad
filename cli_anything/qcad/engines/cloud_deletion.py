"""Cloud annotation → entity deletion engine."""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from matplotlib.path import Path as MplPath

from cli_anything.qcad.engines.delete_by_handle import delete_handles
from cli_anything.qcad.utils.layer_fix import fix_layer_visibility


try:
    import fitz  # type: ignore
except ImportError as e:  # pragma: no cover
    raise ImportError("PyMuPDF required") from e


@dataclass
class Cloud:
    label: str
    side: str
    verts: List[Tuple[float, float]]
    bbox: Tuple[float, float, float, float]
    height: float


@dataclass
class EntityInfo:
    handle: str
    etype: str
    points: List[Tuple[float, float]]
    bbox: Optional[Tuple[float, float, float, float]]
    text: str = ""
    color: int = 0
    meta: Dict = field(default_factory=dict)


def extract_clouds(pdf_path: str, scale: float = 72.0) -> List[Cloud]:
    """Extract cloud Polygon annotations with swap_xy mapping."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    raw = []
    for annot in page.annots() or []:
        if annot.info.get("subject", "") != "Cloud":
            continue
        verts = annot.vertices
        dxf = [(v[1] / scale, v[0] / scale) for v in verts]
        xs = [p[0] for p in dxf]
        ys = [p[1] for p in dxf]
        cx = sum(xs) / len(xs)
        raw.append({
            "side": "LEFT" if cx < 7 else "RIGHT",
            "verts": dxf,
            "bbox": (min(xs), max(xs), min(ys), max(ys)),
            "height": max(ys) - min(ys),
        })
    doc.close()

    raw.sort(key=lambda c: (0 if c["side"] == "LEFT" else 1,
                            0 if c["bbox"][2] < 7 else 1))
    clouds = []
    for i, rc in enumerate(raw):
        vert_label = "TOP" if rc["bbox"][2] > 7 else "BOTTOM"
        clouds.append(Cloud(
            label=f"C{i} ({rc['side']}-{vert_label})",
            side=rc["side"], verts=rc["verts"],
            bbox=rc["bbox"], height=rc["height"],
        ))
    return clouds


def _build_entity_index(dxf_path: str) -> List[EntityInfo]:
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    entities: List[EntityInfo] = []
    for e in msp:
        try:
            etype = e.dxftype()
            handle = e.dxf.handle.upper()
            pts = []
            bb = None
            meta = {}
            text = ""
            color = getattr(e.dxf, "color", 0) or 0
            if etype in ("TEXT", "MTEXT"):
                x, y = e.dxf.insert.x, e.dxf.insert.y
                pts = [(x, y)]
                text = getattr(e.dxf, "text", getattr(e, "text", "")).strip()
            elif etype == "LINE":
                x1, y1 = e.dxf.start.x, e.dxf.start.y
                x2, y2 = e.dxf.end.x, e.dxf.end.y
                pts = [(x1, y1), (x2, y2), ((x1 + x2) / 2, (y1 + y2) / 2)]
                bb = (min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2))
            elif etype == "CIRCLE":
                cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
                pts = [(cx, cy), (cx + r, cy), (cx - r, cy), (cx, cy + r), (cx, cy - r)]
                bb = (cx - r, cx + r, cy - r, cy + r)
            elif etype == "ARC":
                cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
                pts = [(cx, cy), (cx + r, cy)]
                bb = (cx - r, cx + r, cy - r, cy + r)
            elif etype == "ELLIPSE":
                cx, cy = e.dxf.center.x, e.dxf.center.y
                pts = [(cx, cy)]
            elif etype == "POLYLINE":
                verts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if verts:
                    pts = list(verts)
                    pts.append((sum(v[0] for v in verts) / len(verts),
                                sum(v[1] for v in verts) / len(verts)))
                    xs = [p[0] for p in verts]; ys = [p[1] for p in verts]
                    bb = (min(xs), max(xs), min(ys), max(ys))
                    meta["n_vertices"] = len(verts)
                    meta["vertices"] = verts
            elif etype == "LWPOLYLINE":
                verts = list(e.get_points(format="xy"))
                if verts:
                    pts = list(verts)
                    pts.append((sum(v[0] for v in verts) / len(verts),
                                sum(v[1] for v in verts) / len(verts)))
                    xs = [p[0] for p in verts]; ys = [p[1] for p in verts]
                    bb = (min(xs), max(xs), min(ys), max(ys))
                    meta["n_vertices"] = len(verts)
            elif etype == "INSERT":
                x, y = e.dxf.insert.x, e.dxf.insert.y
                pts = [(x, y)]
                meta["name"] = getattr(e.dxf, "name", "")
            if pts:
                entities.append(EntityInfo(handle=handle, etype=etype, points=pts, bbox=bb,
                                           text=text, color=color, meta=meta))
        except Exception:
            continue
    return entities


def _bboxes_overlap(a: Tuple[float, ...], b: Tuple[float, ...]) -> bool:
    return a[0] <= b[1] and a[1] >= b[0] and a[2] <= b[3] and a[3] >= b[2]


def _point_in_bbox(x: float, y: float, bb: Tuple[float, ...]) -> bool:
    return bb[0] <= x <= bb[1] and bb[2] <= y <= bb[3]


def _match_entities(entities: List[EntityInfo], clouds: List[Cloud],
                    strict_margin: float = -0.08) -> Tuple[Set[str], Set[str], Dict]:
    deletion: Set[str] = set()
    boundary: Set[str] = set()
    stats = defaultdict(lambda: {"t1": 0, "t2": 0, "t3": 0, "t4": 0})
    for e in entities:
        if not e.points:
            continue
        for cloud in clouds:
            is_thin = cloud.height < 1.0
            done = False
            for pt in e.points:
                if MplPath(cloud.verts).contains_point(pt, radius=strict_margin):
                    deletion.add(e.handle)
                    stats[cloud.label]["t1"] += 1
                    done = True
                    break
            if done:
                break
            if e.bbox and (e.etype == "HATCH" or
                           (is_thin and e.etype in ("POLYLINE", "LINE", "LWPOLYLINE"))):
                if _bboxes_overlap(e.bbox, cloud.bbox):
                    deletion.add(e.handle)
                    stats[cloud.label]["t2"] += 1
                    break
            if is_thin and e.etype in ("TEXT", "MTEXT", "LINE", "POLYLINE", "LWPOLYLINE", "CIRCLE"):
                for pt in e.points:
                    if _point_in_bbox(pt[0], pt[1], cloud.bbox):
                        deletion.add(e.handle)
                        stats[cloud.label]["t3"] += 1
                        done = True
                        break
                if done:
                    break
            for pt in e.points:
                if MplPath(cloud.verts).contains_point(pt):
                    boundary.add(e.handle)
                    stats[cloud.label]["t4"] += 1
                    break
    return deletion, boundary, dict(stats)


def _content_sweep(entities: List[EntityInfo], deletion: Set[str],
                   clouds: List[Cloud]) -> Set[str]:
    deleted_words = set()
    for e in entities:
        if e.handle in deletion and e.text:
            for w in e.text.lower().split():
                if len(w) > 2:
                    deleted_words.add(w)
    additions = set()
    for e in entities:
        if e.handle in deletion or e.etype not in ("TEXT", "MTEXT") or not e.text:
            continue
        tx, ty = e.points[0]
        side = "LEFT" if tx < 7 else "RIGHT"
        for cloud in clouds:
            if cloud.side != side:
                continue
            cb = cloud.bbox
            if (cb[0] - 3 <= tx <= cb[1] + 3 and cb[2] - 3 <= ty <= cb[3] + 3):
                ewords = set(e.text.lower().split())
                if ewords & deleted_words:
                    additions.add(e.handle)
                    break
    return additions


def _preserve_label_boxes(entities: List[EntityInfo], deletion: Set[str]) -> Set[str]:
    kept_texts = [(e.points[0][0], e.points[0][1])
                  for e in entities
                  if e.handle not in deletion and e.etype in ("TEXT", "MTEXT")]
    preservations = set()
    for e in entities:
        if e.handle in deletion and e.meta.get("n_vertices") == 5:
            verts = e.meta.get("vertices", [])
            if len(verts) < 4:
                continue
            cx = sum(v[0] for v in verts[:4]) / 4
            cy = sum(v[1] for v in verts[:4]) / 4
            for tx, ty in kept_texts:
                if math.hypot(tx - cx, ty - cy) < 0.30:
                    preservations.add(e.handle)
                    break
    return preservations


def _preserve_ground_refs(entities: List[EntityInfo], deletion: Set[str],
                          kept_handles: Set[str]) -> Set[str]:
    kept_boxes = [e for e in entities if e.handle in kept_handles and e.meta.get("n_vertices") == 5]
    preservations = set()
    for e in entities:
        if e.handle not in deletion or e.meta.get("n_vertices") != 3:
            continue
        verts = e.meta.get("vertices", [])
        if len(verts) != 3:
            continue
        seg1 = math.hypot(verts[1][0] - verts[0][0], verts[1][1] - verts[0][1])
        seg2 = math.hypot(verts[2][0] - verts[1][0], verts[2][1] - verts[1][1])
        if seg1 + seg2 >= 1.0:
            continue
        v0x, v0y = verts[0]
        for box in kept_boxes:
            bverts = box.meta.get("vertices", [])
            if len(bverts) < 4:
                continue
            bx_max = max(v[0] for v in bverts[:4])
            by_min = min(v[1] for v in bverts[:4])
            by_max = max(v[1] for v in bverts[:4])
            if abs(v0x - bx_max) < 0.05 and by_min - 0.05 <= v0y <= by_max + 0.05:
                preservations.add(e.handle)
                break
    return preservations


class CloudDeletionEngine:
    """Delete entities enclosed in PDF cloud markups."""

    def run(self, dxf_path: str, pdf_path: str, out_dxf: str,
            overrides: Optional[Dict] = None) -> Dict:
        clouds = extract_clouds(pdf_path)
        entities = _build_entity_index(dxf_path)
        deletion, boundary, stats = _match_entities(entities, clouds)
        deletion |= _content_sweep(entities, deletion, clouds)
        preservations = _preserve_label_boxes(entities, deletion)
        kept = {e.handle for e in entities} - deletion
        preservations |= _preserve_ground_refs(entities, deletion, kept)
        deletion -= preservations

        restored = set(preservations)
        if overrides:
            for h in overrides.get("add", []):
                deletion.add(h.upper())
            for h in overrides.get("remove", []):
                deletion.discard(h.upper())
            for h in overrides.get("restore", []):
                deletion.discard(h.upper()); restored.add(h.upper())

        deleted_dxf = str(Path(out_dxf).with_suffix("")) + ".deleted.dxf"
        delete_handles(dxf_path, deleted_dxf, deletion)
        fix_layer_visibility(deleted_dxf, out_dxf)

        return {
            "success": True,
            "deleted_count": len(deletion),
            "deletion": sorted(deletion),
            "restored": sorted(restored),
            "boundary": sorted(boundary),
            "stats": stats,
            "clouds": [c.label for c in clouds],
            "dxf": out_dxf,
        }
