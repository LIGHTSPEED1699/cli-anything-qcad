"""Visual verification: render and compare DWG/DXF outputs."""
import base64
import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image, ImageChops


class QcadRenderer:
    """Render DWG/DXF to PNG using QCAD's dwg2bmp CLI."""

    def __init__(self, qcad_dir: Optional[str] = None):
        self.qcad_dir = Path(qcad_dir) if qcad_dir else Path(os.environ.get('QCAD_DIR', 'qcad'))
        self.dwg2bmp = self.qcad_dir / 'dwg2bmp'
        self._converter = None

    def render(self, file_path: str, output_png: str, resolution: int = 150) -> bool:
        out_bmp = Path(output_png).with_suffix('.bmp')
        cmd = [str(self.dwg2bmp), '-o', str(out_bmp), '-r', str(resolution), file_path]
        env = os.environ.copy()
        env.setdefault('DISPLAY', ':0')
        env.setdefault('QT_QPA_PLATFORM', 'offscreen')
        env['LD_LIBRARY_PATH'] = f"{self.qcad_dir}:{self.qcad_dir / 'plugins'}:{env.get('LD_LIBRARY_PATH', '')}"
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
            if not out_bmp.exists() or out_bmp.stat().st_size == 0:
                return False
            Image.open(out_bmp).convert('RGB').save(output_png)
            return True
        except Exception:
            return False

    def compare(
        self,
        original_path: str,
        modified_path: str,
        output_png: str,
        converter=None,
    ) -> Dict[str, Any]:
        # dwg2bmp needs DWG input; convert DXF if necessary.
        orig = self._ensure_dwg(original_path, converter)
        mod = self._ensure_dwg(modified_path, converter)
        for p in [original_path, modified_path]:
            if not Path(p).stem:
                raise ValueError(f"Path {p!r} has empty stem")
        orig_png = str(Path(original_path).with_suffix('')) + '_orig_render.png'
        mod_png = str(Path(modified_path).with_suffix('')) + '_mod_render.png'
        ok1 = self.render(orig, orig_png)
        ok2 = self.render(mod, mod_png)
        if not (ok1 and ok2):
            return {"error": "Rendering failed"}
        return self._pixel_compare(orig_png, mod_png, output_png)

    def _ensure_dwg(self, path: str, converter=None) -> str:
        if Path(path).suffix.lower() == ".dwg":
            return path
        if converter is None:
            if self._converter is None:
                from cli_anything.qcad.backends.dwg_converter import DwgConverter
                self._converter = DwgConverter()
            converter = self._converter
        out_dwg = str(Path(path).with_suffix(".dwg"))
        if not Path(out_dwg).exists():
            if not converter.dxf_to_dwg(path, out_dwg):
                return path  # fallback, render may still fail
        return out_dwg

    @staticmethod
    def _pixel_compare(orig_png: str, mod_png: str, output_png: str) -> Dict[str, Any]:
        try:
            orig = Image.open(orig_png).convert('RGB')
            mod = Image.open(mod_png).convert('RGB')
            w = min(orig.width, mod.width)
            h = min(orig.height, mod.height)
            orig_c = orig.crop((0, 0, w, h))
            mod_c = mod.crop((0, 0, w, h))

            diff = ImageChops.difference(orig_c.convert('L'), mod_c.convert('L'))
            import numpy as np
            np_diff = np.array(diff)
            mask = np_diff > 20
            pct = 100.0 * mask.sum() / mask.size if mask.size else 0.0

            strip = Image.new('RGB', (w * 3, h))
            strip.paste(orig_c, (0, 0))
            strip.paste(mod_c, (w, 0))
            red = np.array(orig_c)
            red[mask] = [255, 0, 0]
            strip.paste(Image.fromarray(red), (w * 2, 0))
            strip.save(output_png)

            return {
                "pixel_change_pct": round(pct, 2),
                "status": "CHANGED" if pct > 0.1 else "UNCHANGED",
                "original_png": orig_png,
                "modified_png": mod_png,
                "output_png": output_png,
                "diff_pixels": int(mask.sum()),
            }
        except Exception as e:
            return {"error": str(e)}


class QcadVlmVerifier:
    """Render DWG and ask a yes/no VLM question via Ollama."""

    def __init__(self, qcad_bin: str = None, model: str = None):
        qcad_dir = None
        if qcad_bin:
            qcad_dir = str(Path(qcad_bin).parent)
        self.renderer = QcadRenderer(qcad_dir=qcad_dir)
        self.model = model or os.environ.get("VISION_MODEL", "gemma4:31b-cloud")
        self.base_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")

    def verify(self, dwg_path: str, question: str) -> dict:
        png_path = str(Path(dwg_path).with_suffix('')) + '_vlm.png'
        ok = self.renderer.render(dwg_path, png_path)
        if not ok:
            return {"error": "rendering failed", "answer": None}
        with open(png_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Answer yes/no questions about CAD drawing screenshots. Only answer YES or NO."},
                {"role": "user", "content": question, "images": [b64]},
            ],
            "stream": False,
            "options": {"num_predict": 64, "temperature": 0.1},
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
        answer = result.get("message", {}).get("content", "")
        return {"answer": answer, "model": self.model}
