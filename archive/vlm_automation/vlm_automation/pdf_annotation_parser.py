#!/usr/bin/env python3
"""
PDF Annotation Parser for DWG markup workflow.

Extracts structured annotations from PDF markup files using PyMuPDF.
Produces a JSON list of actionable markups for the VLM agent.

Usage:
    python pdf_annotation_parser.py /path/to/markup.pdf
    python pdf_annotation_parser.py /path/to/markup.pdf --output markups.json
"""

import json
import sys
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum


try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. Install with: pip install pymupdf")
    sys.exit(1)


class AnnotationType(Enum):
    """Types of actionable annotations."""
    REPLACE = "replace"          # "Replace X with Y"
    MOVE = "move"                # "Move this to..."
    CHANGE_PROPERTY = "change_property"  # "Change color to...", "Change label to..."
    DELETE = "delete"            # "Remove this..."
    ADD = "add"                  # "Add a ..."
    REORDER = "reorder"          # "Move this row to second..."
    UNKNOWN = "unknown"


@dataclass
class Annotation:
    """Represents a single markup annotation."""
    text: str
    target_bbox: List[float]  # [x0, y0, x1, y1] in PDF coordinates
    page: int
    annot_type: str  # raw PDF annotation type (FreeText, Line, etc.)
    arrow_vertices: Optional[List[Tuple[float, float]]] = None
    author: str = ""
    inferred_action: str = "unknown"
    confidence: float = 0.0


def infer_action_type(text: str) -> Tuple[str, float]:
    """
    Infer the action type from annotation text.
    Returns (action_type, confidence).
    """
    text_lower = text.lower().strip()
    
    # Replace patterns
    replace_keywords = ['replace', 'swap', 'change to', 'use instead']
    if any(kw in text_lower for kw in replace_keywords):
        return (AnnotationType.REPLACE.value, 0.9)
    
    # Move patterns
    move_keywords = ['move', 'relocate', 'shift', 'position']
    if any(kw in text_lower for kw in move_keywords):
        return (AnnotationType.MOVE.value, 0.9)
    
    # Reorder patterns
    reorder_keywords = ['reorder', 'rearrange', 'move this row', 'move to row', 'following']
    if any(kw in text_lower for kw in reorder_keywords):
        return (AnnotationType.REORDER.value, 0.9)
    
    # Color/Property change
    color_keywords = ['change color', 'change to', 'make it', 'set color', 'change', 'blu to', 'wht to']
    if any(kw in text_lower for kw in color_keywords):
        return (AnnotationType.CHANGE_PROPERTY.value, 0.8)
    
    # Delete
    delete_keywords = ['delete', 'remove', 'erase', 'get rid of']
    if any(kw in text_lower for kw in delete_keywords):
        return (AnnotationType.DELETE.value, 0.9)
    
    # Add
    add_keywords = ['add', 'insert', 'create', 'draw']
    if any(kw in text_lower for kw in add_keywords):
        return (AnnotationType.ADD.value, 0.8)
    
    return (AnnotationType.UNKNOWN.value, 0.3)


def is_actionable(text: str) -> bool:
    """Check if annotation text describes an actionable change."""
    text_lower = text.lower().strip()
    
    # Skip metadata-only annotations
    skip_patterns = [
        'rev ', 'revision', 'reviewed by', 'approved by',
        'date:', 'project:', 'drawing no', 'sheet',
        'windsor plant', 'plant support'  # from your sample
    ]
    
    if any(pat in text_lower for pat in skip_patterns):
        # But check if it also contains actionable content
        action_keywords = ['replace', 'move', 'change', 'delete', 'add', 'reorder']
        has_action = any(kw in text_lower for kw in action_keywords)
        if not has_action:
            return False
    
    return True


def extract_pdf_annotations(pdf_path: str) -> List[Annotation]:
    """
    Extract all actionable annotations from a PDF markup file.
    
    Returns list of Annotation objects sorted by page and position.
    """
    annotations = []
    
    doc = fitz.open(pdf_path)
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_annots = list(page.annots())
        
        # Group annotations: FreeText + associated Line (arrow)
        # Typically: FreeText has the text, Line connects it to the target
        freetext_annots = []
        line_annots = []
        
        for annot in page_annots:
            annot_type = annot.type[1]  # e.g., 'FreeText', 'Line'
            if annot_type == 'FreeText':
                freetext_annots.append(annot)
            elif annot_type == 'Line':
                line_annots.append(annot)
        
        # Process each FreeText annotation
        for ft_annot in freetext_annots:
            text = ft_annot.info.get("content", "").strip()
            author = ft_annot.info.get("title", "")
            rect = ft_annot.rect
            
            # Skip non-actionable annotations
            if not is_actionable(text):
                continue
            
            # Try to find associated arrow line
            arrow_vertices = None
            for line_annot in line_annots:
                # Check if line is near the FreeText annotation
                line_rect = line_annot.rect
                if rects_overlap(rect, line_rect, tolerance=50):
                    if hasattr(line_annot, 'vertices') and line_annot.vertices:
                        arrow_vertices = line_annot.vertices
                    break
            
            # Determine target bbox (where the annotation points)
            # Use arrow end point if available, otherwise use FreeText rect center
            target_bbox = [rect.x0, rect.y0, rect.x1, rect.y1]
            if arrow_vertices and len(arrow_vertices) >= 2:
                # The last vertex is typically the arrow tip (target)
                tip = arrow_vertices[-1]
                # Create a small bbox around the tip
                target_bbox = [tip[0] - 20, tip[1] - 20, tip[0] + 20, tip[1] + 20]
            
            # Infer action type
            action_type, confidence = infer_action_type(text)
            
            annotation = Annotation(
                text=text,
                target_bbox=target_bbox,
                page=page_num,
                annot_type="FreeText",
                arrow_vertices=arrow_vertices,
                author=author,
                inferred_action=action_type,
                confidence=confidence
            )
            annotations.append(annotation)
    
    doc.close()
    
    # Sort by page, then by vertical position (top to bottom)
    annotations.sort(key=lambda a: (a.page, a.target_bbox[1]))
    
    return annotations


def rects_overlap(rect1: fitz.Rect, rect2: fitz.Rect, tolerance: float = 0) -> bool:
    """Check if two rectangles overlap (with optional tolerance)."""
    # Expand rect1 by tolerance
    r1 = fitz.Rect(
        rect1.x0 - tolerance,
        rect1.y0 - tolerance,
        rect1.x1 + tolerance,
        rect1.y1 + tolerance
    )
    # Check intersection
    intersect = r1.intersect(rect2)
    return intersect.width > 0 and intersect.height > 0


def export_to_json(annotations: List[Annotation], output_path: str):
    """Export annotations to JSON file."""
    data = {
        "source": "pdf_markup",
        "total_annotations": len(annotations),
        "annotations": [asdict(a) for a in annotations]
    }
    
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"Exported {len(annotations)} annotations to {output_path}")


def export_to_agent_tasks(annotations: List[Annotation], output_path: str):
    """
    Export as simplified agent tasks.
    Each task has the info the VLM agent needs.
    """
    tasks = []
    for i, annot in enumerate(annotations):
        task = {
            "task_id": i + 1,
            "instruction": annot.text,
            "action_type": annot.inferred_action,
            "pdf_target": {
                "page": annot.page,
                "bbox": annot.target_bbox
            },
            "confidence": annot.confidence
        }
        tasks.append(task)
    
    with open(output_path, 'w') as f:
        json.dump({"tasks": tasks}, f, indent=2)
    
    print(f"Exported {len(tasks)} agent tasks to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Parse PDF markup annotations for DWG editing"
    )
    parser.add_argument("pdf", help="Path to PDF markup file")
    parser.add_argument("--output", "-o", help="Output JSON file (detailed)")
    parser.add_argument("--tasks", "-t", help="Output agent tasks JSON (simplified)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each annotation")
    
    args = parser.parse_args()
    
    if not Path(args.pdf).exists():
        print(f"ERROR: File not found: {args.pdf}")
        sys.exit(1)
    
    print(f"Parsing annotations from: {args.pdf}")
    annotations = extract_pdf_annotations(args.pdf)
    
    if not annotations:
        print("No actionable annotations found.")
        print("Note: Annotations without action keywords (replace, move, change, etc.) are skipped.")
        sys.exit(0)
    
    print(f"\nFound {len(annotations)} actionable annotation(s):\n")
    
    for i, annot in enumerate(annotations, 1):
        print(f"[{i}] Page {annot.page + 1} | {annot.inferred_action.upper()}")
        print(f"    Text: \"{annot.text}\"")
        print(f"    Target: {annot.target_bbox}")
        if annot.arrow_vertices:
            print(f"    Arrow: {annot.arrow_vertices}")
        print(f"    Confidence: {annot.confidence:.0%}")
        print()
    
    # Summary by type
    from collections import Counter
    types = Counter(a.inferred_action for a in annotations)
    print("Summary by action type:")
    for action, count in types.most_common():
        print(f"  {action}: {count}")
    
    # Export if requested
    if args.output:
        export_to_json(annotations, args.output)
    
    if args.tasks:
        export_to_agent_tasks(annotations, args.tasks)


if __name__ == "__main__":
    main()
