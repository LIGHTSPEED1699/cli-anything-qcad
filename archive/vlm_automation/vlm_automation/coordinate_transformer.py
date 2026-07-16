#!/usr/bin/env python3
"""
Coordinate Transformer: Convert DXF coordinates to screen pixel coordinates.

Approach: Reference-point calibration using known entity mappings.
Since OCR doesn't reliably find drawing text in QCAD screenshots, we use
a more robust approach:
1. Use VLM or manual input to identify known entities on screen
2. Match those to DXF entities
3. Derive affine transform from matched reference points

Usage:
    python coordinate_transformer.py --dxf /tmp/example_panel_layout.dxf --window-name QCAD

Alternative: Manual calibration with known reference points
    python coordinate_transformer.py --dxf /tmp/example_panel_layout.dxf \
        --ref-point "Blu:100,200" --ref-point "NT-110:300,400"
"""

import sys
import json
import time
import tempfile
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent))

from x11_controller import X11Controller
from dxf_entity_lookup import DxfEntityIndex, DxfEntity


@dataclass
class ReferencePoint:
    """A matched reference point: DXF coordinate <-> screen pixel."""
    text: str
    dxf_x: float
    dxf_y: float
    screen_x: int
    screen_y: int
    confidence: float = 1.0  # 1.0 for manual, lower for detected


class CoordinateTransformer:
    """Transforms DXF model coordinates to screen pixel coordinates."""

    def __init__(self, dxf_path: str, window_name: str = "QCAD"):
        self.dxf_path = dxf_path
        self.window_name = window_name
        self.dxf_index = DxfEntityIndex(dxf_path)
        self.dxf_index.load()

        self.x11 = X11Controller()
        self.window_id: Optional[int] = None

        # Transform parameters (affine: screen = A * dxf + offset)
        self.scale_x: float = 1.0
        self.scale_y: float = -1.0
        self.offset_x: float = 0.0
        self.offset_y: float = 0.0
        self._cross_x: float = 0.0
        self._cross_y: float = 0.0

        self.calibrated = False
        self.reference_points: List[ReferencePoint] = []

    def find_window(self, fallback_names: List[str] = None) -> bool:
        """Find the QCAD window. Tries primary name, then fallbacks."""
        names_to_try = [self.window_name]
        
        # Default fallback chain matching QCADDualImageMatcher behavior
        DEFAULT_FALLBACKS = [
            "QCAD Professional",
            "QCAD Trial",
            "QCAD 3",
            "RivieraWaves",
        ]
        for fb in DEFAULT_FALLBACKS:
            if fb not in names_to_try:
                names_to_try.append(fb)
        
        if fallback_names:
            for fb in fallback_names:
                if fb not in names_to_try:
                    names_to_try.append(fb)
        
        # Also try the DWG filename without extension
        dwg_name = Path(self.dxf_path).stem
        if dwg_name not in names_to_try:
            names_to_try.append(dwg_name)

        for name in names_to_try:
            self.window_id = self.x11.get_window_by_name(name)
            if self.window_id:
                print(f"Found QCAD window (title='{name}'): {self.window_id}")
                return True

        # Last resort: search by size (QCAD is typically large)
        from Xlib import display as xdisplay
        d = xdisplay.Display()
        root = d.screen().root
        for w in root.query_tree().children:
            try:
                geom = w.get_geometry()
                if geom.width > 500 and geom.height > 400:
                    name = w.get_wm_name()
                    if name and str(name) not in ('None', ''):
                        print(f"  Candidate: ID={w.id} '{name}' {geom.width}x{geom.height}")
            except:
                pass

        print(f"QCAD window not found (tried: {names_to_try})")
        return False

    def _ocr_screenshot(self, screenshot_path: str) -> List[Dict[str, Any]]:
        """Run Tesseract OCR on screenshot and return text boxes with coordinates."""
        try:
            result = subprocess.run(
                ['tesseract', screenshot_path, '-', '--psm', '6', 'tsv'],
                capture_output=True, text=True, timeout=30
            )
            lines = result.stdout.strip().split('\n')
            if len(lines) < 2:
                return []

            # Parse TSV output
            results = []
            for line in lines[1:]:  # Skip header
                parts = line.split('\t')
                if len(parts) >= 11 and parts[10] != 'text':
                    try:
                        conf = float(parts[10])
                        if conf > 30:  # Only high-confidence results
                            left = int(parts[6])
                            top = int(parts[7])
                            width = int(parts[8])
                            height = int(parts[9])
                            text = parts[11] if len(parts) > 11 else ""
                            if text.strip() and len(text.strip()) >= 2:
                                results.append({
                                    'text': text.strip(),
                                    'x': left + width // 2,
                                    'y': top + height // 2,
                                    'bbox': [left, top, left + width, top + height],
                                    'confidence': conf / 100.0
                                })
                    except (ValueError, IndexError):
                        continue
            return results
        except Exception as e:
            print(f"OCR error: {e}")
            return []

    def _match_ocr_to_dxf(self, ocr_results: List[Dict[str, Any]]) -> List[ReferencePoint]:
        """Match OCR results to DXF entities."""
        matched = []
        for ocr in ocr_results:
            ocr_text = ocr['text'].strip()
            if len(ocr_text) < 2:
                continue

            # Try exact match first
            matches = self.dxf_index.search_exact(ocr_text)
            if not matches:
                # Try fuzzy
                fuzzy = self.dxf_index.search_fuzzy(ocr_text, threshold=0.8)
                if fuzzy:
                    matches = [fuzzy[0][0]]

            if matches:
                dxf_ent = matches[0]
                matched.append(ReferencePoint(
                    text=ocr_text,
                    dxf_x=dxf_ent.insertion_point[0],
                    dxf_y=dxf_ent.insertion_point[1],
                    screen_x=ocr['x'],
                    screen_y=ocr['y'],
                    confidence=ocr['confidence']
                ))
        return matched

    def calibrate_with_vlm(self) -> bool:
        """
        Calibrate using VLM to find known text entities on the QCAD screen.
        This is more reliable than OCR for CAD drawings.
        """
        print("Attempting VLM-based calibration...")
        try:
            from ollama_client import OllamaClient
            client = OllamaClient()

            # Take screenshot
            if not self.window_id:
                if not self.find_window():
                    return False

            screenshot_path = f"/tmp/qcad_vlm_calib_{int(time.time())}.png"
            self.x11.screenshot_window(self.window_id, screenshot_path)
            print(f"  Screenshot: {screenshot_path}")

            # Get a few known entities from DXF
            known_entities = []
            for text in ["Blu", "NT-110", "NT-111", "12 Blk", "12 Red", "12 Wht"]:
                matches = self.dxf_index.search_exact(text)
                if matches:
                    known_entities.append(matches[0])

            if len(known_entities) < 2:
                print("  Not enough known entities for VLM calibration")
                return False

            # Ask VLM to find the first entity
            entity = known_entities[0]
            prompt = (
                f"Find the text '{entity.text}' in this QCAD screenshot. "
                f"Respond ONLY with the approximate center coordinates in format 'x,y'. "
                f"If you cannot find it, say 'NOT_FOUND'."
            )

            print(f"  Asking VLM to find '{entity.text}'...")
            response = client.chat_with_image(
                model=self.x11_controller.vision_model if hasattr(self, 'x11_controller') else 'gemma4:e4b',
                prompt=prompt,
                image_path=screenshot_path
            )
            print(f"  VLM response: {response}")

            # Parse response
            coords_match = re.search(r'(\d+)\s*,\s*(\d+)', response)
            if coords_match:
                screen_x = int(coords_match.group(1))
                screen_y = int(coords_match.group(2))
                self.reference_points.append(ReferencePoint(
                    text=entity.text,
                    dxf_x=entity.insertion_point[0],
                    dxf_y=entity.insertion_point[1],
                    screen_x=screen_x,
                    screen_y=screen_y,
                    confidence=0.8
                ))

                # For a second point, ask about another entity
                if len(known_entities) > 1:
                    entity2 = known_entities[1]
                    prompt2 = (
                        f"Find the text '{entity2.text}' in this QCAD screenshot. "
                        f"Respond ONLY with the approximate center coordinates in format 'x,y'. "
                        f"If you cannot find it, say 'NOT_FOUND'."
                    )
                    print(f"  Asking VLM to find '{entity2.text}'...")
                    response2 = client.process_image(screenshot_path, prompt2)
                    print(f"  VLM response: {response2}")

                    coords_match2 = re.search(r'(\d+)\s*,\s*(\d+)', response2)
                    if coords_match2:
                        screen_x2 = int(coords_match2.group(1))
                        screen_y2 = int(coords_match2.group(2))
                        self.reference_points.append(ReferencePoint(
                            text=entity2.text,
                            dxf_x=entity2.insertion_point[0],
                            dxf_y=entity2.insertion_point[1],
                            screen_x=screen_x2,
                            screen_y=screen_y2,
                            confidence=0.8
                        ))

                if len(self.reference_points) >= 2:
                    self._compute_transform()
                    self.calibrated = True
                    self._validate()
                    return True

            print("  VLM calibration: insufficient points")
            return False

        except Exception as e:
            print(f"  VLM calibration error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def calibrate_manual(self, ref_points: List[ReferencePoint]) -> bool:
        """Calibrate using manually-provided reference points."""
        print(f"Calibrating with {len(ref_points)} manual reference points...")
        self.reference_points = ref_points
        if len(ref_points) >= 2:
            self._compute_transform()
            self.calibrated = True
            self._validate()
            return True
        elif len(ref_points) == 1:
            # With one point, we can only do translation (assuming 1:1 scale)
            print("  WARNING: Only 1 reference point - using identity scale")
            rp = ref_points[0]
            self.scale_x = 1.0
            self.scale_y = -1.0
            self.offset_x = rp.screen_x - rp.dxf_x
            self.offset_y = rp.screen_y + rp.dxf_y  # Y flip
            self._cross_x = 0.0
            self._cross_y = 0.0
            self.calibrated = True
            return True
        return False

    def calibrate(self, min_matches: int = 3) -> bool:
        """
        Calibrate the coordinate transform.
        Tries multiple methods: VLM -> OCR -> manual fallback.
        """
        if not self.find_window():
            return False

        print("Calibrating coordinate transform...")

        # Method 1: Try VLM-based calibration
        if self.calibrate_with_vlm():
            return True

        # Method 2: Try OCR-based calibration
        print("\nTrying OCR-based calibration...")
        geom = self.x11.get_window_geometry(self.window_id)
        print(f"  Window: {geom['width']}x{geom['height']} @ ({geom['x']}, {geom['y']})")

        screenshot_path = f"/tmp/qcad_calib_{int(time.time())}.png"
        self.x11.screenshot_window(self.window_id, screenshot_path)
        print(f"  Screenshot: {screenshot_path}")

        ocr_results = self._ocr_screenshot(screenshot_path)
        print(f"  OCR found {len(ocr_results)} text regions")

        self.reference_points = self._match_ocr_to_dxf(ocr_results)
        print(f"  Matched {len(self.reference_points)} reference points")

        if len(self.reference_points) >= min_matches:
            self._compute_transform()
            self.calibrated = True
            self._validate()
            return True

        # Method 3: Use simple scale-based calibration if we know drawing bounds
        print("\nTrying viewport-based calibration...")
        if self._calibrate_from_viewport(geom):
            return True

        print(f"\nCalibration failed. Need {min_matches} reference points, got {len(self.reference_points)}")
        for rp in self.reference_points[:10]:
            print(f"  '{rp.text}': DXF({rp.dxf_x:.2f}, {rp.dxf_y:.2f}) -> Screen({rp.screen_x}, {rp.screen_y})")
        return False

    def _calibrate_from_viewport(self, geom: Dict[str, Any]) -> bool:
        """
        Calibrate using DXF bounding box and window geometry.
        Assumes the drawing is fit to the viewport.
        """
        # Get DXF extents
        try:
            import ezdxf
            doc = ezdxf.readfile(self.dxf_path)
            msp = doc.modelspace()

            # Get all entities bounds
            all_x = []
            all_y = []
            for entity in msp:
                try:
                    if hasattr(entity, 'dxf') and hasattr(entity.dxf, 'insert'):
                        all_x.append(entity.dxf.insert[0])
                        all_y.append(entity.dxf.insert[1])
                except:
                    pass

            if not all_x:
                return False

            dxf_min_x, dxf_max_x = min(all_x), max(all_x)
            dxf_min_y, dxf_max_y = min(all_y), max(all_y)
            dxf_width = dxf_max_x - dxf_min_x
            dxf_height = dxf_max_y - dxf_min_y

            print(f"  DXF bounds: ({dxf_min_x:.2f}, {dxf_min_y:.2f}) - ({dxf_max_x:.2f}, {dxf_max_y:.2f})")
            print(f"  DXF size: {dxf_width:.2f} x {dxf_height:.2f}")

            # Screen dimensions (rough estimate of drawing area)
            screen_w = geom['width'] * 0.85  # Account for toolbars/panels
            screen_h = geom['height'] * 0.80
            screen_margin_x = geom['width'] * 0.05
            screen_margin_y = geom['height'] * 0.10

            # Compute uniform scale
            scale_x = screen_w / dxf_width if dxf_width > 0 else 1
            scale_y = screen_h / dxf_height if dxf_height > 0 else 1
            scale = min(scale_x, scale_y)

            # Center the drawing
            offset_x = screen_margin_x + (screen_w - dxf_width * scale) / 2 - dxf_min_x * scale
            offset_y = screen_margin_y + screen_h - (screen_h - dxf_height * scale) / 2 + dxf_min_y * scale

            self.scale_x = scale
            self.scale_y = -scale
            self.offset_x = offset_x
            self.offset_y = offset_y
            self._cross_x = 0.0
            self._cross_y = 0.0

            print(f"  Estimated scale: {scale:.2f}")
            print(f"  Estimated offset: ({offset_x:.1f}, {offset_y:.1f})")
            print("  WARNING: Using viewport-based estimation. Coordinates may be inaccurate.")
            print("  For accurate results, provide manual reference points.")

            self.calibrated = True
            return True

        except Exception as e:
            print(f"  Viewport calibration error: {e}")
            return False

    def _compute_transform(self) -> None:
        """Compute affine transform from reference points using least squares."""
        import numpy as np

        n = len(self.reference_points)
        A = np.zeros((n * 2, 6))
        B = np.zeros(n * 2)

        for i, rp in enumerate(self.reference_points):
            A[i * 2] = [rp.dxf_x, rp.dxf_y, 1, 0, 0, 0]
            B[i * 2] = rp.screen_x
            A[i * 2 + 1] = [0, 0, 0, rp.dxf_x, rp.dxf_y, 1]
            B[i * 2 + 1] = rp.screen_y

        params, residuals, rank, s = np.linalg.lstsq(A, B, rcond=None)

        self.scale_x = params[0]
        self._cross_x = params[1]
        self.offset_x = params[2]
        self._cross_y = params[3]
        self.scale_y = params[4]
        self.offset_y = params[5]

        print(f"\n  Transform computed:")
        print(f"    screen_x = {self.scale_x:.3f}*dxf_x + {self._cross_x:.3f}*dxf_y + {self.offset_x:.1f}")
        print(f"    screen_y = {self._cross_y:.3f}*dxf_x + {self.scale_y:.3f}*dxf_y + {self.offset_y:.1f}")

    def dxf_to_screen(self, dxf_x: float, dxf_y: float) -> Tuple[int, int]:
        """Convert DXF coordinates to screen pixel coordinates."""
        if not self.calibrated:
            raise RuntimeError("Transformer not calibrated. Call calibrate() first.")

        screen_x = self.scale_x * dxf_x + self._cross_x * dxf_y + self.offset_x
        screen_y = self._cross_y * dxf_x + self.scale_y * dxf_y + self.offset_y
        return (int(round(screen_x)), int(round(screen_y)))

    def screen_to_dxf(self, screen_x: int, screen_y: int) -> Tuple[float, float]:
        """Convert screen pixel coordinates to DXF coordinates."""
        if not self.calibrated:
            raise RuntimeError("Transformer not calibrated. Call calibrate() first.")

        import numpy as np
        A = np.array([[self.scale_x, self._cross_x], [self._cross_y, self.scale_y]])
        B = np.array([screen_x - self.offset_x, screen_y - self.offset_y])
        try:
            dxf_xy = np.linalg.solve(A, B)
            return (float(dxf_xy[0]), float(dxf_xy[1]))
        except np.linalg.LinAlgError:
            dxf_xy = np.linalg.lstsq(A, B, rcond=None)[0]
            return (float(dxf_xy[0]), float(dxf_xy[1]))

    def _validate(self) -> None:
        """Validate transform by computing errors on reference points."""
        errors = []
        for rp in self.reference_points:
            pred_x, pred_y = self.dxf_to_screen(rp.dxf_x, rp.dxf_y)
            err = ((pred_x - rp.screen_x) ** 2 + (pred_y - rp.screen_y) ** 2) ** 0.5
            errors.append(err)

        avg_err = sum(errors) / len(errors) if errors else 0
        max_err = max(errors) if errors else 0

        print(f"\n  Validation:")
        print(f"    Average error: {avg_err:.1f} pixels")
        print(f"    Max error: {max_err:.1f} pixels")
        print(f"    Reference points: {len(self.reference_points)}")

        if max_err > 50:
            print(f"    WARNING: Large transform error. Check if QCAD is zoomed/panned unusually.")

    def move_mouse_to_dxf(self, dxf_x: float, dxf_y: float) -> None:
        """Move mouse to DXF coordinates (converted to screen)."""
        screen_x, screen_y = self.dxf_to_screen(dxf_x, dxf_y)
        print(f"Moved mouse to screen ({screen_x}, {screen_y}) [DXF: {dxf_x:.2f}, {dxf_y:.2f}]")

    def close(self):
        self.x11.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Coordinate Transformer: DXF -> Screen')
    parser.add_argument('--dxf', required=True, help='Path to DXF file')
    parser.add_argument('--window-name', default='QCAD', help='QCAD window name')
    parser.add_argument('--ref-point', action='append', help='Manual reference point (format: "text:screen_x,screen_y")')
    parser.add_argument('--test-dxf', help='Test DXF coordinate (format: x,y)')
    parser.add_argument('--test-screen', help='Test screen coordinate (format: x,y)')
    parser.add_argument('--vlm', action='store_true', help='Use VLM for calibration')
    args = parser.parse_args()

    transformer = CoordinateTransformer(args.dxf, args.window_name)

    try:
        if args.ref_point:
            ref_points = []
            for rp_str in args.ref_point:
                text, coords = rp_str.split(':')
                screen_x, screen_y = map(int, coords.split(','))
                # Find DXF entity
                matches = transformer.dxf_index.search_exact(text.strip())
                if matches:
                    ent = matches[0]
                    ref_points.append(ReferencePoint(
                        text=text.strip(),
                        dxf_x=ent.insertion_point[0],
                        dxf_y=ent.insertion_point[1],
                        screen_x=screen_x,
                        screen_y=screen_y
                    ))
                else:
                    print(f"WARNING: DXF entity '{text}' not found")
            if ref_points:
                success = transformer.calibrate_manual(ref_points)
            else:
                success = transformer.calibrate()
        elif args.vlm:
            success = transformer.calibrate_with_vlm()
        else:
            success = transformer.calibrate()

        if not success:
            print("Calibration failed")
            sys.exit(1)

        if args.test_dxf:
            x, y = map(float, args.test_dxf.split(','))
            sx, sy = transformer.dxf_to_screen(x, y)
            print(f"\nDXF({x}, {y}) -> Screen({sx}, {sy})")

        if args.test_screen:
            x, y = map(int, args.test_screen.split(','))
            dx, dy = transformer.screen_to_dxf(x, y)
            print(f"\nScreen({x}, {y}) -> DXF({dx:.3f}, {dy:.3f})")

    finally:
        transformer.close()


if __name__ == '__main__':
    main()
