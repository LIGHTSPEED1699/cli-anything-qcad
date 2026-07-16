#!/usr/bin/env python3
"""
DXF Action Pipeline: Orchestrates PDF parse → DXF lookup → coordinate transform → action execution.

Usage:
    python dxf_action_pipeline.py \
        --pdf /path/to/markup.pdf \
        --dwg /path/to/drawing.dwg \
        --dxf /path/to/exported.dxf \
        --dry-run

Architecture:
    1. Parse PDF annotations
    2. For each annotation:
       a. Extract target text
       b. Search DXF entity index for matching text
       c. Convert DXF coordinates to screen pixels via calibration
       d. Execute action in QCAD via X11
    3. Report results
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict

sys.path.insert(0, str(Path(__file__).parent))

from pdf_annotation_parser import extract_pdf_annotations, Annotation
from dxf_entity_lookup import DxfEntityIndex
from annotation_matcher import AnnotationMatcher, MatchResult
from coordinate_transformer import CoordinateTransformer
from x11_controller import X11Controller


@dataclass
class PipelineResult:
    """Result of processing a single annotation."""
    task_id: int
    annotation_text: str
    action_type: str
    target_text: Optional[str]
    success: bool
    error: Optional[str] = None
    dxf_coords: Optional[Tuple[float, float]] = None
    screen_coords: Optional[Tuple[int, int]] = None
    entity_handle: Optional[str] = None
    entity_type: Optional[str] = None
    entity_text: Optional[str] = None
    match_confidence: float = 0.0
    duration_seconds: float = 0.0


class DXFActionPipeline:
    """Orchestrates the full PDF → DXF → screen → action pipeline."""

    def __init__(
        self,
        dxf_path: str,
        window_name: str = "QCAD",
        dry_run: bool = False,
        step_delay: float = 0.5,
    ):
        self.dxf_path = dxf_path
        self.window_name = window_name
        self.dry_run = dry_run
        self.step_delay = step_delay

        # Initialize components
        print("Initializing pipeline...")
        self.matcher = AnnotationMatcher(dxf_path)
        self.transformer = CoordinateTransformer(dxf_path, window_name)
        self.x11 = X11Controller()

        self.calibrated = False

    def calibrate(self) -> bool:
        """Calibrate the coordinate transformer."""
        print("\nCalibrating coordinate transformer...")
        success = self.transformer.calibrate(min_matches=2)
        if success:
            self.calibrated = True
            print("Calibration successful!")
        else:
            print("Calibration failed - will attempt fallback method")
        return success

    def process_annotation(self, annotation: Annotation, task_id: int) -> PipelineResult:
        """Process a single annotation through the full pipeline."""
        start_time = time.time()
        print(f"\n{'='*60}")
        print(f"Task {task_id}: {annotation.text}")
        print(f"  Action type: {annotation.inferred_action}")
        print(f"  PDF target: {annotation.target_bbox}")

        try:
            # Step 1: Match annotation to DXF entity
            print("\nStep 1: Matching annotation to DXF entity...")
            match = self.matcher.match(annotation.text, use_fuzzy=True)

            if not match.matched_entity:
                error = f"No DXF entity found for target '{match.extracted_target}'"
                print(f"  ✗ {error}")
                return PipelineResult(
                    task_id=task_id,
                    annotation_text=annotation.text,
                    action_type=annotation.inferred_action,
                    target_text=match.extracted_target,
                    success=False,
                    error=error,
                    duration_seconds=time.time() - start_time
                )

            entity = match.matched_entity
            print(f"  ✓ Found: [{entity.handle}] {entity.entity_type}: \"{entity.text}\"")
            print(f"    DXF Coords: ({entity.insertion_point[0]:.4f}, {entity.insertion_point[1]:.4f})")
            print(f"    Match confidence: {match.confidence:.2f}")

            # Step 2: Convert DXF coordinates to screen pixels
            print("\nStep 2: Converting DXF → screen coordinates...")

            if self.calibrated:
                screen_x, screen_y = self.transformer.dxf_to_screen(
                    entity.insertion_point[0], entity.insertion_point[1]
                )
                print(f"  ✓ Screen: ({screen_x}, {screen_y})")
            else:
                # Fallback: use PDF annotation bbox as rough guide
                # This is much less accurate but better than nothing
                print("  ⚠ Not calibrated - using fallback (less accurate)")
                bbox = annotation.target_bbox
                # Rough center of annotation target area in PDF
                # We'll need to map PDF coordinates to screen somehow
                # For now, just use center of bbox scaled to screen
                screen_x = int((bbox[0] + bbox[2]) / 2)
                screen_y = int((bbox[1] + bbox[3]) / 2)
                print(f"  ⚠ Fallback screen: ({screen_x}, {screen_y}) [from PDF bbox]")

            # Step 3: Move mouse to verify coordinate accuracy
            print("\nStep 3: Moving mouse to verify accuracy...")

            if self.dry_run:
                print(f"  [DRY RUN] Would move mouse to ({screen_x}, {screen_y})")
            else:
                if not self.transformer.window_id:
                    self.transformer.find_window()

                if self.transformer.window_id:
                    # Get window position to compute absolute coords
                    geom = self.x11.get_window_geometry(self.transformer.window_id)
                    abs_x = geom['x'] + screen_x
                    abs_y = geom['y'] + screen_y

                    # Ensure mouse is within screen bounds
                    screen = self.x11.disp.screen()
                    screen_w = screen.width_in_pixels
                    screen_h = screen.height_in_pixels
                    abs_x = max(0, min(abs_x, screen_w - 1))
                    abs_y = max(0, min(abs_y, screen_h - 1))

                    self.x11.move_mouse(abs_x, abs_y)
                    print(f"  ✓ Mouse moved to absolute ({abs_x}, {abs_y})")
                    time.sleep(self.step_delay)
                else:
                    print("  ✗ QCAD window not found, cannot move mouse")
                    return PipelineResult(
                        task_id=task_id,
                        annotation_text=annotation.text,
                        action_type=annotation.inferred_action,
                        target_text=match.extracted_target,
                        success=False,
                        error="QCAD window not found",
                        dxf_coords=entity.insertion_point,
                        screen_coords=(screen_x, screen_y),
                        entity_handle=entity.handle,
                        entity_type=entity.entity_type,
                        entity_text=entity.text,
                        match_confidence=match.confidence,
                        duration_seconds=time.time() - start_time
                    )

            return PipelineResult(
                task_id=task_id,
                annotation_text=annotation.text,
                action_type=annotation.inferred_action,
                target_text=match.extracted_target,
                success=True,
                dxf_coords=entity.insertion_point,
                screen_coords=(screen_x, screen_y),
                entity_handle=entity.handle,
                entity_type=entity.entity_type,
                entity_text=entity.text,
                match_confidence=match.confidence,
                duration_seconds=time.time() - start_time
            )

        except Exception as e:
            print(f"  ✗ Pipeline error: {e}")
            import traceback
            traceback.print_exc()
            return PipelineResult(
                task_id=task_id,
                annotation_text=annotation.text,
                action_type=annotation.inferred_action,
                target_text=None,
                success=False,
                error=str(e),
                duration_seconds=time.time() - start_time
            )

    def run(self, pdf_path: str) -> List[PipelineResult]:
        """Run the full pipeline on a PDF."""
        print("=" * 60)
        print("DXF Action Pipeline Starting")
        print(f"  PDF: {pdf_path}")
        print(f"  DXF: {self.dxf_path}")
        print(f"  Dry run: {self.dry_run}")
        print("=" * 60)

        # 1. Calibrate
        self.calibrate()

        # 2. Parse PDF annotations
        print("\nParsing PDF annotations...")
        annotations = extract_pdf_annotations(pdf_path)
        if not annotations:
            print("No actionable annotations found.")
            return []
        print(f"Found {len(annotations)} annotation(s)")

        # 3. Process each annotation
        results = []
        for i, annot in enumerate(annotations):
            result = self.process_annotation(annot, i + 1)
            results.append(result)

        # 4. Report
        self.print_report(results)

        return results

    def print_report(self, results: List[PipelineResult]):
        """Print final report."""
        print("\n" + "=" * 60)
        print("PIPELINE REPORT")
        print("=" * 60)

        success_count = sum(1 for r in results if r.success)
        total = len(results)

        print(f"\nSuccess: {success_count}/{total} ({success_count/total*100:.0f}%)")
        print(f"Failed: {total - success_count}")
        print(f"Total time: {sum(r.duration_seconds for r in results):.1f}s")
        print()

        for r in results:
            status = "✅" if r.success else "❌"
            print(f"{status} Task {r.task_id}: {r.annotation_text[:60]}...")
            print(f"   Target: {r.target_text}")
            print(f"   Entity: {r.entity_type} [{r.entity_handle}]")
            print(f"   DXF: {r.dxf_coords}")
            print(f"   Screen: {r.screen_coords}")
            print(f"   Confidence: {r.match_confidence:.2f}")
            if r.error:
                print(f"   Error: {r.error}")
            print(f"   Time: {r.duration_seconds:.1f}s")
            print()

    def close(self):
        """Clean up resources."""
        if self.transformer:
            self.transformer.close()
        if self.x11:
            self.x11.close()


def main():
    parser = argparse.ArgumentParser(
        description="DXF Action Pipeline: PDF → DXF → Screen → Action"
    )
    parser.add_argument("--pdf", required=True, help="Path to PDF markup file")
    parser.add_argument("--dxf", required=True, help="Path to DXF file")
    parser.add_argument("--window-name", default="QCAD", help="QCAD window name")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without executing actions")
    parser.add_argument("--step-delay", type=float, default=0.5, help="Delay between steps")
    parser.add_argument("--report", "-r", help="Save JSON report to file")

    args = parser.parse_args()

    for path in [args.pdf, args.dxf]:
        if not Path(path).exists():
            print(f"ERROR: File not found: {path}")
            sys.exit(1)

    pipeline = DXFActionPipeline(
        dxf_path=args.dxf,
        window_name=args.window_name,
        dry_run=args.dry_run,
        step_delay=args.step_delay,
    )

    try:
        results = pipeline.run(args.pdf)

        if args.report and results:
            report = {
                "source_pdf": args.pdf,
                "source_dxf": args.dxf,
                "total_tasks": len(results),
                "successful": sum(1 for r in results if r.success),
                "failed": sum(1 for r in results if not r.success),
                "results": [asdict(r) for r in results]
            }
            with open(args.report, 'w') as f:
                json.dump(report, f, indent=2)
            print(f"\nReport saved to: {args.report}")

        sys.exit(0 if all(r.success for r in results) else 1)

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(130)
    finally:
        pipeline.close()


if __name__ == "__main__":
    main()
