#!/usr/bin/env python3
"""
DWG Markup Pipeline: PDF Annotation → QCAD Modification via VLM

Usage:
    python dwg_markup_pipeline.py \
        --pdf /path/to/markup.pdf \
        --dwg /path/to/drawing.dwg \
        --output /path/to/output.dwg \
        --vision-model qwen2.5vl:latest \
        --text-model qwen3.5:9b

Architecture:
    1. PyMuPDF extracts structured annotations from PDF
    2. For each annotation:
       a. Crop PDF page to show annotation context
       b. Screenshot QCAD with DWG open
       c. VLM (qwen2.5vl) sees both images → identifies target entity + action
       d. X11 controller executes action in QCAD
    3. Save modified DWG
    4. Report success/failure per annotation
"""

import os
import sys
import json
import time
import base64
import shutil
import argparse
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict

sys.path.insert(0, str(Path(__file__).parent))

from pdf_annotation_parser import extract_pdf_annotations, Annotation
from x11_controller import X11Controller
from ollama_client import OllamaClient


@dataclass
class PipelineResult:
    """Result of processing a single annotation."""
    task_id: int
    annotation_text: str
    action_type: str
    success: bool
    error: Optional[str] = None
    qcad_coords: Optional[Tuple[int, int]] = None
    action_executed: Optional[str] = None
    duration_seconds: float = 0.0


class DWGMarkupPipeline:
    """Main pipeline orchestrating PDF → VLM → QCAD → DWG."""

    def __init__(
        self,
        vision_model: str = "qwen2.5vl:latest",
        text_model: str = "qwen3.5:9b",
        ollama_url: str = "http://localhost:11434",
        qcad_path: str = "/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad",
        window_name: str = "QCAD",
        max_steps: int = 10,
        step_delay: float = 2.0,
        dry_run: bool = False,
    ):
        self.vision_model = vision_model
        self.text_model = text_model
        self.ollama_url = ollama_url
        self.qcad_path = qcad_path
        self.window_name = window_name
        self.max_steps = max_steps
        self.step_delay = step_delay
        self.dry_run = dry_run

        self.x11 = X11Controller()
        self.ollama = OllamaClient(base_url=ollama_url)
        self.qcad_window_id: Optional[int] = None

    def launch_qcad(self, dwg_path: str) -> bool:
        """Launch QCAD with the DWG file."""
        print(f"🖥️  Launching QCAD with: {dwg_path}")

        # Check if QCAD is already running
        existing = self.x11.get_window_by_name(self.window_name)
        if existing:
            print(f"   QCAD already running (window ID: {existing})")
            self.qcad_window_id = existing
            self.x11.raise_window(existing)
            time.sleep(1)
            return True

        # Launch QCAD
        cmd = [self.qcad_path, dwg_path]
        print(f"   Command: {' '.join(cmd)}")

        if self.dry_run:
            print("   [DRY RUN] Would launch QCAD")
            return True

        try:
            # Launch in background
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=os.path.dirname(self.qcad_path),
            )

            # Wait for window to appear (up to 30s, accounting for trial delay)
            print("   Waiting for QCAD window...")
            for attempt in range(30):
                time.sleep(1)
                window = self.x11.get_window_by_name(self.window_name)
                if window:
                    self.qcad_window_id = window
                    print(f"   ✓ QCAD window found: {window}")
                    time.sleep(2)  # Let it fully load
                    return True
                print(f"   Attempt {attempt + 1}/30...")

            print("   ✗ Failed to find QCAD window")
            return False

        except Exception as e:
            print(f"   ✗ Error launching QCAD: {e}")
            return False

    def capture_qcad_screenshot(self) -> str:
        """Capture QCAD window screenshot, return path."""
        if not self.qcad_window_id:
            raise RuntimeError("QCAD window not available")

        screenshot_path = f"/tmp/qcad_screenshot_{int(time.time())}.png"
        self.x11.screenshot_window(self.qcad_window_id, screenshot_path)
        return screenshot_path

    def crop_pdf_context(self, pdf_path: str, page: int, target_bbox: List[float], 
                         padding: int = 100) -> str:
        """
        Crop PDF page around annotation target to create context image.
        Returns path to cropped image.
        """
        try:
            import fitz
        except ImportError:
            raise RuntimeError("PyMuPDF required for PDF cropping")

        doc = fitz.open(pdf_path)
        page_obj = doc[page]

        # Get page dimensions
        page_rect = page_obj.rect

        # Expand target bbox with padding
        x0 = max(0, target_bbox[0] - padding)
        y0 = max(0, target_bbox[1] - padding)
        x1 = min(page_rect.width, target_bbox[2] + padding)
        y1 = min(page_rect.height, target_bbox[3] + padding)

        crop_rect = fitz.Rect(x0, y0, x1, y1)

        # Render at higher resolution for VLM clarity
        zoom = 2.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page_obj.get_pixmap(matrix=mat, clip=crop_rect)

        output_path = f"/tmp/pdf_context_{page}_{int(time.time())}.png"
        pix.save(output_path)
        doc.close()

        return output_path

    def build_vlm_prompt(self, annotation: Annotation) -> str:
        """Build the prompt for the vision model."""
        prompt = f"""You are a CAD drawing assistant. You see two images:

LEFT IMAGE: A cropped view from the markup PDF showing an annotation arrow pointing to a specific entity.
RIGHT IMAGE: A screenshot of QCAD showing the same drawing.

TASK: "{annotation.text}"

Your job:
1. Identify which entity in the QCAD screenshot (RIGHT) corresponds to the one pointed to in the PDF markup (LEFT)
2. Determine what action to take based on the task text
3. Report the screen coordinates in QCAD where you need to click or interact

Return your response in this exact format:
TARGET_FOUND: yes/no
TARGET_DESCRIPTION: <what the entity looks like>
ACTION: <click|double_click|type|select|move|delete|done>
QCAD_COORDINATES: (x, y)  [screen coordinates in the QCAD screenshot]
TEXT_INPUT: <text to type if ACTION is type>
REASONING: <how you matched the entity and decided the action>

Important:
- Coordinates must be within the QCAD screenshot bounds
- Be precise — give the center of the entity to click
- If you cannot find the entity, say TARGET_FOUND: no and explain why
"""
        return prompt

    def query_vlm(self, pdf_image_path: str, qcad_image_path: str, 
                  annotation: Annotation) -> Dict[str, Any]:
        """
        Send both images to the VLM and parse the response.
        Returns dict with action details.
        """
        prompt = self.build_vlm_prompt(annotation)

        print(f"   📤 Sending to VLM ({self.vision_model})...")

        if self.dry_run:
            print("   [DRY RUN] Would query VLM")
            return {
                "target_found": True,
                "action": "click",
                "coordinates": (100, 100),
                "text_input": "",
                "reasoning": "Dry run"
            }

        # Build multi-image message for Ollama
        with open(pdf_image_path, 'rb') as f:
            pdf_b64 = base64.b64encode(f.read()).decode('utf-8')
        with open(qcad_image_path, 'rb') as f:
            qcad_b64 = base64.b64encode(f.read()).decode('utf-8')

        messages = [
            {
                "role": "user",
                "content": prompt,
                "images": [pdf_b64, qcad_b64]
            }
        ]

        try:
            result = self.ollama.chat(self.vision_model, messages, timeout=120)
            response_text = result.get('message', {}).get('content', '')
            return self.parse_vlm_response(response_text)
        except Exception as e:
            print(f"   ✗ VLM query failed: {e}")
            return {"target_found": False, "error": str(e)}

    def parse_vlm_response(self, response: str) -> Dict[str, Any]:
        """Parse VLM response into structured dict."""
        result = {
            "target_found": False,
            "target_description": "",
            "action": "",
            "coordinates": None,
            "text_input": "",
            "reasoning": ""
        }

        for line in response.strip().split("\n"):
            line = line.strip()
            if line.startswith("TARGET_FOUND:"):
                val = line[13:].strip().lower()
                result["target_found"] = val in ("yes", "true", "1")
            elif line.startswith("TARGET_DESCRIPTION:"):
                result["target_description"] = line[19:].strip()
            elif line.startswith("ACTION:"):
                result["action"] = line[7:].strip().lower()
            elif line.startswith("QCAD_COORDINATES:"):
                coord_str = line[17:].strip().strip("()")
                try:
                    x, y = map(int, coord_str.split(","))
                    result["coordinates"] = (x, y)
                except (ValueError, IndexError):
                    pass
            elif line.startswith("TEXT_INPUT:"):
                result["text_input"] = line[11:].strip()
            elif line.startswith("REASONING:"):
                result["reasoning"] = line[10:].strip()

        return result

    def execute_in_qcad(self, action: Dict[str, Any]) -> bool:
        """Execute the parsed action in QCAD via X11."""
        if not self.qcad_window_id:
            print("   ✗ No QCAD window")
            return False

        action_type = action.get("action", "")
        coords = action.get("coordinates")
        text = action.get("text_input", "")

        if action_type == "done":
            return True

        if not coords:
            print("   ✗ No coordinates provided")
            return False

        # Get window geometry for absolute coordinates
        geom = self.x11.get_window_geometry(self.qcad_window_id)
        abs_x = geom["x"] + coords[0]
        abs_y = geom["y"] + coords[1]

        print(f"   🖱️  Action: {action_type} at QCAD coords {coords} (absolute: {abs_x}, {abs_y})")

        if self.dry_run:
            print("   [DRY RUN] Would execute action")
            return True

        try:
            if action_type == "click":
                self.x11.click(abs_x, abs_y)
            elif action_type == "double_click":
                self.x11.double_click(abs_x, abs_y)
            elif action_type == "type":
                self.x11.click(abs_x, abs_y)
                time.sleep(0.2)
                self.x11.type_text(text)
            elif action_type == "select":
                self.x11.click(abs_x, abs_y)
                # Could add shift+click for multi-select
            elif action_type == "move":
                # Would need drag operation
                print("   ⚠️ Move action requires drag — not fully implemented")
                return False
            elif action_type == "delete":
                self.x11.click(abs_x, abs_y)
                time.sleep(0.2)
                self.x11.key_press("delete")
            else:
                print(f"   ⚠️ Unknown action: {action_type}")
                return False

            time.sleep(self.step_delay)
            return True

        except Exception as e:
            print(f"   ✗ Action failed: {e}")
            return False

    def save_dwg(self, output_path: str) -> bool:
        """Save the DWG in QCAD using Ctrl+S."""
        if not self.qcad_window_id or self.dry_run:
            return True

        print("💾 Saving DWG (Ctrl+S)...")
        self.x11.key_press("ctrl+s")
        time.sleep(1)

        # Check if save dialog appears (first-time save or different path)
        # This is a simplification — real implementation might need dialog handling
        print("   ✓ Save triggered (check QCAD for save dialog)")
        return True

    def process_annotation(self, annotation: Annotation, pdf_path: str, 
                          task_id: int) -> PipelineResult:
        """Process a single annotation through the full pipeline."""
        start_time = time.time()
        print(f"\n{'='*60}")
        print(f"📋 Task {task_id}: {annotation.text}")
        print(f"   Action type: {annotation.inferred_action}")
        print(f"   PDF page: {annotation.page + 1}, target: {annotation.target_bbox}")

        try:
            # 1. Crop PDF context
            print("   📄 Cropping PDF context...")
            pdf_image = self.crop_pdf_context(
                pdf_path, annotation.page, annotation.target_bbox
            )
            print(f"   ✓ PDF context: {pdf_image}")

            # 2. Capture QCAD screenshot
            print("   📸 Capturing QCAD...")
            qcad_image = self.capture_qcad_screenshot()
            print(f"   ✓ QCAD screenshot: {qcad_image}")

            # 3. Query VLM
            vlm_result = self.query_vlm(pdf_image, qcad_image, annotation)

            if not vlm_result.get("target_found"):
                error = vlm_result.get("error", "Target not found")
                print(f"   ✗ {error}")
                return PipelineResult(
                    task_id=task_id,
                    annotation_text=annotation.text,
                    action_type=annotation.inferred_action,
                    success=False,
                    error=error,
                    duration_seconds=time.time() - start_time
                )

            print(f"   ✓ VLM found target: {vlm_result.get('target_description', 'N/A')}")
            print(f"   🎯 Coordinates: {vlm_result.get('coordinates')}")
            print(f"   📝 Action: {vlm_result.get('action')}")

            # 4. Execute in QCAD
            success = self.execute_in_qcad(vlm_result)

            return PipelineResult(
                task_id=task_id,
                annotation_text=annotation.text,
                action_type=annotation.inferred_action,
                success=success,
                qcad_coords=vlm_result.get("coordinates"),
                action_executed=vlm_result.get("action"),
                duration_seconds=time.time() - start_time
            )

        except Exception as e:
            print(f"   ✗ Pipeline error: {e}")
            import traceback
            traceback.print_exc()
            return PipelineResult(
                task_id=task_id,
                annotation_text=annotation.text,
                action_type=annotation.inferred_action,
                success=False,
                error=str(e),
                duration_seconds=time.time() - start_time
            )

    def run(self, pdf_path: str, dwg_path: str, output_path: str) -> List[PipelineResult]:
        """
        Run the full pipeline.
        Returns list of results per annotation.
        """
        print("=" * 60)
        print("🚀 DWG Markup Pipeline Starting")
        print(f"   PDF: {pdf_path}")
        print(f"   DWG: {dwg_path}")
        print(f"   Output: {output_path}")
        print(f"   Vision Model: {self.vision_model}")
        print(f"   Text Model: {self.text_model}")
        print(f"   Dry Run: {self.dry_run}")
        print("=" * 60)

        # 1. Parse PDF annotations
        print("\n📖 Parsing PDF annotations...")
        annotations = extract_pdf_annotations(pdf_path)
        if not annotations:
            print("No actionable annotations found.")
            return []
        print(f"   ✓ Found {len(annotations)} annotation(s)")

        # 2. Launch QCAD
        if not self.launch_qcad(dwg_path):
            print("✗ Failed to launch QCAD")
            return []

        # 3. Process each annotation
        results = []
        for i, annot in enumerate(annotations):
            result = self.process_annotation(annot, pdf_path, i + 1)
            results.append(result)

        # 4. Save output
        if output_path != dwg_path:
            # Use Save As via QCAD — this is complex via X11
            # Simplified: just trigger save and let user handle path
            print(f"\n💾 Saving to: {output_path}")
            # Copy if same-session save not possible
            if os.path.exists(dwg_path):
                shutil.copy2(dwg_path, output_path)
                print(f"   ✓ Copied {dwg_path} → {output_path}")

        self.save_dwg(output_path)

        # 5. Report
        self.print_report(results)

        return results

    def print_report(self, results: List[PipelineResult]):
        """Print final report."""
        print("\n" + "=" * 60)
        print("📊 PIPELINE REPORT")
        print("=" * 60)

        success_count = sum(1 for r in results if r.success)
        total = len(results)

        print(f"\nSuccess: {success_count}/{total} ({success_count/total*100:.0f}%)")
        print(f"Failed: {total - success_count}")
        print(f"Total time: {sum(r.duration_seconds for r in results):.1f}s")
        print()

        for r in results:
            status = "✅" if r.success else "❌"
            print(f"{status} Task {r.task_id}: {r.annotation_text[:50]}...")
            print(f"   Action: {r.action_type} → {r.action_executed}")
            if r.qcad_coords:
                print(f"   Coords: {r.qcad_coords}")
            if r.error:
                print(f"   Error: {r.error}")
            print(f"   Time: {r.duration_seconds:.1f}s")
            print()

    def cleanup(self):
        """Clean up resources."""
        if self.x11:
            self.x11.close()


def main():
    parser = argparse.ArgumentParser(
        description="DWG Markup Pipeline: PDF annotations → QCAD modifications"
    )
    parser.add_argument("--pdf", required=True, help="Path to PDF markup file")
    parser.add_argument("--dwg", required=True, help="Path to input DWG file")
    parser.add_argument("--output", "-o", required=True, help="Path to output DWG file")
    parser.add_argument("--vision-model", default="qwen2.5vl:latest",
                        help="VLM model for visual matching (default: qwen2.5vl:latest)")
    parser.add_argument("--text-model", default="qwen3.5:9b",
                        help="Text model for planning (default: qwen3.5:9b)")
    parser.add_argument("--ollama-url", default="http://localhost:11434",
                        help="Ollama API URL")
    parser.add_argument("--qcad-path",
                        default="/home/hongbin/opt/qcad-3.32.6-trial-linux-qt5.14-x86_64/qcad",
                        help="Path to QCAD executable")
    parser.add_argument("--window-name", default="QCAD",
                        help="Window name to search for")
    parser.add_argument("--max-steps", type=int, default=10,
                        help="Max steps per annotation")
    parser.add_argument("--step-delay", type=float, default=2.0,
                        help="Delay between steps (seconds)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate without executing actions")
    parser.add_argument("--report", "-r",
                        help="Save JSON report to file")

    args = parser.parse_args()

    # Validate files
    for path in [args.pdf, args.dwg]:
        if not Path(path).exists():
            print(f"ERROR: File not found: {path}")
            sys.exit(1)

    # Create output directory
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    # Run pipeline
    pipeline = DWGMarkupPipeline(
        vision_model=args.vision_model,
        text_model=args.text_model,
        ollama_url=args.ollama_url,
        qcad_path=args.qcad_path,
        window_name=args.window_name,
        max_steps=args.max_steps,
        step_delay=args.step_delay,
        dry_run=args.dry_run,
    )

    try:
        results = pipeline.run(args.pdf, args.dwg, args.output)

        # Save JSON report if requested
        if args.report and results:
            report = {
                "source_pdf": args.pdf,
                "source_dwg": args.dwg,
                "output_dwg": args.output,
                "total_tasks": len(results),
                "successful": sum(1 for r in results if r.success),
                "failed": sum(1 for r in results if not r.success),
                "results": [asdict(r) for r in results]
            }
            with open(args.report, 'w') as f:
                json.dump(report, f, indent=2)
            print(f"\n📄 Report saved to: {args.report}")

        # Exit code
        sys.exit(0 if all(r.success for r in results) else 1)

    except KeyboardInterrupt:
        print("\n⚠️ Interrupted by user")
        sys.exit(130)
    finally:
        pipeline.cleanup()


if __name__ == "__main__":
    main()
