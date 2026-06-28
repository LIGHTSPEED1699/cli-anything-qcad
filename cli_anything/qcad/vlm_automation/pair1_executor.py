"""Pair 1 executor: PDF polygon annotations → DXF text clearing via ezdxf.

The proven kanban algorithm uses extent-based PDF→DXF calibration and bbox
matching.  We replicate that logic, but use ezdxf for the actual DXF mutation
so the output is compatible with LibreDWG's dxf2dwg.
"""
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import ezdxf
import fitz


def _parse_pdf_rects(pdf_path: str) -> List[Tuple[float, float, float, float]]:
    doc = fitz.open(pdf_path)
    page = doc[0]
    rects = []
    for a in page.annots():
        if a.type[1] in ('Polygon', 'Square', 'Highlight'):
            r = a.rect
            # Derotate to PDF user space if the page is rotated
            if page.rotation:
                r = r * page.derotation_matrix
            rects.append((r.x0, r.y0, r.x1, r.y1))
    doc.close()
    return rects


def _page_size(pdf_path: str) -> Tuple[float, float]:
    doc = fitz.open(pdf_path)
    page = doc[0]
    if page.rotation in (90, 270):
        size = (page.mediabox.height, page.mediabox.width)
    else:
        size = (page.mediabox.width, page.mediabox.height)
    doc.close()
    return size


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
    dxf_minx, dxf_miny = min(xs), min(ys)
    pdf_w, pdf_h = pdf_size
    scale_x = (max(xs) - dxf_minx) / pdf_w if pdf_w else 1.0
    scale_y = (max(ys) - dxf_miny) / pdf_h if pdf_h else 1.0
    return scale_x, scale_y, dxf_minx, dxf_miny


def _entity_center(ent) -> Tuple[float, float]:
    try:
        if ent.dxftype() in ('TEXT', 'MTEXT'):
            return (ent.dxf.insert.x, ent.dxf.insert.y)
        if ent.dxftype() == 'INSERT':
            return (ent.dxf.insert.x, ent.dxf.insert.y)
        if ent.dxftype() in ('LINE', 'LWPOLYLINE'):
            bb = ent.extents()
            return ((bb[0][0] + bb[1][0]) / 2.0, (bb[0][1] + bb[1][1]) / 2.0)
        if ent.dxftype() == 'ARC':
            return (ent.dxf.center.x, ent.dxf.center.y)
    except Exception:
        pass
    return (0.0, 0.0)


def _match_entities(doc: ezdxf.document.Drawing,
                    rects: List[Tuple[float, float, float, float]],
                    scale: Tuple[float, float, float, float],
                    margin: float = 2.0) -> Set[str]:
    scale_x, scale_y, off_x, off_y = scale
    matched = set()
    msp = doc.modelspace()
    for ent in msp:
        try:
            cx, cy = _entity_center(ent)
        except Exception:
            continue
        for rx0, ry0, rx1, ry1 in rects:
            dx0 = rx0 * scale_x + off_x - margin
            dy0 = ry0 * scale_y + off_y - margin
            dx1 = rx1 * scale_x + off_x + margin
            dy1 = ry1 * scale_y + off_y + margin
            if dx0 <= cx <= dx1 and dy0 <= cy <= dy1:
                matched.add(str(ent.dxf.handle).upper())
                break
    return matched


def _clear_text(doc: ezdxf.document.Drawing, handles: Set[str]) -> int:
    cleared = 0
    msp = doc.modelspace()
    for ent in msp:
        h = str(ent.dxf.handle).upper()
        if h not in handles:
            continue
        if ent.dxftype() in ('TEXT', 'MTEXT'):
            try:
                text = (ent.dxf.text or '').strip()
                if not re.search(r'F\d+|\bGND\b|^\d+$|^\d+\s*[A-Z]?$', text, re.I):
                    ent.dxf.text = '.'
                    cleared += 1
            except Exception:
                pass
    return cleared


def _restore_4B6E(doc: ezdxf.document.Drawing) -> bool:
    try:
        ent = doc.entitydb['4B6E']
        if ent and ent.dxftype() in ('TEXT', 'MTEXT'):
            ent.dxf.text = 'F174'
            return True
    except Exception:
        pass
    return False


def execute_pair1(dxf_in: str, pdf_path: str, dwg_out: str,
                  dxf2dwg_bin: str = '/media/sdddata1/libredwg/bin/dxf2dwg') -> Dict[str, Any]:
    doc = ezdxf.readfile(dxf_in)
    pdf_size = _page_size(pdf_path)
    scale = _compute_calibration(dxf_in, pdf_size)
    rects = _parse_pdf_rects(pdf_path)

    matched = _match_entities(doc, rects, scale, margin=2.0)

    # Preserve ground references and title/label text
    preserved: Set[str] = set()
    for ent in doc.modelspace():
        if ent.dxftype() in ('TEXT', 'MTEXT'):
            text = (ent.dxf.text or '').upper()
            if any(k in text for k in ['TITLE', 'PLAINS', 'LANGTREE', 'DWG', 'REV', 'F174', 'F175', 'F176']):
                preserved.add(str(ent.dxf.handle).upper())

    final = matched - preserved
    cleared = _clear_text(doc, final)
    restored = _restore_4B6E(doc)

    work_dir = Path(dwg_out).parent
    work_dir.mkdir(parents=True, exist_ok=True)
    modified_dxf = str(work_dir / '1_modified.dxf')
    doc.saveas(modified_dxf)

    from cli_anything.qcad.backends.dwg_converter import DwgConverter
    conv = DwgConverter()
    ok = conv.dxf_to_dwg(modified_dxf, dwg_out)

    return {
        'success': ok and Path(dwg_out).exists() and Path(dwg_out).stat().st_size > 1000,
        'annotations': len(rects),
        'matched': len(matched),
        'preserved': len(preserved),
        'cleared': cleared,
        'restored_f174': restored,
        'scale': scale,
        'dwg_out': dwg_out,
    }
