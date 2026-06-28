"""Pair 3 executor: clone rows 4/5/6 to rows 7/8/9 + text replacements + revision row."""
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import ezdxf
import fitz


ROW_BAND = 0.15
SOURCE_YS = [20.125, 19.875, 19.625]
TARGET_YS = [19.375, 19.125, 18.875]
TEXT_REPLACEMENTS = [
    ('PLC21', 'PLC22'),
    ('CA-1451', 'CA-1452'),
    ('02732', '02733'),
    ('B-SAR-280-02732', 'B-SAR-280-02733'),
]


def _pdf_page_size(pdf_path: str) -> Tuple[float, float]:
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
    pdf_w, pdf_h = pdf_size
    return ((max(xs) - min(xs)) / pdf_w, (max(ys) - min(ys)) / pdf_h, min(xs), min(ys))


def _pdf_to_dxf_point(px: float, py: float, scale: Tuple[float, float, float, float]) -> Tuple[float, float]:
    return (px * scale[0] + scale[2], py * scale[1] + scale[3])


def _entity_y_range(ent) -> Tuple[float, float]:
    try:
        t = ent.dxftype()
        if t in ('TEXT', 'MTEXT', 'INSERT'):
            return (ent.dxf.insert.y, ent.dxf.insert.y)
        if t == 'LINE':
            ys = [ent.dxf.start[1], ent.dxf.end[1]]
            return (min(ys), max(ys))
        if t == 'LWPOLYLINE':
            ys = [p[1] for p in ent.get_points('xy')]
            return (min(ys), max(ys))
        if t == 'ARC':
            return (ent.dxf.center.y - ent.dxf.radius, ent.dxf.center.y + ent.dxf.radius)
    except Exception:
        pass
    return (1e9, -1e9)


def _collect_entities_in_band(msp, y_center: float) -> List[Any]:
    lo, hi = y_center - ROW_BAND, y_center + ROW_BAND
    out = []
    for ent in msp:
        ymin, ymax = _entity_y_range(ent)
        if ymax >= lo and ymin <= hi:
            out.append(ent)
    return out


def _clone_row(doc: ezdxf.document.Drawing, source_y: float, target_y: float) -> int:
    msp = doc.modelspace()
    source_entities = _collect_entities_in_band(msp, source_y)
    dy = target_y - source_y

    # Delete existing entities in target band to avoid overlap
    target_entities = _collect_entities_in_band(msp, target_y)
    for ent in target_entities:
        try:
            msp.delete_entity(ent)
        except Exception:
            pass

    cloned = 0
    for ent in source_entities:
        try:
            copy = doc.entitydb.duplicate_entity(ent)
            if copy.dxftype() in ('TEXT', 'MTEXT'):
                copy.dxf.insert = (copy.dxf.insert.x, copy.dxf.insert.y + dy)
            elif copy.dxftype() == 'INSERT':
                copy.dxf.insert = (copy.dxf.insert.x, copy.dxf.insert.y + dy)
            elif copy.dxftype() == 'LINE':
                copy.dxf.start = (copy.dxf.start.x, copy.dxf.start.y + dy, copy.dxf.start.z)
                copy.dxf.end = (copy.dxf.end.x, copy.dxf.end.y + dy, copy.dxf.end.z)
            elif copy.dxftype() == 'LWPOLYLINE':
                pts = copy.get_points('xy')
                new_pts = [(x, y + dy) for x, y in pts]
                copy.set_points(new_pts)
            elif copy.dxftype() == 'ARC':
                copy.dxf.center = (copy.dxf.center.x, copy.dxf.center.y + dy, copy.dxf.center.z)
            msp.add_entity(copy)
            cloned += 1
        except Exception:
            pass
    return cloned


def _replace_cloned_text(doc: ezdxf.document.Drawing) -> int:
    changed = 0
    for ent in doc.modelspace():
        if ent.dxftype() in ('TEXT', 'MTEXT'):
            try:
                text = ent.dxf.text or ''
                new = text
                for old_s, new_s in TEXT_REPLACEMENTS:
                    new = new.replace(old_s, new_s)
                if new != text:
                    ent.dxf.text = new
                    changed += 1
            except Exception:
                pass
    return changed


def _add_revision_row(doc: ezdxf.document.Drawing, pdf_path: str, scale: Tuple[float, float, float, float]) -> bool:
    # Find revision table area by looking for text "REV" or "DATE" near bottom-left
    target_x, target_y = None, None
    for ent in doc.modelspace():
        if ent.dxftype() in ('TEXT', 'MTEXT'):
            text = (ent.dxf.text or '').upper()
            if 'REV' in text or 'DATE' in text or 'DESCRIPTION' in text:
                if target_y is None or ent.dxf.insert.y < target_y:
                    target_y = ent.dxf.insert.y
                    target_x = ent.dxf.insert.x
    if target_x is None:
        return False

    # Add revision entries near that location
    doc.modelspace().add_text('01A', height=0.15, dxfattribs={'insert': (target_x, target_y - 0.25)})
    doc.modelspace().add_text('2026/05/04', height=0.15, dxfattribs={'insert': (target_x + 0.8, target_y - 0.25)})
    doc.modelspace().add_text('IFR', height=0.15, dxfattribs={'insert': (target_x + 2.0, target_y - 0.25)})
    return True


def execute_pair3(dxf_in: str, pdf_path: str, dwg_out: str) -> Dict[str, Any]:
    doc = ezdxf.readfile(dxf_in)

    cloned = 0
    for sy, ty in zip(SOURCE_YS, TARGET_YS):
        cloned += _clone_row(doc, sy, ty)

    text_changes = _replace_cloned_text(doc)

    scale = _compute_calibration(dxf_in, _pdf_page_size(pdf_path))
    revision_added = _add_revision_row(doc, pdf_path, scale)

    work_dir = Path(dwg_out).parent
    work_dir.mkdir(parents=True, exist_ok=True)
    modified_dxf = str(work_dir / '3_modified.dxf')
    doc.saveas(modified_dxf)

    from cli_anything.qcad.backends.dwg_converter import DwgConverter
    conv = DwgConverter()
    ok = conv.dxf_to_dwg(modified_dxf, dwg_out)

    return {
        'success': ok and Path(dwg_out).exists() and Path(dwg_out).stat().st_size > 1000,
        'cloned': cloned,
        'text_changes': text_changes,
        'revision_added': revision_added,
        'dwg_out': dwg_out,
    }
