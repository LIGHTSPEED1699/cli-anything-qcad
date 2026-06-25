"""T2 backend: QCAD Pro headless ECMAScript execution."""
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict


class QcadEcmaBackend:
    """Execute DWG-native edits via QCAD headless ECMAScript."""

    def __init__(self, qcad_bin: str = None):
        self.qcad_bin = qcad_bin

    def _find_qcad(self) -> str:
        if self.qcad_bin and Path(self.qcad_bin).exists():
            return self.qcad_bin
        import shutil
        candidates = [
            shutil.which("qcad"),
            str(Path.home() / "opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad"),
        ]
        for c in candidates:
            if c and Path(c).exists():
                return c
        raise RuntimeError("QCAD binary not found")

    def execute(self, annotation: Dict[str, Any], file_path: str) -> Dict[str, Any]:
        """Run a QCAD ECMAScript for this annotation."""
        return {
            "backend": "qcad_ecma",
            "success": False,
            "message": "T2 backend stub: generate and run per-category ECMAScript",
            "annotation": annotation.get("text"),
        }

    def run_script(self, script: str, file_path: str) -> Dict[str, Any]:
        """Execute an arbitrary ECMAScript against a DWG/DXF file."""
        qcad = self._find_qcad()
        with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
            f.write(script)
            script_path = f.name
        cmd = [qcad, "-platform", "offscreen", "-autostart", script_path]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return {"backend": "qcad_ecma", "success": True, "stdout": result.stdout}
        except subprocess.CalledProcessError as e:
            return {"backend": "qcad_ecma", "success": False, "error": e.stderr}
