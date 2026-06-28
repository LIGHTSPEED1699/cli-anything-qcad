#!/usr/bin/env python3
"""
QCAD Screenshot + VLM Entity Matcher

Captures QCAD window screenshot and sends it to the VLM
along with a PDF context image, to identify the target entity's
screen coordinates in QCAD.

Usage:
    python qcad_vlm_match.py --pdf-image /tmp/task_1_context.png --prompt "Replace NT111 with NT-110"
    python qcad_vlm_match.py --window-id 12345678 --pdf-image /tmp/task_1_context.png --prompt "Find the NT111 block"
    python qcad_vlm_match.py --tasks /tmp/pdf_contexts/manifest.json --report /tmp/vlm_matches.json
"""

import os
import sys
import json
import base64
import time
import argparse
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

sys.path.insert(0, str(Path(__file__).parent))

from x11_controller import X11Controller
from ollama_client import OllamaClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Fallback chain: gemma4:31b-cloud (best for vision) -> qwen2.5vl (local native JSON) -> gemma4:e4b (local last resort)
# Updated 2026-06-04 after F174 benchmark showed gemma4:31b-cloud is 12× faster and less hallucinatory
# than qwen2.5vl for technical drawing verification.
DEFAULT_FALLBACK_CHAIN: List[str] = ["gemma4:31b-cloud", "qwen2.5vl:latest", "gemma4:e4b"]
DEFAULT_TIMEOUT: float = 120.0


class QCADVLMMatcher:
    """Matches PDF annotation targets to QCAD screen coordinates via VLM."""

    def __init__(
        self,
        vision_model: Optional[str] = None,
        fallback_chain: Optional[List[str]] = None,
        timeout: float = DEFAULT_TIMEOUT,
        ollama_url: str = "http://localhost:11434",
        qcad_window_name: str = "QCAD",
    ):
        self._preferred = vision_model
        self.fallback_chain = fallback_chain or DEFAULT_FALLBACK_CHAIN.copy()
        self.timeout = timeout
        self.ollama_url = ollama_url
        self.ollama = OllamaClient(base_url=ollama_url, timeout=timeout)
        self.x11 = X11Controller()
        self.qcad_window_name = qcad_window_name

        # Build effective chain: preferred first, then chain minus duplicates
        effective = []
        if self._preferred:
            effective.append(self._preferred)
        for m in self.fallback_chain:
            if m not in effective:
                effective.append(m)
        self.effective_chain = effective
        logger.info("VLM matcher chain: %s (timeout=%.0fs)", self.effective_chain, self.timeout)

    @property
    def vision_model(self) -> str:
        return self.effective_chain[0] if self.effective_chain else "gemma4:e4b"

    def find_qcad_window(self) -> Optional[int]:
        """Find QCAD window ID."""
        wid = self.x11.get_window_by_name(self.qcad_window_name)
        if wid:
            return wid
        # Try common variations
        for name in ["QCAD Professional", "QCAD Trial", "QCAD 3", "RivieraWaves"]:
            wid = self.x11.get_window_by_name(name)
            if wid:
                return wid
        return None

    def capture_qcad(self, window_id: Optional[int] = None) -> str:
        """Capture QCAD screenshot, return path."""
        if window_id is None:
            window_id = self.find_qcad_window()
            if not window_id:
                raise RuntimeError("QCAD window not found")

        output_path = f"/tmp/qcad_screenshot_{int(time.time())}.png"
        self.x11.screenshot_window(window_id, output_path)
        logger.info("Captured QCAD screenshot: %s", output_path)
        return output_path

    def build_match_prompt(self, instruction: str) -> str:
        """Build the VLM prompt for entity matching."""
        return f"""You are a CAD drawing assistant. You see two images:

LEFT IMAGE: A cropped view from the markup PDF showing an annotation and its target area.
RIGHT IMAGE: A screenshot of QCAD showing the same drawing opened.

TASK: "{instruction}"

Your job:
1. Look at the LEFT image — identify what entity the annotation is pointing to (a block, label, row, component, etc.)
2. Look at the RIGHT image — find that SAME entity in the QCAD view
3. Report the screen coordinates where you need to click on that entity

Return your response in this EXACT format:
TARGET_FOUND: yes/no
ENTITY_TYPE: <block|text|line|row|label|other>
ENTITY_DESCRIPTION: <what it looks like, 10 words max>
QCAD_COORDINATES: (x, y)
CONFIDENCE: <high|medium|low>
REASONING: <brief explanation>

Important:
- Coordinates are within the QCAD screenshot (0,0 is top-left)
- Give the CENTER of the entity to click
- If not found, say TARGET_FOUND: no and explain why
"""

    def _validate_coordinates(self, coords: Optional[Tuple[int, int]], image_path: str) -> bool:
        """Validate that coordinates are within image bounds."""
        if coords is None:
            return False
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                w, h = img.size
            x, y = coords
            if not (0 <= x <= w and 0 <= y <= h):
                logger.warning("Coordinates %s outside image bounds (%s, %s)", coords, w, h)
                return False
            return True
        except Exception as e:
            logger.warning("Could not validate coordinates: %s", e)
            return True  # be permissive if PIL unavailable

    def _query_with_fallback(
        self,
        messages: List[Dict[str, Any]],
    ) -> Tuple[Optional[str], Optional[str], float, int]:
        """
        Try each model in the fallback chain.
        Returns (response_text, model_used, duration_sec, attempts).
        """
        last_error = None
        for attempt, model in enumerate(self.effective_chain, start=1):
            logger.info("  [attempt %d/%d] Querying VLM (%s)...", attempt, len(self.effective_chain), model)
            start = time.time()
            try:
                result = self.ollama.chat(model, messages, timeout=self.timeout)
                response_text = result.get('message', {}).get('content', '')
                duration = time.time() - start
                logger.info("  ✓ VLM (%s) responded in %.1fs", model, duration)
                return response_text, model, duration, attempt
            except Exception as e:
                duration = time.time() - start
                last_error = e
                logger.warning("  ✗ VLM (%s) failed after %.1fs: %s", model, duration, e)

        logger.error("All models in fallback chain exhausted. Last error: %s", last_error)
        return None, None, 0.0, len(self.effective_chain)

    def match_entity(
        self,
        pdf_image_path: str,
        qcad_image_path: Optional[str] = None,
        instruction: str = "",
        window_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send PDF context + QCAD screenshot to VLM for entity matching.
        Returns parsed result dict.
        """
        # Capture QCAD if not provided
        if qcad_image_path is None:
            qcad_image_path = self.capture_qcad(window_id)

        prompt = self.build_match_prompt(instruction)

        # Build multi-image message for Ollama
        logger.info("Encoding images: pdf=%s qcad=%s", pdf_image_path, qcad_image_path)
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

        response_text, model_used, duration, attempts = self._query_with_fallback(messages)

        if response_text is None:
            return {
                "target_found": False,
                "error": f"All VLM models failed after {attempts} attempts. Last error: {str(last_error)}",
                "qcad_screenshot": qcad_image_path,
                "duration_sec": duration,
                "attempts": attempts,
            }

        parsed = self._parse_response(response_text, qcad_image_path)
        parsed["model_used"] = model_used
        parsed["duration_sec"] = duration
        parsed["attempts"] = attempts

        # Validate coordinates if claimed found
        if parsed.get("target_found") and parsed.get("coordinates"):
            if not self._validate_coordinates(parsed["coordinates"], qcad_image_path):
                logger.warning("  ⚠️ Invalid coordinates from VLM, marking as not found")
                parsed["target_found"] = False
                parsed["validation_error"] = f"Coordinates {parsed['coordinates']} outside image bounds"

        return parsed

    def _parse_response(self, response: str, qcad_image_path: str) -> Dict[str, Any]:
        """Parse VLM response into structured dict."""
        result = {
            "target_found": False,
            "entity_type": "",
            "entity_description": "",
            "coordinates": None,
            "confidence": "",
            "reasoning": "",
            "qcad_screenshot": qcad_image_path,
            "raw_response": response,
        }

        for line in response.strip().split("\n"):
            line = line.strip()
            if line.startswith("TARGET_FOUND:"):
                val = line[13:].strip().lower()
                result["target_found"] = val in ("yes", "true", "1")
            elif line.startswith("ENTITY_TYPE:"):
                result["entity_type"] = line[12:].strip()
            elif line.startswith("ENTITY_DESCRIPTION:"):
                result["entity_description"] = line[19:].strip()
            elif line.startswith("QCAD_COORDINATES:"):
                coord_str = line[17:].strip().strip("()")
                try:
                    x_str, y_str = coord_str.split(",", 1)
                    x = int(x_str.strip().replace("\"", ""))
                    y = int(y_str.strip().replace("\"", ""))
                    result["coordinates"] = (x, y)
                except (ValueError, IndexError):
                    pass
            elif line.startswith("CONFIDENCE:"):
                result["confidence"] = line[11:].strip().lower()
            elif line.startswith("REASONING:"):
                result["reasoning"] = line[10:].strip()

        return result

    def process_manifest(self, manifest_path: str, report_path: Optional[str] = None) -> Dict[str, Any]:
        """Process all tasks from a context manifest."""
        with open(manifest_path) as f:
            manifest = json.load(f)

        contexts = manifest.get("contexts", [])
        results = []

        logger.info("Processing %d tasks from manifest...", len(contexts))
        print("=" * 60)

        # Capture QCAD once for all tasks
        qcad_image = self.capture_qcad()
        logger.info("QCAD screenshot: %s", qcad_image)
        print()

        for ctx in contexts:
            task_id = ctx["task_id"]
            instruction = ctx["instruction"]
            pdf_image = ctx["image_path"]

            print(f"[{task_id}] {instruction}")
            match_result = self.match_entity(
                pdf_image_path=pdf_image,
                qcad_image_path=qcad_image,
                instruction=instruction,
            )

            result = {
                "task_id": task_id,
                "instruction": instruction,
                "action_type": ctx["action_type"],
                **match_result,
            }
            results.append(result)

            if match_result.get("target_found"):
                coords = match_result.get("coordinates")
                conf = match_result.get("confidence", "unknown")
                desc = match_result.get("entity_description", "")
                model_used = match_result.get("model_used", "unknown")
                print(f"  ✅ Found: {desc} at {coords} (confidence: {conf}, model: {model_used})")
            else:
                error = match_result.get("error", match_result.get("reasoning", "No reason given"))
                print(f"  ❌ Not found: {error}")
            print()

        report = {
            "total_tasks": len(results),
            "found": sum(1 for r in results if r.get("target_found")),
            "not_found": sum(1 for r in results if not r.get("target_found")),
            "qcad_screenshot": qcad_image,
            "results": results,
        }

        if report_path:
            Path(report_path).parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2)
            logger.info("Report saved to: %s", report_path)
            print(f"Report saved to: {report_path}")

        return report

    def close(self):
        self.x11.close()


def main():
    parser = argparse.ArgumentParser(description="QCAD + VLM Entity Matcher")
    parser.add_argument("--pdf-image", help="Path to PDF context image")
    parser.add_argument("--qcad-image", help="Path to existing QCAD screenshot (optional)")
    parser.add_argument("--prompt", "-p", help="Instruction / task description")
    parser.add_argument("--window-id", type=int, help="QCAD window ID (auto-detected if not set)")
    parser.add_argument("--tasks", "-t", help="Path to manifest.json for batch processing")
    parser.add_argument("--report", "-r", help="Path to save JSON report")
    parser.add_argument(
                "--vision-model",
                default="gemma4:31b-cloud",
                help="Preferred VLM model name (default: gemma4:31b-cloud)",
    )
    parser.add_argument(
        "--fallback-chain",
        default=",".join(DEFAULT_FALLBACK_CHAIN),
        help=f"Comma-separated fallback models (default: {','.join(DEFAULT_FALLBACK_CHAIN)})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout per model attempt in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama URL")

    args = parser.parse_args()

    fallback_list = [m.strip() for m in args.fallback_chain.split(",") if m.strip()]

    matcher = QCADVLMMatcher(
        vision_model=args.vision_model,
        fallback_chain=fallback_list,
        timeout=args.timeout,
        ollama_url=args.ollama_url,
    )

    try:
        if args.tasks:
            if not Path(args.tasks).exists():
                print(f"ERROR: Manifest not found: {args.tasks}")
                sys.exit(1)
            report = matcher.process_manifest(args.tasks, args.report)
            print(f"\nSummary: {report['found']}/{report['total_tasks']} entities matched")
        elif args.pdf_image and args.prompt:
            if not Path(args.pdf_image).exists():
                print(f"ERROR: PDF image not found: {args.pdf_image}")
                sys.exit(1)
            result = matcher.match_entity(
                pdf_image_path=args.pdf_image,
                qcad_image_path=args.qcad_image,
                instruction=args.prompt,
                window_id=args.window_id,
            )
            print("\nResult:")
            print(json.dumps(result, indent=2))
        else:
            print("ERROR: Provide either --tasks (batch) or --pdf-image + --prompt (single)")
            sys.exit(1)
    finally:
        matcher.close()


if __name__ == "__main__":
    main()
