"""Generate cloud detection overlay screenshots for pipeline diagnostics.

Two modes:
1. PDF overlay: render the PDF page with its original annotations, then
   overlay detected cloud polygons (C0, C1, ...) in distinct colors.
2. DWG overlay: render the DWG via dwg2bmp, map cloud polygons from PDF
   coordinates to DXF coordinates (affine calibration or border fallback),
   then overlay them on the DWG rendering.

Usage:
    from cli_anything.qcad.utils.cloud_overlay import generate_cloud_overlay
    generate_cloud_overlay("markup.pdf", "overlay.png")  # PDF mode

    from cli_anything.qcad.utils.cloud_overlay import generate_dwg_cloud_overlay
    generate_dwg_cloud_overlay("input.dwg", "markup.pdf", "overlay.png")  # DWG mode
"""
import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

try:
    import fitz  # PyMuPDF
except ImportError as e:  # pragma: no cover
    raise ImportError("PyMuPDF is required. Install: pip install pymupdf") from e

try:
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
except ImportError as e:  # pragma: no cover
    raise ImportError("Pillow and numpy are required. Install: pip install pillow numpy") from e


# Distinct colors for up to 8 clouds
CLOUD_COLORS = [
    (255, 0, 0, 220),      # C0 red
    (0, 200, 0, 220),      # C1 green
    (0, 100, 255, 220),    # C2 blue
    (180, 0, 255, 220),    # C3 purple
    (255, 165, 0, 220),    # C4 orange
    (0, 255, 255, 220),    # C5 cyan
    (255, 255, 0, 220),    # C6 yellow
    (255, 20, 147, 220),   # C7 pink
]

FREETEXT_COLOR = (255, 165, 0, 180)  # orange for FreeText callouts


def _normalize_verts(verts, page, rm, has_rotation):
    """Apply rotation_matrix to polygon vertices."""
    if has_rotation and verts:
        return [
            (v[0] * rm.a + v[1] * rm.c + rm.e,
             v[0] * rm.b + v[1] * rm.d + rm.f)
            for v in verts
        ]
    return verts


def _normalize_rect_corners(rect, page, rm, has_rotation):
    """Apply rotation_matrix to rect, return (min_xy, max_xy)."""
    if has_rotation:
        corners = [(rect.x0, rect.y0), (rect.x1, rect.y0),
                   (rect.x0, rect.y1), (rect.x1, rect.y1)]
        tc = [
            (p[0] * rm.a + p[1] * rm.c + rm.e,
             p[0] * rm.b + p[1] * rm.d + rm.f)
            for p in corners
        ]
        xs = [p[0] for p in tc]
        ys = [p[1] for p in tc]
        return (min(xs), min(ys)), (max(xs), max(ys))
    return (rect.x0, rect.y0), (rect.x1, rect.y1)


def generate_cloud_overlay(
    pdf_path: str,
    output_png: str,
    scale: float = 2.0,
    show_freetexts: bool = True,
) -> str:
    """Render the PDF page with detected cloud polygons overlaid.

    Args:
        pdf_path: Path to the PDF markup file.
        output_png: Path to write the overlay PNG.
        scale: Render scale factor (2.0 = 2x zoom).
        show_freetexts: If True, draw orange rectangles around FreeText callouts.

    Returns:
        Path to the saved PNG.
    """
    doc = fitz.open(pdf_path)
    page = doc[0]

    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, annots=True)
    img = Image.fromarray(
        np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
    ).convert('RGBA')

    rm = page.rotation_matrix
    has_rotation = page.rotation != 0

    # Collect polygon clouds
    polygons: List[List[Tuple[float, float]]] = []
    for annot in page.annots() or []:
        if annot.type[1] in ('Polygon', 'PolyLine'):
            verts = list(annot.vertices) if hasattr(annot, 'vertices') and annot.vertices else []
            polygons.append(_normalize_verts(verts, page, rm, has_rotation))

    # Collect FreeText callouts
    freetexts: List[Dict[str, Any]] = []
    if show_freetexts:
        for annot in page.annots() or []:
            if annot.type[1] == 'FreeText':
                text = annot.info.get('content', '')
                tc = _normalize_rect_corners(annot.rect, page, rm, has_rotation)
                freetexts.append({'text': text, 'corners': tc})

    doc.close()

    # Draw overlay
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(24 * scale / 2)
        )
    except Exception:
        font = ImageFont.load_default()

    for i, verts in enumerate(polygons):
        color = CLOUD_COLORS[i % len(CLOUD_COLORS)]
        px_verts = [(int(v[0] * scale), int(v[1] * scale)) for v in verts]
        draw.polygon(px_verts, fill=color[:3] + (50,))
        draw.line(px_verts + [px_verts[0]], fill=color, width=4)
        xs = [v[0] for v in px_verts]
        ys = [v[1] for v in px_verts]
        cx = int(sum(xs) / len(xs))
        cy = int(sum(ys) / len(ys))
        label = f"C{i}"
        bbox = draw.textbbox((cx, cy), label, font=font)
        draw.rectangle([bbox[0] - 2, bbox[1] - 2, bbox[2] + 2, bbox[3] + 2],
                       fill=(0, 0, 0, 200))
        draw.text((cx, cy), label, fill=color, font=font)

    for ft in freetexts:
        tc = ft['corners']
        txs = [p[0] for p in tc]
        tys = [p[1] for p in tc]
        rect_px = (int(min(txs) * scale), int(min(tys) * scale),
                   int(max(txs) * scale), int(max(tys) * scale))
        draw.rectangle(rect_px, outline=FREETEXT_COLOR, width=2)

    result = Image.alpha_composite(img, overlay)
    result.convert('RGB').save(output_png, 'PNG')
    return output_png


def _compute_dxf_extents(dxf_path: str) -> Tuple[float, float, float, float]:
    """Compute DXF model space extents (min_x, min_y, max_x, max_y)."""
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    min_x, max_x = float('inf'), float('-inf')
    min_y, max_y = float('inf'), float('-inf')
    for e in msp:
        try:
            if e.dxftype() in ('TEXT', 'MTEXT'):
                ip = e.dxf.insert
                min_x, max_x = min(min_x, ip.x), max(max_x, ip.x)
                min_y, max_y = min(min_y, ip.y), max(max_y, ip.y)
            elif e.dxftype() == 'LINE':
                min_x = min(min_x, e.dxf.start.x, e.dxf.end.x)
                max_x = max(max_x, e.dxf.start.x, e.dxf.end.x)
                min_y = min(min_y, e.dxf.start.y, e.dxf.end.y)
                max_y = max(max_y, e.dxf.start.y, e.dxf.end.y)
            elif e.dxftype() == 'LWPOLYLINE':
                for pt in e.get_points():
                    min_x, max_x = min(min_x, pt[0]), max(max_x, pt[0])
                    min_y, max_y = min(min_y, pt[1]), max(max_y, pt[1])
            elif e.dxftype() in ('CIRCLE', 'ARC'):
                c, r = e.dxf.center, e.dxf.radius
                min_x, max_x = min(min_x, c.x - r), max(max_x, c.x + r)
                min_y, max_y = min(min_y, c.y - r), max(max_y, c.y + r)
            elif e.dxftype() == 'INSERT':
                ip = e.dxf.insert
                min_x, max_x = min(min_x, ip.x), max(max_x, ip.x)
                min_y, max_y = min(min_y, ip.y), max(max_y, ip.y)
        except Exception:
            pass
    return min_x, min_y, max_x, max_y


def _content_bbox(img: Image.Image, threshold: int = 30) -> Tuple[int, int, int, int]:
    """Find non-black content bounding box in a rendered image."""
    arr = np.array(img.convert('L'))
    mask = arr > threshold
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return 0, 0, img.width, img.height
    cr = np.where(rows)[0]
    cc = np.where(cols)[0]
    return cc[0], cr[0], cc[-1], cr[-1]


def _pdf_content_bbox(pdf_path: str) -> Tuple[float, float, float, float]:
    """Find content bounding box in PDF page (page.rect space)."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), annots=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )
    gray = np.array(Image.fromarray(arr).convert('L'))
    mask = gray < 200  # non-white
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return 0, 0, float(pix.width), float(pix.height)
    cr = np.where(rows)[0]
    cc = np.where(cols)[0]
    doc.close()
    return float(cc[0]), float(cr[0]), float(cc[-1]), float(cr[-1])


def generate_dwg_cloud_overlay(
    dwg_path: str,
    pdf_path: str,
    output_png: str,
    qcad_dir: Optional[str] = None,
    width: int = 2000,
    height: int = 1500,
    show_labels: bool = True,
) -> str:
    """Render the DWG and overlay detected cloud polygons from the PDF.

    Pipeline:
    1. Convert DWG → DXF
    2. Render DWG to PNG via dwg2bmp (-zoom-all -m 0)
    3. Extract cloud polygons from PDF, normalize to page.rect space
    4. Map page.rect → DXF (affine calibration when text matches exist,
       border calibration as fallback)
    5. Map DXF → PNG pixels via content bbox calibration
    6. Draw cloud overlays on the DWG rendering

    Args:
        dwg_path: Path to the DWG file.
        pdf_path: Path to the PDF markup file.
        output_png: Path to write the overlay PNG.
        qcad_dir: Path to QCAD installation directory.
        width: DWG render width in pixels.
        height: DWG render height in pixels.
        show_labels: If True, draw yellow circles at DXF text label positions.

    Returns:
        Path to the saved PNG.
    """
    if qcad_dir is None:
        qcad_dir = "/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64"

    # 1. Convert DWG → DXF
    from cli_anything.qcad.backends.dwg_converter import DwgConverter
    converter = DwgConverter()  # auto-detect QCAD binary
    dxf_path = str(Path(dwg_path).with_suffix('.dxf'))
    converter.dwg_to_dxf(dwg_path, dxf_path)

    # 2. Render DWG
    dwg_png = str(Path(dxf_path).with_suffix('.png'))
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = qcad_dir
    env["DISPLAY"] = ":0"
    env["XAUTHORITY"] = "/run/user/1000/gdm/Xauthority"
    env["QT_QPA_PLATFORM"] = "offscreen"
    subprocess.run(
        [os.path.join(qcad_dir, "dwg2bmp"), "-f", "-a",
         "-x", str(width), "-y", str(height),
         "-zoom-all", "-m", "0", "-o", dwg_png, dwg_path],
        env=env, capture_output=True, text=True, timeout=60,
    )
    dwg_img = Image.open(dwg_png).convert('RGBA')

    # 3. DXF extents
    min_x, min_y, max_x, max_y = _compute_dxf_extents(dxf_path)
    dxf_w = max_x - min_x
    dxf_h = max_y - min_y

    # 4. DWG content bbox → DXF-to-pixel mapping
    dwg_left, dwg_top, dwg_right, dwg_bottom = _content_bbox(dwg_img)
    dwg_content_w = dwg_right - dwg_left + 1
    dwg_content_h = dwg_bottom - dwg_top + 1

    def dxf_to_pixel(x, y):
        px = dwg_left + (x - min_x) / dxf_w * dwg_content_w
        py = dwg_top + (max_y - y) / dxf_h * dwg_content_h
        return (px, py)

    # 5. Extract clouds from PDF, normalize to page.rect space
    doc_pdf = fitz.open(pdf_path)
    page = doc_pdf[0]
    rm = page.rotation_matrix
    has_rotation = page.rotation != 0

    clouds = []
    freetexts = []  # FreeText callout annotations with arrow vertices
    for annot in page.annots() or []:
        atype = annot.type[1]
        if atype in ('Polygon', 'PolyLine'):
            verts = list(annot.vertices) if hasattr(annot, 'vertices') and annot.vertices else []
            clouds.append(_normalize_verts(verts, page, rm, has_rotation))
        elif atype == 'FreeText':
            text = annot.info.get('content', '')
            verts = list(annot.vertices) if hasattr(annot, 'vertices') and annot.vertices else []
            if verts:
                ft_verts = _normalize_verts(verts, page, rm, has_rotation)
                freetexts.append({'text': text, 'verts': ft_verts})
    doc_pdf.close()

    # 6. Map page.rect → DXF (affine or border calibration)
    from cli_anything.qcad.core.planner import (
        _extract_pdf_text_spans, _calibrate_affine, _map_pdf_point_to_dxf,
    )
    from cli_anything.qcad.utils.dxf_entity_index import DxfEntityIndex

    index = DxfEntityIndex(dxf_path)
    index.load()
    pdf_spans = _extract_pdf_text_spans(pdf_path)
    affine = _calibrate_affine(pdf_spans, index)

    if affine is not None:
        def pr_to_dxf(x, y):
            return _map_pdf_point_to_dxf((x, y), affine)
    else:
        # Border calibration fallback (same logic as planner._border_calibration)
        from cli_anything.qcad.utils.cloud_overlay import _pdf_content_bbox, _compute_dxf_extents as _ext
        pdf_left_b, pdf_top_b, pdf_right_b, pdf_bottom_b = _pdf_content_bbox(pdf_path)
        pdf_content_w = pdf_right_b - pdf_left_b
        pdf_content_h = pdf_bottom_b - pdf_top_b

        def pr_to_dxf(pr_x, pr_y):
            dx = (pr_x - pdf_left_b) / pdf_content_w * dxf_w + min_x
            dy = (pdf_bottom_b - pr_y) / pdf_content_h * dxf_h + min_y
            return (dx, dy)

    # 7. Draw overlay
    overlay = Image.new('RGBA', dwg_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18
        )
    except Exception:
        font = ImageFont.load_default()

    for i, verts_pr in enumerate(clouds):
        verts_dxf = [pr_to_dxf(x, y) for x, y in verts_pr]
        px_verts = [(int(dxf_to_pixel(x, y)[0]), int(dxf_to_pixel(x, y)[1]))
                     for x, y in verts_dxf]
        color = CLOUD_COLORS[i % len(CLOUD_COLORS)]
        draw.polygon(px_verts, fill=color[:3] + (40,))
        draw.line(px_verts + [px_verts[0]], fill=color, width=3)
        xs = [v[0] for v in px_verts]
        ys = [v[1] for v in px_verts]
        cx = int(sum(xs) / len(xs))
        cy = int(sum(ys) / len(ys))
        label = f"C{i}"
        bbox = draw.textbbox((cx, cy), label, font=font)
        draw.rectangle([bbox[0] - 2, bbox[1] - 2, bbox[2] + 2, bbox[3] + 2],
                       fill=(0, 0, 0, 200))
        draw.text((cx, cy), label, fill=color, font=font)

    # Draw FreeText callout arrows (text box → arrow tip line)
    for i, ft in enumerate(freetexts):
        verts_pr = ft['verts']
        text = ft['text'][:40]  # truncate long text
        verts_dxf = [pr_to_dxf(x, y) for x, y in verts_pr]
        px_verts = [(int(dxf_to_pixel(x, y)[0]), int(dxf_to_pixel(x, y)[1]))
                     for x, y in verts_dxf]
        color = FREETEXT_COLOR
        # Draw the callout line from text box to arrow tip
        if len(px_verts) >= 2:
            draw.line(px_verts, fill=color, width=3)
        # Draw arrow tip marker (last vertex) as a filled circle
        if px_verts:
            tip = px_verts[-1]
            draw.ellipse([tip[0] - 8, tip[1] - 8, tip[0] + 8, tip[1] + 8],
                         fill=color[:3] + (200,))
            # Draw text box marker (first vertex) as a hollow rectangle
            box = px_verts[0]
            draw.rectangle([box[0] - 6, box[1] - 6, box[0] + 6, box[1] + 6],
                           outline=color, width=2)
        # Label with annotation text
        label = f"F{i}: {text}"
        if px_verts:
            lx, ly = px_verts[0][0] + 10, px_verts[0][1] - 10
            bbox = draw.textbbox((lx, ly), label, font=font)
            draw.rectangle([bbox[0] - 2, bbox[1] - 2, bbox[2] + 2, bbox[3] + 2],
                           fill=(0, 0, 0, 200))
            draw.text((lx, ly), label, fill=color, font=font)

    # Mark known DXF text label positions
    if show_labels:
        for e in index.get_all_text_entities():
            if len(e.text) >= 2:
                px, py = dxf_to_pixel(e.insertion_point[0], e.insertion_point[1])
                draw.ellipse([px - 5, py - 5, px + 5, py + 5],
                             outline=(255, 255, 0), width=1)
                if len(e.text) <= 8:
                    draw.text((px + 7, py - 7), e.text, fill=(255, 255, 0), font=font)

    result = Image.alpha_composite(dwg_img, overlay)
    result.convert('RGB').save(output_png, 'PNG')

    # Cleanup temp files
    for f in [dxf_path, dwg_png]:
        try:
            os.remove(f)
        except OSError:
            pass

    return output_png