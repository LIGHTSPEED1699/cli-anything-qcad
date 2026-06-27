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


def extract_clouds(pdf_path: str, dxf_path: Optional[str] = None,
                    scale: float = 72.0) -> List[Cloud]:
    """Extract cloud Polygon annotations and map them into DXF coordinates.

    Uses an affine calibration derived from matching text labels in both the
    PDF and the DXF. Falls back to a rotation-only transform if calibration
    cannot be computed.
    """
    affine = _calibrate_dxf_affine(dxf_path, pdf_path) if dxf_path else None

    doc = fitz.open(pdf_path)
    page = doc[0]
    raw_w = page.mediabox.width
    raw_h = page.mediabox.height
    rotation = page.rotation

    def raw_to_dxf(v):
        raw_x, raw_y = float(v[0]), float(v[1])
        if affine is not None:
            # PyMuPDF annot.vertices are already in display/page space; map directly to DXF
            dx = affine[0, 0] * raw_x + affine[1, 0] * raw_y + affine[2, 0]
            dy = affine[0, 1] * raw_x + affine[1, 1] * raw_y + affine[2, 1]
            return (dx, dy)
        # Fallback: convert raw user-space to display space, then scale as 72 pts/inch
        disp_x, disp_y = _raw_to_display(v, rotation, raw_w, raw_h)
        return (disp_x / scale, disp_y / scale)

    raw = []
    for annot in page.annots() or []:
        if annot.info.get("subject", "") != "Cloud":
            continue
        verts = [raw_to_dxf(v) for v in annot.vertices]
        xs = [p[0] for p in verts]
        ys = [p[1] for p in verts]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        raw.append({
            "side": "LEFT" if cx < 8.5 else "RIGHT",
            "verts": verts,
            "bbox": (min(xs), max(xs), min(ys), max(ys)),
            "height": max(ys) - min(ys),
            "cy": cy,
        })
    doc.close()

    raw.sort(key=lambda c: (0 if c["side"] == "LEFT" else 1,
                            0 if c["cy"] < 5.5 else 1))
    clouds = []
    for i, rc in enumerate(raw):
        vert_label = "TOP" if rc["cy"] > 5.5 else "BOTTOM"
        clouds.append(Cloud(
            label=f"C{i} ({rc['side']}-{vert_label})",
            side=rc["side"], verts=rc["verts"],
            bbox=rc["bbox"], height=rc["height"],
        ))
    return clouds


def _calibrate_dxf_affine(dxf_path: str, pdf_path: str) -> Optional["np.ndarray"]:
    """Derive an affine transform that maps PDF *display* points into DXF coords.

    Matches text labels between the DXF and the rendered PDF using the PDF text
    span origin (baseline-left), which corresponds to the DXF TEXT insertion point,
    then solves:
        [dxf_x] = M @ [pdf_display_x, pdf_display_y, 1]
    Returns None if not enough reliable matches.
    """
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
    except Exception:
        return None

    pdf_doc = fitz.open(pdf_path)
    page = pdf_doc[0]

    dxf_labels: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for e in msp:
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        txt = (e.dxf.text if e.dxftype() == "TEXT" else e.text).strip()
        if not txt or len(txt) < 3:
            continue
        dxf_labels[txt].append((e.dxf.insert.x, e.dxf.insert.y))

    # Collect PDF text spans with origin (baseline-left in display space)
    pdf_spans: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span.get("text", "").strip()
                if not txt or len(txt) < 3:
                    continue
                ox, oy = span.get("origin", (None, None))
                if ox is None or oy is None:
                    continue
                pdf_spans[txt].append((float(ox), float(oy)))

    pairs_pdf = []
    pairs_dxf = []
    for txt, dxf_positions in dxf_labels.items():
        if len(dxf_positions) != 1:
            continue
        pdf_positions = pdf_spans.get(txt, [])
        if len(pdf_positions) != 1:
            continue
        pairs_pdf.append(pdf_positions[0])
        pairs_dxf.append(dxf_positions[0])

    pdf_doc.close()

    if len(pairs_dxf) < 6:
        return None

    import numpy as np
    A = np.array([[px, py, 1.0] for px, py in pairs_pdf])
    B = np.array(pairs_dxf)

    # Iteratively reject outliers with large residuals
    for _ in range(3):
        M, *_ = np.linalg.lstsq(A, B, rcond=None)
        residuals = np.hypot(
            A[:,0]*M[0,0] + A[:,1]*M[1,0] + M[2,0] - B[:,0],
            A[:,0]*M[0,1] + A[:,1]*M[1,1] + M[2,1] - B[:,1]
        )
        if len(residuals) > 2:
            thresh = np.median(residuals) + 2.0 * np.std(residuals)
        else:
            thresh = 1.0
        mask = residuals <= thresh
        if mask.all():
            break
        A = A[mask]
        B = B[mask]
        if len(B) < 6:
            return None

    M, *_ = np.linalg.lstsq(A, B, rcond=None)
    return M


def _raw_to_display(raw_pt, rotation: int, raw_w: float, raw_h: float):
    """Convert a raw PDF user-space point to displayed page coordinates."""
    rx, ry = float(raw_pt[0]), float(raw_pt[1])
    if rotation == 0:
        return (rx, ry)
    elif rotation == 90:
        return (raw_h - ry, rx)
    elif rotation == 180:
        return (raw_w - rx, raw_h - ry)
    elif rotation == 270:
        return (ry, raw_w - rx)
    return (rx, ry)


def _build_entity_index(dxf_path: str) -> List[EntityInfo]:
    """Parse DXF text directly to avoid ezdxf strictness on subclass markers.

    Captures full geometry: both endpoints of LINEs, all LWPOLYLINE/POLYLINE
    vertices plus segment midpoints, TEXT insertion points and approximate
    bounding boxes, INSERT insertion points, and SOLID vertices.
    """
    with open(dxf_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    in_entities = False
    entities: List[EntityInfo] = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if s == "ENTITIES":
            in_entities = True
        if in_entities and s == "ENDSEC":
            break
        if in_entities and s == "0":
            etype = lines[i + 1].strip() if i + 1 < n else ""
            if etype and etype not in {"SECTION", "ENDSEC", "TABLE", "ENDTAB", "BLOCK", "ENDBLK", "CLASS", "ENDCLASS", "APPID", "BLOCK_RECORD", "DIMSTYLE", "LAYER", "LTYPE", "STYLE", "UCS", "VIEW", "VPORT"}:
                j = i + 2
                handle = None
                layer = ""
                color = 0
                text = ""
                x: Optional[float] = None
                y: Optional[float] = None
                x2: Optional[float] = None
                y2: Optional[float] = None
                verts: List[Tuple[float, float]] = []
                meta: Dict = {}
                text_height: Optional[float] = None
                while j < n:
                    if lines[j].strip() == "0":
                        break
                    code = lines[j].strip()
                    # Robust parsing: if the "value" line itself is an entity type like TEXT/LINE,
                    # ezdxf-style corruption injected a spurious subclass marker. Skip it.
                    if code in ("TEXT", "MTEXT", "LINE", "LWPOLYLINE", "INSERT", "SOLID", "CIRCLE", "ARC"):
                        j += 1
                        continue
                    if code == "5" and j + 1 < n:
                        handle = lines[j + 1].strip().upper()
                    elif code == "8" and j + 1 < n:
                        layer = lines[j + 1].strip()
                    elif code == "62" and j + 1 < n:
                        try:
                            color = abs(int(lines[j + 1].strip()))
                        except ValueError:
                            pass
                    elif code == "1" and j + 1 < n:
                        text = lines[j + 1].strip()
                    elif code == "40" and j + 1 < n:
                        try:
                            text_height = float(lines[j + 1].strip())
                        except ValueError:
                            pass
                    elif code == "10" and j + 1 < n:
                        try:
                            xv = float(lines[j + 1].strip())
                            if j + 3 < n and lines[j + 2].strip() == "20":
                                yv = float(lines[j + 3].strip())
                                verts.append((xv, yv))
                                j += 2
                                if x is None:
                                    x, y = xv, yv
                                elif etype in ("LINE", "LWPOLYLINE", "POLYLINE") and x2 is None:
                                    x2, y2 = xv, yv
                            else:
                                if x is None:
                                    x = xv
                        except ValueError:
                            pass
                    elif code == "11" and j + 1 < n:
                        try:
                            xv = float(lines[j + 1].strip())
                            if j + 3 < n and lines[j + 2].strip() == "21":
                                yv = float(lines[j + 3].strip())
                                x2, y2 = xv, yv
                                verts.append((xv, yv))
                                j += 2
                            else:
                                x2 = xv
                        except ValueError:
                            pass
                    elif code == "20" and j + 1 < n and y is None and x is not None:
                        try:
                            y = float(lines[j + 1].strip())
                        except ValueError:
                            pass
                    elif code == "21" and j + 1 < n and y2 is None and x2 is not None:
                        try:
                            y2 = float(lines[j + 1].strip())
                        except ValueError:
                            pass
                    elif code in ("12", "13") and j + 1 < n:
                        # skip secondary alignment/fit points
                        j += 1
                    elif code == "50" and j + 1 < n:
                        try:
                            meta["angle"] = float(lines[j + 1].strip())
                        except ValueError:
                            pass
                    elif code == "70" and j + 1 < n:
                        try:
                            meta["flags"] = int(lines[j + 1].strip())
                        except ValueError:
                            pass
                    j += 1

                pts: List[Tuple[float, float]] = []
                bb = None

                if etype == "LINE" and x is not None and y is not None and x2 is not None and y2 is not None:
                    pts = [(x, y), (x2, y2), ((x + x2) / 2, (y + y2) / 2)]
                    bb = (min(x, x2), max(x, x2), min(y, y2), max(y, y2))
                elif etype in ("LWPOLYLINE", "POLYLINE") and verts:
                    pts = list(verts)
                    for k in range(len(verts) - 1):
                        p1, p2 = verts[k], verts[k + 1]
                        pts.append(((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2))
                    pts.append((sum(v[0] for v in verts) / len(verts),
                                sum(v[1] for v in verts) / len(verts)))
                    xs = [v[0] for v in verts]
                    ys = [v[1] for v in verts]
                    bb = (min(xs), max(xs), min(ys), max(ys))
                    meta["n_vertices"] = len(verts)
                    meta["vertices"] = verts
                elif etype in ("TEXT", "MTEXT") and x is not None and y is not None:
                    pts.append((x, y))
                    h = text_height or 0.125
                    w = max(h * 0.6 * len(text), h)
                    bb = (x, x + w, y - h, y)
                    meta["text_height"] = h
                elif etype == "INSERT" and x is not None and y is not None:
                    pts.append((x, y))
                    bb = (x, x, y, y)
                elif etype == "SOLID" and verts:
                    pts = list(verts)
                    pts.append((sum(v[0] for v in verts) / len(verts),
                                sum(v[1] for v in verts) / len(verts)))
                    xs = [v[0] for v in verts]
                    ys = [v[1] for v in verts]
                    bb = (min(xs), max(xs), min(ys), max(ys))
                    meta["n_vertices"] = len(verts)
                    meta["vertices"] = verts
                elif x is not None and y is not None:
                    pts.append((x, y))
                    if x2 is not None and y2 is not None:
                        pts.append((x2, y2))
                        bb = (min(x, x2), max(x, x2), min(y, y2), max(y, y2))
                    else:
                        bb = (x, x, y, y)

                if pts and handle:
                    entities.append(EntityInfo(handle=handle, etype=etype, points=pts,
                                               bbox=bb, text=text, color=color, meta=meta))
                i = j - 1
        i += 1
    return entities


def _build_entity_index_ezdxf(dxf_path: str) -> List[EntityInfo]:
    """Build entity index using ezdxf on a clean DXF. Captures full geometry."""
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    entities: List[EntityInfo] = []

    def add(handle, etype, pts, text="", color=0, meta=None, bbox=None):
        if not pts:
            return
        if bbox is None:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bb = (min(xs), max(xs), min(ys), max(ys))
        else:
            bb = bbox
        entities.append(EntityInfo(handle=handle, etype=etype, points=pts,
                                   bbox=bb, text=text, color=color, meta=meta or {}))

    for e in msp:
        etype = e.dxftype()
        if etype == "LINE":
            s = e.dxf.start
            t = e.dxf.end
            pts = [(s.x, s.y), (t.x, t.y), ((s.x + t.x) / 2, (s.y + t.y) / 2)]
            add(e.dxf.handle, etype, pts)
        elif etype == "LWPOLYLINE":
            raw = [(p[0], p[1]) for p in e.get_points('xy')]
            if e.closed:
                raw.append(raw[0])
            pts = list(raw)
            for i in range(len(raw) - 1):
                p1, p2 = raw[i], raw[i + 1]
                pts.append(((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2))
            if raw:
                pts.append((sum(p[0] for p in raw) / len(raw),
                            sum(p[1] for p in raw) / len(raw)))
            meta = {"vertices": raw[:-1] if e.closed else raw, "n_vertices": len(raw) - 1 if e.closed else len(raw)}
            add(e.dxf.handle, etype, pts, meta=meta)
        elif etype == "POLYLINE":
            try:
                raw = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if e.is_closed:
                    raw.append(raw[0])
                pts = list(raw)
                for i in range(len(raw) - 1):
                    p1, p2 = raw[i], raw[i + 1]
                    pts.append(((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2))
                if raw:
                    pts.append((sum(p[0] for p in raw) / len(raw),
                                sum(p[1] for p in raw) / len(raw)))
                meta = {"vertices": raw, "n_vertices": len(raw)}
                add(e.dxf.handle, etype, pts, meta=meta)
            except Exception:
                pass
        elif etype == "CIRCLE":
            cx, cy = e.dxf.center.x, e.dxf.center.y
            r = e.dxf.radius
            pts = [(cx + r * math.cos(a), cy + r * math.sin(a))
                   for a in [i * math.pi / 8 for i in range(16)]]
            pts.append((cx, cy))
            add(e.dxf.handle, etype, pts)
        elif etype == "ARC":
            try:
                cx, cy = e.dxf.center.x, e.dxf.center.y
                r = e.dxf.radius
                sa = math.radians(e.dxf.start_angle)
                ea = math.radians(e.dxf.end_angle)
                pts = []
                for a in [sa + i * (ea - sa) / 16 for i in range(17)]:
                    pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
                pts.append((cx, cy))
                add(e.dxf.handle, etype, pts)
            except Exception:
                pass
        elif etype == "TEXT":
            x, y = e.dxf.insert.x, e.dxf.insert.y
            h = e.dxf.height
            w = max(h * 0.6 * len(e.dxf.text), h)
            pts = [(x, y)]
            meta = {"text_height": h}
            add(e.dxf.handle, etype, pts, text=e.dxf.text, meta=meta,
                bbox=(x, x + w, y - h, y))
        elif etype == "MTEXT":
            x, y = e.dxf.insert.x, e.dxf.insert.y
            text = e.text
            h = e.dxf.text_height if hasattr(e.dxf, 'text_height') else (e.dxf.height if hasattr(e.dxf, 'height') else 0.125)
            w = max(h * 0.6 * len(text), h)
            pts = [(x, y)]
            meta = {"text_height": h}
            add(e.dxf.handle, etype, pts, text=text, meta=meta,
                bbox=(x, x + w, y - h, y))
        elif etype == "INSERT":
            x, y = e.dxf.insert.x, e.dxf.insert.y
            pts = [(x, y)]
            add(e.dxf.handle, etype, pts)
        elif etype == "SOLID":
            try:
                raw = [(v.x, v.y) for v in e.wcs_vertices()]
                pts = list(raw)
                if raw:
                    pts.append((sum(v[0] for v in raw) / len(raw),
                                sum(v[1] for v in raw) / len(raw)))
                meta = {"vertices": raw, "n_vertices": len(raw)}
                add(e.dxf.handle, etype, pts, meta=meta)
            except Exception:
                pass
    return entities

def _segment_intersects_cloud(seg: Tuple[Tuple[float,float], Tuple[float,float]], cloud_verts: List[Tuple[float,float]]) -> bool:
    """Return True if a line segment intersects the cloud polygon boundary or lies inside it."""
    x1, y1 = seg[0]
    x2, y2 = seg[1]
    # Quick bbox reject
    cxmin = min(v[0] for v in cloud_verts)
    cxmax = max(v[0] for v in cloud_verts)
    cymin = min(v[1] for v in cloud_verts)
    cymax = max(v[1] for v in cloud_verts)
    if max(x1, x2) < cxmin or min(x1, x2) > cxmax or max(y1, y2) < cymin or min(y1, y2) > cymax:
        return False
    # Segment-segment intersection against each polygon edge
    n = len(cloud_verts)
    for i in range(n):
        ax, ay = cloud_verts[i]
        bx, by = cloud_verts[(i + 1) % n]
        if _segments_intersect(x1, y1, x2, y2, ax, ay, bx, by):
            return True
    # If both endpoints are inside, the segment is inside
    if MplPath(cloud_verts).contains_point((x1, y1)) and MplPath(cloud_verts).contains_point((x2, y2)):
        return True
    return False


def _segments_intersect(x1, y1, x2, y2, x3, y3, x4, y4) -> bool:
    """Robust 2D segment intersection test."""
    def ccw(Ax, Ay, Bx, By, Cx, Cy):
        return (Cy - Ay) * (Bx - Ax) > (By - Ay) * (Cx - Ax)
    return (ccw(x1, y1, x3, y3, x4, y4) != ccw(x2, y2, x3, y3, x4, y4) and
            ccw(x1, y1, x2, y2, x3, y3) != ccw(x1, y1, x2, y2, x4, y4))


def _entity_segments(e: EntityInfo) -> List[Tuple[Tuple[float,float], Tuple[float,float]]]:
    """Return the line segments represented by an entity."""
    segments = []
    if e.etype == "LINE" and len(e.points) >= 2:
        segments.append((e.points[0], e.points[1]))
    elif e.etype in ("LWPOLYLINE", "POLYLINE"):
        verts = e.meta.get("vertices", [])
        for i in range(len(verts) - 1):
            segments.append((verts[i], verts[i + 1]))
    elif e.etype == "SOLID":
        verts = e.meta.get("vertices", [])
        if len(verts) >= 2:
            for i in range(len(verts) - 1):
                segments.append((verts[i], verts[i + 1]))
    elif e.etype == "CIRCLE":
        cx = sum(p[0] for p in e.points) / len(e.points)
        cy = sum(p[1] for p in e.points) / len(e.points)
        # approximate radius from sample points
        r = sum(math.hypot(p[0] - cx, p[1] - cy) for p in e.points) / len(e.points)
        if r > 0:
            for i in range(16):
                a1 = i * 2 * math.pi / 16
                a2 = (i + 1) * 2 * math.pi / 16
                segments.append(((cx + r * math.cos(a1), cy + r * math.sin(a1)),
                                 (cx + r * math.cos(a2), cy + r * math.sin(a2))))
    return segments


def _bboxes_overlap(a: Tuple[float, ...], b: Tuple[float, ...]) -> bool:
    return a[0] <= b[1] and a[1] >= b[0] and a[2] <= b[3] and a[3] >= b[2]


def _match_entities(entities: List[EntityInfo], clouds: List[Cloud],
                    strict_margin: float = 0.0) -> Tuple[Set[str], Set[str], Dict]:
    deletion: Set[str] = set()
    boundary: Set[str] = set()
    stats = defaultdict(lambda: {"t1": 0, "t2": 0, "t3": 0, "t4": 0})
    for e in entities:
        if not e.points:
            continue
        for cloud in clouds:
            is_thin = cloud.height < 1.0
            done = False

            # T1: point-in-polygon (strict) for any sampled point
            for pt in e.points:
                if MplPath(cloud.verts).contains_point(pt, radius=strict_margin):
                    deletion.add(e.handle)
                    stats[cloud.label]["t1"] += 1
                    done = True
                    break
            if done:
                break

            # T2: full bbox overlap for filled shapes
            if e.bbox and (e.etype == "HATCH" or
                           (is_thin and e.etype in ("POLYLINE", "LINE", "LWPOLYLINE"))):
                if _bboxes_overlap(e.bbox, cloud.bbox):
                    deletion.add(e.handle)
                    stats[cloud.label]["t2"] += 1
                    break

            # T3: segment intersection or contained segment for line-like entities
            if e.etype in ("LINE", "LWPOLYLINE", "POLYLINE", "SOLID", "CIRCLE"):
                for seg in _entity_segments(e):
                    if _segment_intersects_cloud(seg, cloud.verts):
                        deletion.add(e.handle)
                        stats[cloud.label]["t3"] += 1
                        done = True
                        break
                if done:
                    break

            # T4: any point touches the cloud boundary
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
            overrides: Optional[Dict] = None,
            reference_dxf: Optional[str] = None,
            cloud_vertices: Optional[List[List[Tuple[float, float]]]] = None) -> Dict:
        """Run cloud deletion.

        If `cloud_vertices` is provided, only those polygon vertex lists are used
        as clouds. Otherwise all cloud polygons are extracted from the PDF.
        """
        if cloud_vertices is not None:
            affine = _calibrate_dxf_affine(reference_dxf or dxf_path, pdf_path)
            clouds = []
            for i, verts_pdf in enumerate(cloud_vertices):
                verts = []
                for v in verts_pdf:
                    if affine is not None:
                        dx = affine[0, 0] * v[0] + affine[1, 0] * v[1] + affine[2, 0]
                        dy = affine[0, 1] * v[0] + affine[1, 1] * v[1] + affine[2, 1]
                        verts.append((dx, dy))
                    else:
                        verts.append((v[0] / 72.0, v[1] / 72.0))
                xs = [p[0] for p in verts]; ys = [p[1] for p in verts]
                cx = sum(xs) / len(xs); cy = sum(ys) / len(ys)
                side = "LEFT" if cx < 8.5 else "RIGHT"
                vert_label = "TOP" if cy > 5.5 else "BOTTOM"
                clouds.append(Cloud(
                    label=f"C{i} ({side}-{vert_label})",
                    side=side, verts=verts,
                    bbox=(min(xs), max(xs), min(ys), max(ys)),
                    height=max(ys) - min(ys),
                ))
        else:
            clouds = extract_clouds(pdf_path, dxf_path=reference_dxf or dxf_path)

        # Prefer an ezdxf-built index for matching (clean DXF), but also collect the
        # set of handles that actually exist in the working (possibly corrupted) DXF.
        try:
            entities = _build_entity_index_ezdxf(dxf_path)
            reference_entities = entities
        except Exception:
            entities = _build_entity_index(dxf_path)
            reference_entities = _build_entity_index_ezdxf(reference_dxf) if reference_dxf else entities

        deletion, boundary, stats = _match_entities(reference_entities, clouds)
        deletion |= _content_sweep(reference_entities, deletion, clouds)
        preservations = _preserve_label_boxes(reference_entities, deletion)
        kept = {e.handle for e in reference_entities} - deletion
        preservations |= _preserve_ground_refs(reference_entities, deletion, kept)
        # Allow overrides to disable the ground/label preservation heuristics.
        if overrides and overrides.get("preserve") is False:
            preservations = set()
        deletion -= preservations

        # Map deletion handles back to working DXF: only delete handles that actually exist in dxf_path.
        working_handles = {e.handle for e in entities}
        deletion = {h for h in deletion if h in working_handles}
        boundary = {h for h in boundary if h in working_handles}
        preservations = {h for h in preservations if h in working_handles}

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
