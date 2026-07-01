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


def _rect_gap(rect1: fitz.Rect, rect2: fitz.Rect) -> float:
    """Minimum gap between two rects (0 if they overlap)."""
    dx = max(0, max(rect2.x0 - rect1.x1, rect1.x0 - rect2.x1))
    dy = max(0, max(rect2.y0 - rect1.y1, rect1.y0 - rect2.y1))
    return (dx * dx + dy * dy) ** 0.5


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

            # Prepare FreeText rects (normalized) for two-pass association.
            freetext_rects: List[Tuple[Any, fitz.Rect, str]] = []
            for ft_annot in freetext_annots:
                text = ft_annot.info.get("content", "").strip()
                rect = self._normalize_rect(ft_annot.rect, page)
                freetext_rects.append((ft_annot, rect, text))

            # FreeText→Polygon association.
            # Each FreeText independently matches to its nearest overlapping
            # polygon cloud.  Multiple FreeTexts can match the same cloud
            # (e.g. both "mark spare" and "delete clouded objects" can point
            # to the same strip cloud, producing both a mark_spare and a
            # delete task).
            #
            # Strategy: for each FreeText, find the nearest cloud by rect gap
            # within a 50pt tolerance.  Direct overlaps (gap=0) are preferred
            # but not exclusive — a FreeText that directly overlaps a cloud
            # always matches it; a FreeText that doesn't overlap any cloud
            # matches the nearest one within 50pt.
            ft_to_cloud: Dict[int, Optional[List[Tuple[float, float]]]] = {}
            # Track which clouds have at least one FreeText match (to avoid
            # emitting standalone annotations for them).
            matched_cloud_indices: set = set()

            for ft_idx, (ft_annot, rect, text) in enumerate(freetext_rects):
                if not _is_actionable(text):
                    ft_to_cloud[ft_idx] = None
                    continue
                best_gap = float("inf")
                best_c_idx = None
                best_verts = None
                for c_idx, cloud in enumerate(all_clouds):
                    cloud_rect = fitz.Rect(*cloud["rect"])
                    gap = _rect_gap(rect, cloud_rect)
                    # Direct overlap (gap=0) or within 50pt
                    if gap == 0 or gap <= 50:
                        if gap < best_gap:
                            best_gap = gap
                            best_c_idx = c_idx
                            best_verts = cloud["vertices"]
                if best_verts is not None:
                    ft_to_cloud[ft_idx] = best_verts
                    matched_cloud_indices.add(best_c_idx)

            # Fallback to line annotations for FreeTexts with no polygon cloud.
            # Multiple overlapping Line annotations (e.g. scratch marks forming
            # an X) are combined into a bbox-derived polygon.
            for ft_idx, (ft_annot, rect, text) in enumerate(freetext_rects):
                cloud_vertices = ft_to_cloud.get(ft_idx)
                if cloud_vertices is not None:
                    continue  # already matched to a polygon
                if not _is_actionable(text):
                    continue

                matched_verts: List[Tuple[float, float]] = []
                for line_annot in line_annots:
                    line_rect = self._normalize_rect(line_annot.rect, page)
                    if _rects_overlap(rect, line_rect, tolerance=50):
                        if hasattr(line_annot, "vertices") and line_annot.vertices:
                            matched_verts.extend(
                                self._normalize_vertices(
                                    list(line_annot.vertices), page))
                if len(matched_verts) >= 2:
                    xs = [v[0] for v in matched_verts]
                    ys = [v[1] for v in matched_verts]
                    x0, x1 = min(xs), max(xs)
                    y0, y1 = min(ys), max(ys)
                    if len(matched_verts) == 2:
                        dx = x1 - x0
                        dy = y1 - y0
                        length = max(abs(dx), abs(dy), 1)
                        px = -dy / length * 5
                        py = dx / length * 5
                        cloud_vertices = [
                            (x0 + px, y0 + py),
                            (x0 - px, y0 - py),
                            (x1 - px, y1 - py),
                            (x1 + px, y1 + py),
                        ]
                    else:
                        cloud_vertices = [
                            (x0, y0), (x1, y0), (x1, y1), (x0, y1),
                        ]
                    ft_to_cloud[ft_idx] = cloud_vertices

            # Emit annotations for each FreeText
            for ft_idx, (ft_annot, rect, text) in enumerate(freetext_rects):
                if not _is_actionable(text):
                    continue
                cloud_vertices = ft_to_cloud.get(ft_idx)
                author = ft_annot.info.get("title", "")

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

            # Also emit standalone polygon clouds that look like deletion clouds.
            # A polygon is "matched" if at least one FreeText was associated
            # with it above.  Unmatched polygons get standalone delete annotations.
            for c_idx, cloud in enumerate(all_clouds):
                if c_idx in matched_cloud_indices:
                    continue
                if cloud["text"]:
                    continue  # has its own text content
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
