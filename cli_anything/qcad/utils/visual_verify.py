"""Visual verification: render and compare DWG/DXF outputs."""
import json
import os
import base64
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageChops


@dataclass
class VerificationResult:
    status: str
    pixel_change_pct: float
    vlm_confidence: Optional[float] = None
    vlm_reasoning: Optional[str] = None
    original_png: Optional[str] = None
    modified_png: Optional[str] = None
    diff_png: Optional[str] = None
    error: Optional[str] = None
    renderer_used: str = ""
    vlm: Dict = None

    def __post_init__(self):
        if self.vlm is None:
            self.vlm = {}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VisualVerifier:
    """Compare original vs modified drawing renders."""

    def __init__(self, renderer=None):
        self.renderer = renderer

    def render(self, file_path: str, output_png: str) -> bool:
        if self.renderer is None:
            return False
        return self.renderer.render(file_path, output_png)

    def compare(self, original_png: str, modified_png: str,
                annotations: List[Dict[str, Any]]) -> VerificationResult:
        try:
            orig = Image.open(original_png).convert("RGB")
            mod = Image.open(modified_png).convert("RGB")
            w = min(orig.width, mod.width)
            h = min(orig.height, mod.height)
            orig_c = orig.crop((0, 0, w, h)).convert("L")
            mod_c = mod.crop((0, 0, w, h)).convert("L")

            diff = ImageChops.difference(orig_c, mod_c)
            import numpy as np
            np_diff = np.array(diff)
            nonzero = int(np.count_nonzero(np_diff > 20))
            total = np_diff.size
            pct = 100.0 * nonzero / total if total else 0.0

            diff_path = str(Path(modified_png).with_suffix("")) + "_diff.png"
            out = Image.new("RGB", (w, h))
            out.paste(orig.crop((0, 0, w, h)), (0, 0))
            arr = np.array(out)
            arr[np_diff > 20] = [255, 0, 0]
            Image.fromarray(arr).save(diff_path)

            status = "PASSED" if pct > 0.1 else "FAILED"
            return VerificationResult(
                status=status,
                pixel_change_pct=pct,
                original_png=original_png,
                modified_png=modified_png,
                diff_png=diff_path,
                renderer_used="pixel_diff",
            )
        except Exception as e:
            return VerificationResult(
                status="ERROR",
                pixel_change_pct=0.0,
                error=str(e),
                renderer_used="pixel_diff",
            )

    def vlm_verify(self, image_path: str, question: str,
                   ollama_url: str = "http://192.168.2.15:11434",
                   model: str = "gemma4:31b-cloud") -> Dict[str, Any]:
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": question, "images": [image_b64]}
            ],
            "stream": False,
            "options": {"num_predict": 512, "temperature": 0.3},
        }
        try:
            req = urllib.request.Request(
                f"{ollama_url}/api/chat",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode())
                answer = result.get("message", {}).get("content", "")
                return {
                    "answer": answer,
                    "pass": answer.strip().upper().startswith("YES"),
                    "model": model,
                    "eval_count": result.get("eval_count", 0),
                }
        except Exception as e:
            return {"answer": f"ERROR: {e}", "pass": False, "model": model, "error": str(e)}
