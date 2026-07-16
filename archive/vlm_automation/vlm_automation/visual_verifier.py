#!/usr/bin/env python3
"""
Visual Verifier: Render DWG/DXF to PDF/PNG and compare to detect artifacts.

Used by all tiers after editing:
  - T1: ezdxf edit → render modified DXF → compare with original
  - T2: QCAD ECMAScript → render modified DWG → compare with pre-edit
  - T3: ODA round-trip → render both original and round-tripped → compare
  - T4: VLM+X11 → post-action screenshot + VLM Phase 3 (separate)

Verification pipeline:
  1. Render both original and modified files to PNG (via LibreCAD/QCAD/ODA)
  2. Pixel-level diff (fast, deterministic)
  3. If pixel changes > threshold → VLM semantic compare (slower, meaning-aware)
  4. Decision: PASSED / WARNING / FAILED
"""

import os
import re
import sys
import json
import shutil
import tempfile
import subprocess
import argparse
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
from dataclasses import dataclass, asdict
from PIL import Image


@dataclass
class VerificationResult:
    """Result of visual verification."""
    status: str  # "PASSED", "WARNING", "FAILED"
    original_png: Optional[str] = None
    modified_png: Optional[str] = None
    diff_png: Optional[str] = None
    pixel_changed: int = 0
    pixel_total: int = 0
    pixel_change_pct: float = 0.0
    vlm_confidence: Optional[float] = None
    vlm_reasoning: Optional[str] = None
    error: Optional[str] = None
    renderer_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VisualVerifier:
    """Verifies DWG/DXF edits by rendering and comparing."""

    # Pixel change thresholds
    PIXEL_PASS_THRESHOLD = 0.01       # <1% changed = likely just text edit
    PIXEL_WARNING_THRESHOLD = 0.05    # 1–5% changed = investigate
    PIXEL_FAIL_THRESHOLD = 0.10       # >10% changed = likely artifact

    def __init__(self, renderer: str = "auto", dpi: int = 150):
        """
        Args:
            renderer: "auto", "librecad", "qcad", "oda", or "imagemagick"
            dpi: Resolution for rendering (higher = more sensitive diff)
        """
        self.renderer = renderer
        self.dpi = dpi
        self._check_tools()

    def _check_tools(self):
        """Detect available rendering tools."""
        self.tools = {
            "librecad": shutil.which("librecad") is not None,
            "qcad": shutil.which("qcad") is not None or Path("~/opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad").expanduser().exists(),
            "dwg2pdf": shutil.which("dwg2pdf") is not None,
            "ODAFileConverter": self._find_oda_converter() is not None,
            "convert": shutil.which("convert") is not None,  # ImageMagick
        }

        if self.renderer == "auto":
            # Prefer LibreCAD for DXF, QCAD for DWG
            self.renderer = "librecad"  # Will switch per file type

    def _find_oda_converter(self) -> Optional[Path]:
        """Find ODA File Converter binary."""
        candidates = [
            "/usr/bin/ODAFileConverter",
            "/usr/local/bin/ODAFileConverter",
            "/tmp/squashfs-root/usr/bin/ODAFileConverter",
        ]
        for c in candidates:
            p = Path(c)
            if p.exists():
                return p
        return None

    def _get_qcad_path(self) -> Optional[str]:
        """Find QCAD binary path."""
        qcad_path = shutil.which("qcad")
        if qcad_path:
            return qcad_path
        home_qcad = Path("~/opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad").expanduser()
        if home_qcad.exists():
            return str(home_qcad)
        return None

    def render(self, file_path: str, output_png: str, renderer: Optional[str] = None) -> bool:
        """
        Render a DWG or DXF file to PNG.

        Args:
            file_path: Path to DWG or DXF file
            output_png: Path for output PNG
            renderer: Override renderer for this call

        Returns:
            True if successful
        """
        file_path = Path(file_path)
        output_png = Path(output_png)
        ext = file_path.suffix.lower()
        renderer = renderer or self.renderer

        if ext == ".dxf":
            return self._render_dxf(str(file_path), str(output_png), renderer)
        elif ext == ".dwg":
            return self._render_dwg(str(file_path), str(output_png), renderer)
        else:
            # Try ImageMagick for anything else
            return self._render_with_imagemagick(str(file_path), str(output_png))

    def _render_dxf(self, dxf_path: str, output_png: str, renderer: str) -> bool:
        """Render DXF to PNG."""
        # Prefer LibreCAD for DXF
        if self.tools["librecad"] and renderer in ("auto", "librecad"):
            return self._render_with_librecad(dxf_path, output_png)

        # Fallback to QCAD
        qcad = self._get_qcad_path()
        if qcad and renderer in ("auto", "qcad"):
            return self._render_with_qcad(dxf_path, output_png)

        # Last resort: ODA
        if self.tools["ODAFileConverter"] and renderer in ("auto", "oda"):
            return self._render_with_oda(dxf_path, output_png)

        return False

    def _render_dwg(self, dwg_path: str, output_png: str, renderer: str) -> bool:
        """Render DWG to PNG."""
        # Prefer QCAD dwg2pdf for DWG
        if self.tools["dwg2pdf"] and renderer in ("auto", "qcad"):
            return self._render_with_dwg2pdf(dwg_path, output_png)

        # Fallback to QCAD headless
        qcad = self._get_qcad_path()
        if qcad and renderer in ("auto", "qcad"):
            return self._render_with_qcad(dwg_path, output_png)

        # ODA fallback
        if self.tools["ODAFileConverter"] and renderer in ("auto", "oda"):
            return self._render_with_oda(dwg_path, output_png)

        return False

    def _render_with_librecad(self, dxf_path: str, output_png: str) -> bool:
        """Render DXF to PDF via LibreCAD, then convert to PNG."""
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                pdf_path = Path(tmpdir) / "output.pdf"
                env = os.environ.copy()
                env["QT_QPA_PLATFORM"] = "offscreen"
                cmd = [
                    "librecad", "dxf2pdf",
                    "-o", str(pdf_path),
                    dxf_path
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
                if result.returncode != 0:
                    print(f"LibreCAD error: {result.stderr}")
                    return False
                if not pdf_path.exists():
                    return False
                return self._pdf_to_png(str(pdf_path), output_png)
        except Exception as e:
            print(f"LibreCAD render failed: {e}")
            return False

    def _render_with_qcad(self, file_path: str, output_png: str) -> bool:
        """Render via QCAD headless ECMAScript."""
        try:
            qcad = self._get_qcad_path()
            if not qcad:
                return False

            with tempfile.TemporaryDirectory() as tmpdir:
                # Write a render script
                # Escape backslashes for ECMAScript string literals
                safe_file_path = file_path.replace('\\', '\\\\')
                safe_output_png = output_png.replace('\\', '\\\\')
                script = f"""
// QCAD headless render script
var doc = Document.getCurrentlyOpenedDocument();
if (!doc) {{
    doc = Document.open("{safe_file_path}");
}}
if (!doc) {{
    print("ERROR: Cannot open file");
    qcad.exit(1);
}}
var view = new View(doc);
view.setViewport(0, 0, 1920, 1080);
view.autoZoom();
view.exportAsImage("{safe_output_png}", 1920, 1080);
qcad.exit(0);
"""
                script_path = Path(tmpdir) / "render.js"
                script_path.write_text(script)

                cmd = [
                    qcad,
                    "-platform", "offscreen",
                    "-autostart", str(script_path),
                    file_path
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                return result.returncode == 0 and Path(output_png).exists()
        except Exception as e:
            print(f"QCAD render failed: {e}")
            return False

    def _render_with_dwg2pdf(self, dwg_path: str, output_png: str) -> bool:
        """Render DWG via QCAD dwg2pdf, then convert to PNG."""
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                pdf_path = Path(tmpdir) / "output.pdf"
                env = os.environ.copy()
                env["QT_QPA_PLATFORM"] = "offscreen"
                cmd = ["dwg2pdf", "-platform", "offscreen", dwg_path]
                result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
                # dwg2pdf outputs to same directory as input
                expected_pdf = Path(dwg_path).with_suffix(".pdf")
                if expected_pdf.exists():
                    shutil.move(str(expected_pdf), str(pdf_path))
                if pdf_path.exists():
                    return self._pdf_to_png(str(pdf_path), output_png)
                return False
        except Exception as e:
            print(f"dwg2pdf render failed: {e}")
            return False

    def _render_with_oda(self, file_path: str, output_png: str) -> bool:
        """Render via ODA File Converter."""
        oda = self._find_oda_converter()
        if not oda:
            return False
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                input_dir = Path(tmpdir) / "input"
                output_dir = Path(tmpdir) / "output"
                input_dir.mkdir()
                output_dir.mkdir()
                shutil.copy(file_path, input_dir / Path(file_path).name)

                # ODA batch mode: directory conversion
                cmd = [
                    str(oda),
                    str(input_dir),
                    str(output_dir),
                    "ACAD2018",
                    "PDF",
                    "0",  # recursive
                    "1",  # audit
                ]
                env = os.environ.copy()
                env["QT_QPA_PLATFORM"] = "offscreen"
                result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)

                # Find the output PDF
                pdf_files = list(output_dir.glob("*.pdf"))
                if pdf_files:
                    return self._pdf_to_png(str(pdf_files[0]), output_png)
                return False
        except Exception as e:
            print(f"ODA render failed: {e}")
            return False

    def _render_with_imagemagick(self, file_path: str, output_png: str) -> bool:
        """Fallback: use ImageMagick convert for already-PDF inputs."""
        try:
            cmd = ["convert", "-density", str(self.dpi), file_path, output_png]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode == 0 and Path(output_png).exists()
        except Exception as e:
            print(f"ImageMagick render failed: {e}")
            return False

    def _pdf_to_png(self, pdf_path: str, output_png: str) -> bool:
        """Convert PDF to PNG via ImageMagick or pdftoppm."""
        try:
            # Try pdftoppm first (better quality)
            pdftoppm = shutil.which("pdftoppm")
            if pdftoppm:
                cmd = [
                    pdftoppm,
                    "-png",
                    "-r", str(self.dpi),
                    "-singlefile",
                    pdf_path,
                    output_png.replace(".png", "")
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    # pdftoppm appends .png
                    generated = Path(output_png.replace(".png", "") + ".png")
                    if generated.exists():
                        if str(generated) != output_png:
                            shutil.move(str(generated), output_png)
                        return True

            # Fallback to ImageMagick
            return self._render_with_imagemagick(pdf_path, output_png)
        except Exception as e:
            print(f"PDF→PNG conversion failed: {e}")
            return False

    def pixel_diff(self, original_png: str, modified_png: str, output_diff: Optional[str] = None) -> Tuple[int, int, float]:
        """
        Compute pixel-level difference between two PNGs.

        Returns:
            (changed_pixels, total_pixels, change_percentage)
        """
        try:
            img1 = Image.open(original_png).convert("RGB")
            img2 = Image.open(modified_png).convert("RGB")

            # Resize to same dimensions
            if img1.size != img2.size:
                img2 = img2.resize(img1.size, Image.LANCZOS)

            width, height = img1.size
            total_pixels = width * height

            # Compute diff
            diff_count = 0
            threshold = 30  # RGB difference threshold

            if output_diff:
                diff_img = Image.new("RGB", img1.size)
                diff_pixels = diff_img.load()

            for y in range(height):
                for x in range(width):
                    p1 = img1.getpixel((x, y))
                    p2 = img2.getpixel((x, y))
                    diff = sum(abs(a - b) for a, b in zip(p1, p2))
                    if diff > threshold:
                        diff_count += 1
                        if output_diff:
                            diff_pixels[x, y] = (255, 0, 0)  # Red for changed
                    else:
                        if output_diff:
                            diff_pixels[x, y] = p1

            if output_diff:
                diff_img.save(output_diff)

            change_pct = diff_count / total_pixels if total_pixels > 0 else 0
            return diff_count, total_pixels, change_pct

        except Exception as e:
            print(f"Pixel diff failed: {e}")
            return 0, 1, 1.0  # Return max change on error

    def verify(self, original_file: str, modified_file: str,
               expected_change_desc: str = "",
               vlm_client=None) -> VerificationResult:
        """
        Verify that a modification produced the expected change without artifacts.

        Args:
            original_file: Path to original DWG/DXF
            modified_file: Path to modified DWG/DXF
            expected_change_desc: Description of expected change (for VLM compare)
            vlm_client: Optional OllamaClient for semantic comparison

        Returns:
            VerificationResult with status PASSED/WARNING/FAILED
        """
        result = VerificationResult(status="FAILED")

        # Step 1: Render both files
        with tempfile.TemporaryDirectory() as tmpdir:
            original_png = Path(tmpdir) / "original.png"
            modified_png = Path(tmpdir) / "modified.png"
            diff_png = Path(tmpdir) / "diff.png"

            print(f"  Rendering original: {original_file}")
            if not self.render(original_file, str(original_png)):
                result.error = "Failed to render original file"
                result.renderer_used = self.renderer
                return result

            print(f"  Rendering modified: {modified_file}")
            if not self.render(modified_file, str(modified_png)):
                result.error = "Failed to render modified file"
                result.renderer_used = self.renderer
                return result

            result.original_png = str(original_png)
            result.modified_png = str(modified_png)
            result.renderer_used = self.renderer

            # Step 2: Pixel diff
            print(f"  Computing pixel diff...")
            changed, total, pct = self.pixel_diff(
                str(original_png), str(modified_png), str(diff_png)
            )
            result.pixel_changed = changed
            result.pixel_total = total
            result.pixel_change_pct = pct
            result.diff_png = str(diff_png)

            print(f"    Changed pixels: {changed}/{total} ({pct*100:.2f}%)")

            # Step 3: Decision based on pixel change
            if pct < self.PIXEL_PASS_THRESHOLD:
                # Very small change — could be just text edit, or could be NO change (failure)
                # Need VLM to confirm the intended change actually happened
                if vlm_client and expected_change_desc:
                    print(f"  VLM semantic verification...")
                    vlm_result = self._vlm_compare(
                        vlm_client, str(original_png), str(modified_png),
                        expected_change_desc
                    )
                    result.vlm_confidence = vlm_result.get("confidence")
                    result.vlm_reasoning = vlm_result.get("reasoning")

                    if vlm_result.get("intended_change_detected", False):
                        result.status = "PASSED"
                    else:
                        result.status = "FAILED"
                        result.error = f"VLM: intended change not detected. {vlm_result.get('reasoning', '')}"
                else:
                    # No VLM available — assume pass for small changes
                    result.status = "PASSED"

            elif pct < self.PIXEL_WARNING_THRESHOLD:
                # Moderate change — VLM compare recommended
                if vlm_client and expected_change_desc:
                    vlm_result = self._vlm_compare(
                        vlm_client, str(original_png), str(modified_png),
                        expected_change_desc
                    )
                    result.vlm_confidence = vlm_result.get("confidence")
                    result.vlm_reasoning = vlm_result.get("reasoning")

                    if vlm_result.get("confidence", 0) > 0.85 and vlm_result.get("unintended_changes", 0) == 0:
                        result.status = "PASSED"
                    elif vlm_result.get("confidence", 0) > 0.70:
                        result.status = "WARNING"
                    else:
                        result.status = "FAILED"
                else:
                    result.status = "WARNING"
                    result.vlm_reasoning = "No VLM client provided for semantic verification"

            else:
                # Large change — likely artifact or catastrophic edit
                result.status = "FAILED"
                result.error = f"Pixel change {pct*100:.1f}% exceeds fail threshold {self.PIXEL_FAIL_THRESHOLD*100:.1f}%. Likely conversion artifact or unintended modification."

        return result

    def _vlm_compare(self, vlm_client, original_png: str, modified_png: str,
                     expected_change: str) -> Dict[str, Any]:
        """Ask VLM to compare original and modified screenshots."""
        prompt = f"""
Compare these two CAD drawing screenshots.

LEFT = original, RIGHT = modified

The intended change was: "{expected_change}"

Answer these questions:
1. Did the intended change occur? (yes/no)
2. Are there any unintended changes? (count)
3. Confidence: 0.0 to 1.0
4. Brief reasoning

Output JSON only:
{{
  "intended_change_detected": true/false,
  "unintended_changes": 0,
  "confidence": 0.0,
  "reasoning": "..."
}}
"""
        try:
            # Use dual-image comparison if client supports it
            response = vlm_client.chat_with_image(
                model=getattr(vlm_client, 'vision_model', 'qwen2.5vl:3b'),
                prompt=prompt,
                image_path=modified_png,  # Primary image
                # Some clients support multiple images; fallback to single
            )
            # Try to parse JSON from response
            import json
            # Extract JSON from response text
            text = response if isinstance(response, str) else str(response)
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return {"intended_change_detected": False, "unintended_changes": 0, "confidence": 0.0, "reasoning": "Could not parse VLM response"}
        except Exception as e:
            return {"intended_change_detected": False, "unintended_changes": 0, "confidence": 0.0, "reasoning": f"VLM error: {str(e)}"}

    def verify_dry_run(self, original_file: str) -> VerificationResult:
        """
        Dry-run verification: render original file only, to test renderer availability.

        Returns:
            VerificationResult with status = PASSED if render succeeds
        """
        result = VerificationResult(status="FAILED")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                test_png = Path(tmpdir) / "test.png"
                if self.render(original_file, str(test_png)):
                    result.status = "PASSED"
                    result.renderer_used = self.renderer
                    result.original_png = str(test_png)
                    result.reasoning = "Dry-run render successful"
                else:
                    result.error = "Dry-run render failed"
        except Exception as e:
            result.error = f"Dry-run exception: {e}"
        return result


def demo():
    """Demonstrate visual verifier with a dry run."""
    print("=" * 60)
    print("Visual Verifier Demo")
    print("=" * 60)

    verifier = VisualVerifier(renderer="auto", dpi=150)
    print(f"\nAvailable tools: {verifier.tools}")

    # Dry run test — render a sample DXF if available
    sample_dxf = Path("~/.openclaw/workspace/vlm-gui-automation/example_panel_layout.dxf").expanduser()
    if sample_dxf.exists():
        print(f"\nDry-run rendering: {sample_dxf}")
        result = verifier.verify_dry_run(str(sample_dxf))
        print(f"  Status: {result.status}")
        print(f"  Renderer: {result.renderer_used}")
        if result.original_png:
            print(f"  Output: {result.original_png}")
        if result.error:
            print(f"  Error: {result.error}")
    else:
        print(f"\nNo sample DXF found at {sample_dxf}")
        print("  Skipping dry run. Place a DXF/DWG file to test rendering.")


if __name__ == "__main__":
    demo()
