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
    """Extract actionable annotations from a PDF markup file."""

    def parse(self, pdf_path: str) -> List[Dict[str, Any]]:
        annotations: List[Annotation] = []
        doc = fitz.open(pdf_path)

        for page_num in range(len(doc)):
            page = doc[page_num]
            freetext_annots, line_annots = [], []
            for annot in page.annots() or []:
                annot_type = annot.type[1]
                if annot_type == 'FreeText':
                    freetext_annots.append(annot)
                elif annot_type == 'Line':
                    line_annots.append(annot)

            for ft_annot in freetext_annots:
                text = ft_annot.info.get("content", "").strip()
                if not _is_actionable(text):
                    continue
                rect = ft_annot.rect
                author = ft_annot.info.get("title", "")

                arrow_vertices = None
                for line_annot in line_annots:
                    if _rects_overlap(rect, line_annot.rect, tolerance=50):
                        if hasattr(line_annot, 'vertices') and line_annot.vertices:
                            arrow_vertices = line_annot.vertices
                        break

                target_bbox = [rect.x0, rect.y0, rect.x1, rect.y1]
                if arrow_vertices and len(arrow_vertices) >= 2:
                    tip = arrow_vertices[-1]
                    target_bbox = [tip[0] - 20, tip[1] - 20, tip[0] + 20, tip[1] + 20]

                action_type, confidence = infer_action_type(text)
                annotations.append(Annotation(
                    text=text,
                    target_bbox=target_bbox,
                    page=page_num,
                    annot_type="FreeText",
                    arrow_vertices=arrow_vertices,
                    author=author,
                    inferred_action=action_type,
                    confidence=confidence,
                ))

        doc.close()
        annotations.sort(key=lambda a: (a.page, a.target_bbox[1]))
        return [a.to_dict() for a in annotations]
