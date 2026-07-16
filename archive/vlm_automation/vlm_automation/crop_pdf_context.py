#!/usr/bin/env python3
"""
PDF Context Cropper for DWG markup pipeline.

Crops PDF pages around annotation targets to create context images
for the VLM to identify corresponding entities in QCAD.

Usage:
    python crop_pdf_context.py /path/to/markup.pdf --tasks /path/to/tasks.json --outdir /tmp/context_images/
    python crop_pdf_context.py /path/to/markup.pdf --page 0 --bbox "491,199,531,239" --padding 100
"""

import json
import sys
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. Install with: pip install pymupdf")
    sys.exit(1)


def crop_around_bbox(
    pdf_path: str,
    page_num: int,
    bbox: List[float],
    padding: int = 100,
    zoom: float = 2.0,
    output_path: Optional[str] = None,
) -> str:
    """
    Crop a PDF page around a target bbox with padding.
    Returns path to saved image.
    """
    doc = fitz.open(pdf_path)
    page = doc[page_num]
    page_rect = page.rect

    # Expand bbox with padding
    x0 = max(0, bbox[0] - padding)
    y0 = max(0, bbox[1] - padding)
    x1 = min(page_rect.width, bbox[2] + padding)
    y1 = min(page_rect.height, bbox[3] + padding)

    crop_rect = fitz.Rect(x0, y0, x1, y1)

    # Render at higher resolution for VLM clarity
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, clip=crop_rect)

    if output_path is None:
        output_path = f"/tmp/pdf_context_p{page_num}_{int(bbox[0])}_{int(bbox[1])}.png"

    pix.save(output_path)
    doc.close()

    return output_path


def crop_from_tasks(pdf_path: str, tasks_path: str, outdir: str, padding: int = 100, zoom: float = 2.0) -> List[Dict[str, Any]]:
    """Crop context images for all tasks in a tasks.json file."""
    outdir_path = Path(outdir)
    outdir_path.mkdir(parents=True, exist_ok=True)

    with open(tasks_path) as f:
        data = json.load(f)

    tasks = data.get("tasks", [])
    results = []

    for task in tasks:
        task_id = task["task_id"]
        bbox = task["pdf_target"]["bbox"]
        page = task["pdf_target"]["page"]

        output_path = str(outdir_path / f"task_{task_id}_context.png")
        image_path = crop_around_bbox(pdf_path, page, bbox, padding, zoom, output_path)

        results.append({
            "task_id": task_id,
            "instruction": task["instruction"],
            "action_type": task["action_type"],
            "image_path": image_path,
            "page": page,
            "bbox": bbox,
        })
        print(f"  Task {task_id}: {image_path}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Crop PDF context images for VLM")
    parser.add_argument("pdf", help="Path to PDF markup file")
    parser.add_argument("--tasks", "-t", help="Path to agent_tasks.json (from pdf_annotation_parser)")
    parser.add_argument("--outdir", "-o", default="/tmp/pdf_contexts", help="Output directory")
    parser.add_argument("--page", type=int, help="Page number (0-indexed, for manual mode)")
    parser.add_argument("--bbox", help="Bounding box as x0,y0,x1,y1 (for manual mode)")
    parser.add_argument("--padding", "-p", type=int, default=100, help="Padding around target (default: 100)")
    parser.add_argument("--zoom", "-z", type=float, default=2.0, help="Render zoom factor (default: 2.0)")
    parser.add_argument("--output", help="Output image path (for manual mode)")

    args = parser.parse_args()

    if not Path(args.pdf).exists():
        print(f"ERROR: PDF not found: {args.pdf}")
        sys.exit(1)

    if args.tasks:
        # Batch mode from tasks.json
        if not Path(args.tasks).exists():
            print(f"ERROR: Tasks file not found: {args.tasks}")
            sys.exit(1)

        print(f"Cropping context images for tasks from: {args.tasks}")
        results = crop_from_tasks(args.pdf, args.tasks, args.outdir, args.padding, args.zoom)

        # Save manifest
        manifest_path = Path(args.outdir) / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump({"contexts": results}, f, indent=2)
        print(f"\nManifest saved to: {manifest_path}")
        print(f"Total images: {len(results)}")

    elif args.page is not None and args.bbox:
        # Manual single crop
        bbox = [float(x) for x in args.bbox.split(",")]
        image_path = crop_around_bbox(args.pdf, args.page, bbox, args.padding, args.zoom, args.output)
        print(f"Cropped image saved to: {image_path}")

    else:
        print("ERROR: Provide either --tasks (batch mode) or --page + --bbox (manual mode)")
        sys.exit(1)


if __name__ == "__main__":
    main()
