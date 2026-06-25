"""DWG ↔ DXF conversion using QCAD Pro or ODA File Converter."""
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class DwgConverter:
    """Convert between DWG and DXF formats."""

    def __init__(
        self,
        qcad_bin: Optional[str] = None,
        oda_converter: Optional[str] = None,
        version: str = "ACAD2018",
    ):
        self.qcad_bin = qcad_bin or self._find_qcad()
        self.oda_converter = oda_converter or self._find_oda()
        self.version = version

    def _find_qcad(self) -> Optional[str]:
        candidates = [
            shutil.which("qcad"),
            str(Path.home() / "opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad"),
            str(Path.home() / "opt/qcad-pro/qcad"),
        ]
        for c in candidates:
            if c and Path(c).exists():
                return c
        return None

    def _find_oda(self) -> Optional[str]:
        candidates = [
            shutil.which("ODAFileConverter"),
            "/usr/bin/ODAFileConverter",
            "/usr/local/bin/ODAFileConverter",
        ]
        for c in candidates:
            if c and Path(c).exists():
                return c
        return None

    def dwg_to_dxf(self, dwg_path: str, dxf_path: str) -> bool:
        if self.qcad_bin:
            return self._qcad_dwg_to_dxf(dwg_path, dxf_path)
        if self.oda_converter:
            return self._oda_dwg_to_dxf(dwg_path, dxf_path)
        raise RuntimeError("No DWG→DXF converter available (need QCAD Pro or ODA File Converter)")

    def dxf_to_dwg(self, dxf_path: str, dwg_path: str) -> bool:
        if self.oda_converter:
            return self._oda_dxf_to_dwg(dxf_path, dwg_path)
        if self.qcad_bin:
            return self._qcad_dxf_to_dwg(dxf_path, dwg_path)
        raise RuntimeError("No DXF→DWG converter available (need ODA File Converter or QCAD Pro)")

    def _oda_dwg_to_dxf(self, dwg_path: str, dxf_path: str) -> bool:
        input_dir = Path(dwg_path).parent
        output_dir = Path(dxf_path).parent
        cmd = [
            self.oda_converter,
            str(input_dir), str(output_dir),
            self.version, "DXF", "0", "1",
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        generated = output_dir / (Path(dwg_path).stem + ".dxf")
        if generated.exists():
            generated.rename(dxf_path)
            return True
        return False

    def _oda_dxf_to_dwg(self, dxf_path: str, dwg_path: str) -> bool:
        input_dir = Path(dxf_path).parent
        output_dir = Path(dwg_path).parent
        cmd = [
            self.oda_converter,
            str(input_dir), str(output_dir),
            self.version, "DWG", "0", "1",
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        generated = output_dir / (Path(dxf_path).stem + ".dwg")
        if generated.exists():
            generated.rename(dwg_path)
            return True
        return False

    def _qcad_dwg_to_dxf(self, dwg_path: str, dxf_path: str) -> bool:
        # QCAD headless import/export via ECMAScript or built-in command
        safe_dwg = dwg_path.replace("'", "\\'")
        safe_dxf = dxf_path.replace("'", "\\'")
        script = f"""var doc = new Document('{safe_dwg}'); doc.saveAs('{safe_dxf}', 'DXF R2018');"""
        with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
            f.write(script)
            script_path = f.name
        cmd = [self.qcad_bin, "-platform", "offscreen", "-autostart", script_path]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return Path(dxf_path).exists()

    def _qcad_dxf_to_dwg(self, dxf_path: str, dwg_path: str) -> bool:
        safe_dxf = dxf_path.replace("'", "\\'")
        safe_dwg = dwg_path.replace("'", "\\'")
        script = f"""var doc = new Document('{safe_dxf}'); doc.saveAs('{safe_dwg}', 'DWG R2018');"""
        with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
            f.write(script)
            script_path = f.name
        cmd = [self.qcad_bin, "-platform", "offscreen", "-autostart", script_path]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return Path(dwg_path).exists()
