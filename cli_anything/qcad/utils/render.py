"""Render DWG/DXF to PNG."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class QcadRenderer:
    """Render a DWG/DXF file to PNG using available tools."""

    def __init__(self, qcad_bin: Optional[str] = None, dpi: int = 150):
        self.qcad_bin = qcad_bin or self._find_qcad()
        self.dpi = dpi

    def _find_qcad(self) -> Optional[str]:
        candidates = [
            shutil.which("qcad"),
            str(Path.home() / "opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad"),
        ]
        for c in candidates:
            if c and Path(c).exists():
                return c
        return None

    def render(self, file_path: str, output_png: str) -> bool:
        """Stub: render DWG/DXF to PNG via QCAD or dwg2pdf."""
        # TODO: integrate full visual_verifier.py renderer logic
        return False
