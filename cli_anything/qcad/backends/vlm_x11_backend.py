"""T4 backend: VLM + X11 GUI automation fallback."""
import base64
import json
import os
import urllib.request
from typing import Any, Dict

from cli_anything.qcad.utils.visual_verifier import QcadVlmVerifier


class VlmX11Backend:
    """Execute ambiguous or interactive edits via VLM screen understanding and X11."""

    def __init__(self, qcad_bin: str = None):
        self.verifier = QcadVlmVerifier(qcad_bin=qcad_bin)

    def execute(self, annotation: Dict[str, Any], file_path: str) -> Dict[str, Any]:
        """Run VLM+X11 automation for this annotation.

        For now, this reports the ambiguity and performs a VLM visual check.
        A full implementation would use Geisterhand to drive QCAD GUI clicks.
        """
        question = annotation.get("text", "")
        try:
            result = self.verifier.verify(file_path, question)
            return {
                "backend": "vlm_x11",
                "success": bool(result.get("pass")),
                "vlm_result": result,
                "annotation": question,
            }
        except Exception as e:
            return {
                "backend": "vlm_x11",
                "success": False,
                "error": str(e),
                "annotation": question,
            }

    def ask_vlm(self, image_path: str, question: str, ollama_url: str = None,
                model: str = None) -> Dict[str, Any]:
        ollama_url = ollama_url or os.environ.get("OLLAMA_URL", "http://192.168.2.15:11434")
        model = model or os.environ.get("VISION_MODEL", "gemma4:31b-cloud")
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": question, "images": [image_b64]}
            ],
            "stream": False,
            "options": {"num_predict": 1024, "temperature": 0.3},
        }
        try:
            req = urllib.request.Request(
                f"{ollama_url}/api/chat",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode())
                return {
                    "answer": result.get("message", {}).get("content", ""),
                    "model": model,
                    "eval_count": result.get("eval_count", 0),
                }
        except Exception as e:
            return {"answer": f"ERROR: {e}", "model": model, "error": str(e)}
