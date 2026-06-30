"""Generate cloud detection overlay screenshots for pipeline diagnostics.

Renders the PDF page with its original annotations, then overlays the
detected cloud polygons (C0, C1, C2, ...) and FreeText callout rectangles
in distinct colors.  This lets you visually verify that the PDF annotation
parser correctly identified the revision clouds before the pipeline
proceeds to deletion or modification steps.

Usage:
    from cli_anything.qcad.utils.cloud_overlay import generate_cloud_overlay
    generate_cloud_overlay("markup.pdf", "overlay.png")
"""
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

    # Render the PDF page with its original annotations visible
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, annots=True)
    img = Image.fromarray(
        np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
    ).convert('RGBA')

    rm = page.rotation_matrix
    has_rotation = page.rotation != 0

    # Collect polygon clouds — raw coordinates, no normalization
    polygons: List[List[Tuple[float, float]]] = []
    for annot in page.annots() or []:
        if annot.type[1] in ('Polygon', 'PolyLine'):
            verts = list(annot.vertices) if hasattr(annot, 'vertices') and annot.vertices else []
            polygons.append(verts)

    # Collect FreeText callouts — raw coordinates
    freetexts: List[Dict[str, Any]] = []
    if show_freetexts:
        for annot in page.annots() or []:
            if annot.type[1] == 'FreeText':
                text = annot.info.get('content', '')
                r = annot.rect
                tc = [(r.x0, r.y0), (r.x1, r.y0), (r.x0, r.y1), (r.x1, r.y1)]
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

        # Semi-transparent fill
        draw.polygon(px_verts, fill=color[:3] + (50,))
        # Solid outline
        draw.line(px_verts + [px_verts[0]], fill=color, width=4)

        # Label
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