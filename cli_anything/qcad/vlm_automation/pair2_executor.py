"""Pair 2 executor: cloud deletion + free-text edits (add / change)."""
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import ezdxf
import fitz


def _pdf_page_size(pdf_path: str) -> Tuple[float, float]:
    doc = fitz.open(pdf_path)
    page = doc[0]
    if page.rotation in (90, 270):
        size = (page.mediabox.height, page.mediabox.width)
    else:
        size = (page.mediabox.width, page.mediabox.height)
    doc.close()
    return size


def _parse_annotations(pdf_path: str) -> List[Dict[str, Any]]:
    doc = fitz.open(pdf_path)
    page = doc[0]
    annots = []
    for a in page.annots():
        r = a.rect
        if page.rotation:
            r = r * page.derotation_matrix
        annots.append({
            'type': a.type[1],
            'rect': (r.x0, r.y0, r.x1, r.y1),
            'text': (a.get_text() or '').strip(),
        })
    doc.close()
    return annots


def _compute_calibration(dxf_path: str, pdf_size: Tuple[float, float]) -> Tuple[float, float, float, float]:
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    pts = []
    for e in msp:
        try:
            if e.dxftype() in ('TEXT', 'MTEXT'):
                pts.append((e.dxf.insert.x, e.dxf.insert.y))
            elif e.dxftype() == 'LWPOLYLINE':
                pts.extend([(p[0], p[1]) for p in e.get_points('xy')])
        except Exception:
            pass
    if not pts:
        return 1.0, 1.0, 0.0, 0.0
    xs, ys = zip(*pts)
    pdf_w, pdf_h = pdf_size
    return ((max(xs) - min(xs)) / pdf_w, (max(ys) - min(ys)) / pdf_h, min(xs), min(ys))


def _entity_center(ent) -> Tuple[float, float]:
    try:
        if ent.dxftype() in ('TEXT', 'MTEXT', 'INSERT'):
            return (ent.dxf.insert.x, ent.dxf.insert.y)
        if ent.dxftype() in ('LINE', 'LWPOLYLINE'):
            bb = ent.extents()
            return ((bb[0][0] + bb[1][0]) / 2.0, (bb[0][1] + bb[1][1]) / 2.0)
        if ent.dxftype() == 'ARC':
            return (ent.dxf.center.x, ent.dxf.center.y)
    except Exception:
        pass
    return (0.0, 0.0)


def _pdf_to_dxf_point(px: float, py: float, scale: Tuple[float, float, float, float]) -> Tuple[float, float]:
    return (px * scale[0] + scale[2], py * scale[1] + scale[3])


def _nearest_text_entity(msp, x: float, y: float):
    best = None
    best_d2 = 1e9
    for ent in msp:
        if ent.dxftype() not in ('TEXT', 'MTEXT'):
            continue
        try:
            ex, ey = ent.dxf.insert.x, ent.dxf.insert.y
            d2 = (ex - x) ** 2 + (ey - y) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best = ent
        except Exception:
            pass
    return best, best_d2


def _match_cloud_entities(doc: ezdxf.document.Drawing, rects: List[Tuple[float, float, float, float]],
                          scale: Tuple[float, float, float, float], margin: float = 2.0) -> Set[str]:
    matched = set()
    for ent in doc.modelspace():
        try:
            cx, cy = _entity_center(ent)
        except Exception:
            continue
        for rx0, ry0, rx1, ry1 in rects:
            dx0 = rx0 * scale[0] + scale[2] - margin
            dy0 = ry0 * scale[1] + scale[3] - margin
            dx1 = rx1 * scale[0] + scale[2] + margin
            dy1 = ry1 * scale[1] + scale[3] + margin
            if dx0 <= cx <= dx1 and dy0 <= cy <= dy1:
                matched.add(str(ent.dxf.handle).upper())
                break
    return matched


def _apply_cloud_deletion(doc: ezdxf.document.Drawing, pdf_path: str) -> Tuple[Set[str], int]:
    annots = _parse_annotations(pdf_path)
    cloud_rects = [a['rect'] for a in annots if a['type'] in ('Polygon', 'Square', 'Highlight')]
    if not cloud_rects:
        return set(), 0
    scale = _compute_calibration(doc.filename, _pdf_page_size(pdf_path))
    to_delete = _match_cloud_entities(doc, cloud_rects, scale, margin=2.0)

    # Preserve title/labels
    preserved: Set[str] = set()
    for ent in doc.modelspace():
        if ent.dxftype() in ('TEXT', 'MTEXT'):
            text = (ent.dxf.text or '').upper()
            if any(k in text for k in ['TITLE', 'PLAINS', 'LANGTREE', 'DWG', 'REV']):
                preserved.add(str(ent.dxf.handle).upper())

    final = to_delete - preserved
    for ent in list(doc.modelspace()):
        if str(ent.dxf.handle).upper() in final:
            doc.modelspace().delete_entity(ent)
    return final, len(final)


def _apply_text_changes(doc: ezdxf.document.Drawing, pdf_path: str) -> int:
    annots = _parse_annotations(pdf_path)
    scale = _compute_calibration(doc.filename, _pdf_page_size(pdf_path))
    changes = 0
    for a in annots:
        if a['type'] != 'FreeText':
            continue
        txt = a['text']

        # "Change to X" → replace nearest text with X
        m = re.search(r'(?:change\s+(?:.*?)\s+)?to\s+["\']?([^"\']+)["\']?', txt, re.I)
        if m:
            target = m.group(1).strip()
            cx = (a['rect'][0] + a['rect'][2]) / 2.0
            cy = (a['rect'][1] + a['rect'][3]) / 2.0
            dx, dy = _pdf_to_dxf_point(cx, cy, scale)
            ent, d2 = _nearest_text_entity(doc.modelspace(), dx, dy)
            if ent and d2 < 25.0:
                ent.dxf.text = target
                changes += 1
            continue

        # "add "TEXT"" → insert new TEXT near annotation
        m = re.search(r'add\s+["\']([^"\']+)["\']', txt, re.I)
        if m:
            target = m.group(1).strip()
            cx = (a['rect'][0] + a['rect'][2]) / 2.0
            cy = (a['rect'][1] + a['rect'][3]) / 2.0
            dx, dy = _pdf_to_dxf_point(cx, cy, scale)
            doc.modelspace().add_text(target, height=0.15, dxfattribs={'insert': (dx, dy)})
            changes += 1
    return changes


def execute_pair2(dxf_in: str, pdf_path: str, dwg_out: str) -> Dict[str, Any]:
    doc = ezdxf.readfile(dxf_in)
    deleted_handles, deleted_count = _apply_cloud_deletion(doc, pdf_path)
    text_changes = _apply_text_changes(doc, pdf_path)

    work_dir = Path(dwg_out).parent
    work_dir.mkdir(parents=True, exist_ok=True)
    modified_dxf = str(work_dir / '2_modified.dxf')
    doc.saveas(modified_dxf)

    from cli_anything.qcad.backends.dwg_converter import DwgConverter
    conv = DwgConverter()
    ok = conv.dxf_to_dwg(modified_dxf, dwg_out)

    return {
        'success': ok and Path(dwg_out).exists() and Path(dwg_out).stat().st_size > 1000,
        'deleted': deleted_count,
        'text_changes': text_changes,
        'dwg_out': dwg_out,
    }
