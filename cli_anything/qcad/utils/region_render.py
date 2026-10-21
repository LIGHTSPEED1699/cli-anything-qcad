"""Render a region of a DWG/DXF addressed in CAD units, with an exact pixel mapping.

The PDF-markup pipeline maps annotations through a PDF -> DXF -> pixel chain with a
border-calibration fallback, and it has burned three pairs on coordinate drift. This
skips the PDF entirely: dwg2bmp takes a window in drawing units, and the mapping
between CAD coordinates and output pixels is a plain affine.

    px = (cad_x - win_x) * (px_w / win_w)
    py = (win_y + win_h - cad_y) * (px_h / win_h)      # image y grows DOWN

Measured against crosshair probes planted at known coordinates: a probe at (15.0, 7.5)
recovered as (14.99, 7.61), one at (25.0, 12.5) as (24.99, 12.61).
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _find_dwg2bmp(qcad_bin: Optional[str] = None) -> Optional[str]:
    if qcad_bin:
        sibling = str(Path(qcad_bin).resolve().parent / "dwg2bmp")
        if Path(sibling).exists():
            return sibling
    found = shutil.which("dwg2bmp")
    if found:
        return found
    for cand in (Path.home() / "opt/qcad-3.32.7-pro-linux-qt6-x86_64/dwg2bmp",
                 Path.home() / "opt/qcad/dwg2bmp"):
        if cand.exists():
            return str(cand)
    return None


class RegionRenderer:
    """Render a CAD-coordinate window of a drawing to PNG."""

    def __init__(self, qcad_bin: Optional[str] = None, timeout: int = 120):
        self.qcad_bin = qcad_bin
        self.timeout = timeout

    def render(self, dwg_path: str, out_png: str, x: float, y: float, w: float, h: float,
               px_w: int = 1200, px_h: int = 800) -> Dict[str, Any]:
        """Render window (x, y, w, h) in CAD units to out_png."""
        exe = _find_dwg2bmp(self.qcad_bin)
        if not exe:
            return {"success": False, "error": "dwg2bmp not found"}
        out_png = str(Path(out_png).resolve())
        with tempfile.TemporaryDirectory() as tmp:
            tmp_in = Path(tmp) / Path(dwg_path).name
            shutil.copy(dwg_path, tmp_in)
            # dwg2bmp writes its output next to the input, ignoring -o
            cmd = [exe, "-f", "-a", "-b", "black", "-c", "-m", "0",
                   "-w", f"{x},{y},{w},{h}", "-x", str(px_w), "-y", str(px_h), str(tmp_in)]
            env = os.environ.copy()
            env["QT_QPA_PLATFORM"] = "offscreen"
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  env=env, timeout=self.timeout)
            produced = None
            for ext in (".png", ".bmp"):
                cand = tmp_in.with_suffix(ext)
                if cand.exists():
                    produced = cand
                    break
            if not produced:
                return {"success": False, "error": (proc.stdout or proc.stderr or "")[-400:]}
            Path(out_png).parent.mkdir(parents=True, exist_ok=True)
            if produced.suffix == ".png":
                shutil.copy(produced, out_png)
            else:
                conv = shutil.which("convert")
                if not conv:
                    return {"success": False, "error": "convert not found for bmp->png"}
                subprocess.run([conv, str(produced), out_png], capture_output=True, timeout=60)
        return {
            "success": Path(out_png).exists(),
            "png": out_png,
            "window": {"x": x, "y": y, "w": w, "h": h},
            "pixels": {"w": px_w, "h": px_h},
        }

    @staticmethod
    def cad_to_pixel(x: float, y: float, window: Dict[str, float],
                     pixels: Dict[str, int]) -> Tuple[float, float]:
        """CAD coordinates -> pixel coordinates in the rendered PNG."""
        px = (x - window["x"]) * (pixels["w"] / window["w"])
        py = (window["y"] + window["h"] - y) * (pixels["h"] / window["h"])
        return px, py

    @staticmethod
    def pixel_to_cad(px: float, py: float, window: Dict[str, float],
                     pixels: Dict[str, int]) -> Tuple[float, float]:
        """Pixel coordinates in the rendered PNG -> CAD coordinates."""
        x = window["x"] + px * (window["w"] / pixels["w"])
        y = window["y"] + window["h"] - py * (window["h"] / pixels["h"])
        return x, y
