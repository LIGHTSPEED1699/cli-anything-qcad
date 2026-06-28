"""Visual verification: render and compare DWG/DXF outputs."""
import json
import os
import subprocess
import base64
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageChops


class QcadRenderer:
    """Render DWG/DXF to PNG using QCAD's dwg2bmp CLI."""

    def __init__(self, qcad_dir: Optional[str] = None):
        self.qcad_dir = Path(qcad_dir) if qcad_dir else Path('/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64')
        self.dwg2bmp = self.qcad_dir / 'dwg2bmp'

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

    def compare(self, original_path: str, modified_path: str, output_png: str) -> Dict[str, Any]:
        orig_png = str(Path(original_path).with_suffix('')) + '_orig_render.png'
        mod_png = str(Path(modified_path).with_suffix('')) + '_mod_render.png'
        ok1 = self.render(original_path, orig_png)
        ok2 = self.render(modified_path, mod_png)
        if not (ok1 and ok2):
            return {"error": "Rendering failed"}
        return self._pixel_compare(orig_png, mod_png, output_png)

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
