"""Visual verification: render and compare DWG/DXF outputs."""
import base64
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli_anything.qcad.utils.render import QcadRenderer


@dataclass
class VerificationResult:
    status: str
    pixel_change_pct: float = 0.0
    vlm_confidence: Optional[float] = None
    vlm_reasoning: Optional[str] = None
    original_png: Optional[str] = None
    modified_png: Optional[str] = None
    diff_png: Optional[str] = None
    error: Optional[str] = None
    renderer_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VisualVerifier:
    """Render DWG/DXF to PNG, pixel-diff, and optionally ask a VLM."""

    def __init__(self, renderer: QcadRenderer = None, ollama_url: str = None, vision_model: str = None):
        self.renderer = renderer or QcadRenderer()
        self.ollama_url = ollama_url or os.environ.get("OLLAMA_URL", "http://192.168.2.15:11434")
        self.vision_model = vision_model or os.environ.get("VISION_MODEL", "qwen2.5vl:latest")

    def render(self, file_path: str, output_png: str) -> bool:
        return self.renderer.render(file_path, output_png)

    def compare(
        self,
        original_png: str,
        modified_png: str,
        annotations: List[Dict[str, Any]],
    ) -> VerificationResult:
        if not Path(original_png).exists() or not Path(modified_png).exists():
            return VerificationResult(status="FAILED", error="Missing render PNG")

        try:
            from PIL import Image, ImageChops
            img1 = Image.open(original_png).convert("RGB")
            img2 = Image.open(modified_png).convert("RGB")
            diff = ImageChops.difference(img1, img2)
            bbox = diff.getbbox()
            changed = 0
            if bbox:
                diff_gray = diff.convert("L")
                changed = sum(1 for p in diff_gray.getdata() if p > 10)
            total = img1.width * img1.height
            pct = changed / total if total else 0.0

            status = "PASSED"
            if pct > 0.10:
                status = "FAILED"
            elif pct > 0.01:
                status = "WARNING"

            return VerificationResult(
                status=status,
                pixel_change_pct=pct,
                original_png=original_png,
                modified_png=modified_png,
                renderer_used="pixel_diff",
            )
        except Exception as e:
            return VerificationResult(status="FAILED", error=str(e))

    def vlm_verify(self, image_path: str, question: str) -> Dict[str, Any]:
        """Stub: port qcad_vlm_verifier.py Ollama API call here."""
        # TODO: integrate real VLM call
        return {"answer": "stub", "confidence": 0.0}
