"""Parse PDF markup annotations into structured tasks."""
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import fitz  # type: ignore  # PyMuPDF
except ImportError as e:  # pragma: no cover
    raise ImportError("PyMuPDF is required. Install: pip install pymupdf") from e


class AnnotationType(Enum):
    REPLACE = "replace"
    MOVE = "move"
    CHANGE_PROPERTY = "change_property"
    DELETE = "delete"
    ADD = "add"
    REORDER = "reorder"
    UNKNOWN = "unknown"


@dataclass
class Annotation:
    text: str
    target_bbox: List[float]
    page: int
    annot_type: str
    arrow_vertices: Optional[List[Tuple[float, float]]] = None
    author: str = ""
    inferred_action: str = "unknown"
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def infer_action_type(text: str) -> Tuple[str, float]:
    text_lower = text.lower().strip()

    replace_keywords = ['replace', 'swap', 'change to', 'use instead']
    if any(kw in text_lower for kw in replace_keywords):
        return (AnnotationType.REPLACE.value, 0.9)

    move_keywords = ['move', 'relocate', 'shift', 'position']
    if any(kw in text_lower for kw in move_keywords):
        return (AnnotationType.MOVE.value, 0.9)

    reorder_keywords = ['reorder', 'rearrange', 'move this row', 'move to row', 'following']
    if any(kw in text_lower for kw in reorder_keywords):
        return (AnnotationType.REORDER.value, 0.9)

    color_keywords = ['change color', 'change to', 'make it', 'set color', 'change', 'blu to', 'wht to']
    if any(kw in text_lower for kw in color_keywords):
        return (AnnotationType.CHANGE_PROPERTY.value, 0.8)

    delete_keywords = ['delete', 'remove', 'erase', 'get rid of']
    if any(kw in text_lower for kw in delete_keywords):
        return (AnnotationType.DELETE.value, 0.9)

    add_keywords = ['add', 'insert', 'create', 'draw']
    if any(kw in text_lower for kw in add_keywords):
        return (AnnotationType.ADD.value, 0.8)

    return (AnnotationType.UNKNOWN.value, 0.3)


def _is_actionable(text: str) -> bool:
    text_lower = text.lower().strip()
    skip_patterns = [
        'rev ', 'revision', 'reviewed by', 'approved by',
        'date:', 'project:', 'drawing no', 'sheet',
        'windsor plant', 'plant support'
    ]
    if any(pat in text_lower for pat in skip_patterns):
        action_keywords = ['replace', 'move', 'change', 'delete', 'add', 'reorder']
        return any(kw in text_lower for kw in action_keywords)
    return True


def _rects_overlap(rect1: fitz.Rect, rect2: fitz.Rect, tolerance: float = 0) -> bool:
    r1 = fitz.Rect(
        rect1.x0 - tolerance,
        rect1.y0 - tolerance,
        rect1.x1 + tolerance,
        rect1.y1 + tolerance
    )
    intersect = r1.intersect(rect2)
    return intersect.width > 0 and intersect.height > 0


class PdfAnnotationParser:
    """Extract actionable annotations from a PDF markup file.

    Handles PDFs with page rotation by normalizing all annotation vertices
    to page.rect (rotated) coordinate space before returning them.
    """

    @staticmethod
    def _normalize_vertices(
        vertices: List[Tuple[float, float]],
        page: fitz.Page,
    ) -> List[Tuple[float, float]]:
        """Transform annotation vertices to page.rect (rotated) space.

        On rotated pages, PyMuPDF returns annotation vertices in mediabox
        (unrotated) coordinate space.  We apply the page's rotation_matrix
        to transform them to page.rect (rotated) space, matching the
        normalized text spans used for affine calibration.
        """
        if not vertices or page.rotation == 0:
            return vertices
        rm = page.rotation_matrix
        return [
            (v[0] * rm.a + v[1] * rm.c + rm.e,
             v[0] * rm.b + v[1] * rm.d + rm.f)
            for v in vertices
        ]

    @staticmethod
    def _normalize_rect(
        rect: fitz.Rect,
        page: fitz.Page,
    ) -> fitz.Rect:
        """Transform annotation rect to page.rect (rotated) space."""
        if page.rotation == 0:
            return rect
        rm = page.rotation_matrix
        corners = [
            (rect.x0, rect.y0),
            (rect.x1, rect.y0),
            (rect.x0, rect.y1),
            (rect.x1, rect.y1),
        ]
        transformed = [
            (p[0] * rm.a + p[1] * rm.c + rm.e,
             p[0] * rm.b + p[1] * rm.d + rm.f)
            for p in corners
        ]
        xs = [p[0] for p in transformed]
        ys = [p[1] for p in transformed]
        return fitz.Rect(min(xs), min(ys), max(xs), max(ys))

    def parse(self, pdf_path: str) -> List[Dict[str, Any]]:
        annotations: List[Annotation] = []
        doc = fitz.open(pdf_path)

        for page_num in range(len(doc)):
            page = doc[page_num]
            freetext_annots, line_annots = [], []
            polygon_annots = []
            all_clouds = []
            for annot in page.annots() or []:
                annot_type = annot.type[1]
                if annot_type == 'FreeText':
                    freetext_annots.append(annot)
                elif annot_type == 'Line':
                    line_annots.append(annot)
                elif annot_type in ('Polygon', 'PolyLine'):
                    polygon_annots.append(annot)

            # First pass: collect polygons with their own text if any
            for pg_annot in polygon_annots:
                text = pg_annot.info.get("content", "").strip()
                vertices = list(pg_annot.vertices) if hasattr(pg_annot, 'vertices') and pg_annot.vertices else []
                if vertices:
                    vertices = self._normalize_vertices(vertices, page)
                    cloud_rect = self._normalize_rect(pg_annot.rect, page)
                    all_clouds.append({
                        "annot_type": "Polygon",
                        "text": text,
                        "vertices": vertices,
                        "rect": [cloud_rect.x0, cloud_rect.y0, cloud_rect.x1, cloud_rect.y1],
                        "page": page_num,
                    })

            for ft_annot in freetext_annots:
                text = ft_annot.info.get("content", "").strip()
                if not _is_actionable(text):
                    continue
                rect = self._normalize_rect(ft_annot.rect, page)
                author = ft_annot.info.get("title", "")

                # Try to find an overlapping/nearby polygon cloud for this FreeText
                cloud_vertices = None
                for cloud in all_clouds:
                    if _rects_overlap(rect, fitz.Rect(*cloud["rect"]), tolerance=80):
                        cloud_vertices = cloud["vertices"]
                        break

                # Fallback to line arrow if no cloud polygon found
                if cloud_vertices is None:
                    for line_annot in line_annots:
                        line_rect = self._normalize_rect(line_annot.rect, page)
                        if _rects_overlap(rect, line_rect, tolerance=50):
                            if hasattr(line_annot, 'vertices') and line_annot.vertices:
                                cloud_vertices = self._normalize_vertices(
                                    list(line_annot.vertices), page)
                            break

                target_bbox = [rect.x0, rect.y0, rect.x1, rect.y1]
                if cloud_vertices and len(cloud_vertices) >= 2:
                    xs = [v[0] for v in cloud_vertices]
                    ys = [v[1] for v in cloud_vertices]
                    target_bbox = [min(xs), min(ys), max(xs), max(ys)]

                action_type, confidence = infer_action_type(text)
                annotations.append(Annotation(
                    text=text,
                    target_bbox=target_bbox,
                    page=page_num,
                    annot_type="FreeText",
                    arrow_vertices=cloud_vertices,
                    author=author,
                    inferred_action=action_type,
                    confidence=confidence,
                ))

            # Also emit standalone polygon clouds that look like deletion clouds
            for cloud in all_clouds:
                if not cloud["text"]:
                    # Skip if any FreeText already claimed this cloud
                    claimed = False
                    cloud_rect = fitz.Rect(*cloud["rect"])
                    for ft_annot in freetext_annots:
                        ft_rect = self._normalize_rect(ft_annot.rect, page)
                        if _rects_overlap(ft_rect, cloud_rect, tolerance=80):
                            claimed = True
                            break
                    if claimed:
                        continue
                    action_type, confidence = AnnotationType.DELETE.value, 0.7
                    annotations.append(Annotation(
                        text="delete clouded objects",
                        target_bbox=cloud["rect"],
                        page=cloud["page"],
                        annot_type="Polygon",
                        arrow_vertices=cloud["vertices"],
                        inferred_action=action_type,
                        confidence=confidence,
                    ))

        doc.close()
        annotations.sort(key=lambda a: (a.page, a.target_bbox[1]))
        return [a.to_dict() for a in annotations]
