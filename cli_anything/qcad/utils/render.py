"""Render DWG/DXF to PNG."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class QcadRenderer:
    """Render a DWG/DXF file to PNG using available tools."""

    def __init__(self, qcad_bin: Optional[str] = None, dpi: int = 150, width: int = 1600):
        self.qcad_bin = qcad_bin or self._find_qcad()
        self.dpi = dpi
        self.width = width

    def _find_qcad(self) -> Optional[str]:
        candidates = [
            shutil.which("qcad"),
            str(Path.home() / "opt/qcad/qcad"),
            str(Path.home() / "opt/qcad/qcad-bin"),
        ]
        for c in candidates:
            if c and Path(c).exists():
                return c
        return None

    def _find_qcad_child(self, name: str) -> Optional[str]:
        if not self.qcad_bin:
            return shutil.which(name)
        sibling = str(Path(self.qcad_bin).parent / name)
        if Path(sibling).exists():
            return sibling
        return shutil.which(name)

    def render(self, file_path: str, output_png: str) -> bool:
        """Render DWG/DXF to PNG.

        Priority:
          1. dwg2bmp from QCAD Pro (headless rasterizer, fastest).
          2. dwg2pdf + ImageMagick convert.
          3. QCAD headless export to PNG via ECMAScript.
        """
        file_path = str(Path(file_path).resolve())
        output_png = str(Path(output_png).resolve())

        if self._render_dwg2bmp(file_path, output_png):
            return True
        if self._render_pdf_convert(file_path, output_png):
            return True
        if self.qcad_bin and self._render_qcad_script(file_path, output_png):
            return True
        return False

    def _render_qcad_script(self, file_path: str, output_png: str) -> bool:
        if not self.qcad_bin:
            return False
        qd = Path(self.qcad_bin).parent
        safe_file = file_path.replace("'", "\\'")
        safe_png = output_png.replace("'", "\\'")
        script = (
            'include("scripts/library.js");\n'
            'function main() {\n'
            f'  var file = "{safe_file}";\n'
            f'  var outPng = "{safe_png}";\n'
            '  var storage = new RMemoryStorage();\n'
            '  var spatialIndex = new RSpatialIndexSimple();\n'
            '  var doc = new RDocument(storage, spatialIndex);\n'
            '  var di = new RDocumentInterface(doc);\n'
            '  var err = di.importFile(file);\n'
            '  if (err !== RDocumentInterface.IoErrorNoError) { qcad.quit(1); }\n'
            '  var view = new RGraphicsViewQt(di);\n'
            '  view.zoomToEntities();\n'
            '  var scene = view.getScene();\n'
            f'  var img = scene.renderToImage({self.width}, 0);\n'
            '  img.save(outPng, "PNG");\n'
            '  qcad.quit(0);\n'
            '}\n'
            'if (typeof(including) === "undefined" || !including) main();\n'
        )
        with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
            f.write(script)
            script_path = f.name
        env = os.environ.copy()
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        env.setdefault("DISPLAY", os.environ.get("DISPLAY", ":0"))
        env.setdefault("LD_LIBRARY_PATH", f"{qd}:{qd}/plugins")
        cmd = [self.qcad_bin, "-no-gui", "-platform", "offscreen",
               "-allow-multiple-instances", "-autostart", script_path]
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
        return result.returncode == 0 and Path(output_png).exists()

    def _render_dwg2bmp(self, file_path: str, output_png: str) -> bool:
        exe = self._find_qcad_child("dwg2bmp")
        if not exe:
            return False
        in_path = Path(file_path)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_input = Path(tmpdir) / in_path.name
            shutil.copy(file_path, tmp_input)
            out_bmp = Path(tmpdir) / (in_path.stem + ".bmp")
            # Use explicit width/height with -zoom-all instead of bare -x/-r flags.
            # dwg2bmp writes output next to input (ignores -o), so we copy to tmpdir.
            cmd = [exe, "-x", str(self.width), "-y", "1200",
                   "-zoom-all", "-m", "0", "-f", str(tmp_input)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0 or not out_bmp.exists():
                return False
            subprocess.run(["convert", str(out_bmp), output_png], capture_output=True, timeout=60)
            return Path(output_png).exists()

    def _render_pdf_convert(self, file_path: str, output_png: str) -> bool:
        exe = self._find_qcad_child("dwg2pdf")
        if not exe:
            return False
        in_path = Path(file_path)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_input = Path(tmpdir) / in_path.name
            shutil.copy(file_path, tmp_input)
            out_pdf = Path(tmpdir) / (in_path.stem + ".pdf")
            cmd = [exe, "-x", "-f", str(tmp_input)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0 or not out_pdf.exists():
                return False
            subprocess.run(["convert", "-density", str(self.dpi), str(out_pdf), output_png],
                          capture_output=True, timeout=120)
            return Path(output_png).exists()
