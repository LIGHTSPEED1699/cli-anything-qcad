"""DWG ↔ DXF conversion using QCAD Pro headless ECMAScript."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from cli_anything.qcad.utils.layer_fix import fix_layer_visibility


class DwgConverter:
    """Convert between DWG and DXF; applies layer visibility fix on import."""

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
            str(Path.home() / "opt/qcad/qcad"),
            str(Path.home() / "opt/qcad/qcad-bin"),
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
        """DWG → DXF, then fix layer visibility."""
        success = self._dwg_to_dxf_raw(dwg_path, dxf_path)
        if success:
            fixed_dxf = dxf_path + ".fixed.dxf"
            fix_layer_visibility(dxf_path, fixed_dxf)
            shutil.move(fixed_dxf, dxf_path)
        return success

    def _dwg_to_dxf_raw(self, dwg_path: str, dxf_path: str) -> bool:
        if self.qcad_bin:
            return self._qcad_dwg_to_dxf(dwg_path, dxf_path)
        if self.oda_converter:
            return self._oda_dwg_to_dxf(dwg_path, dxf_path)
        raise RuntimeError("No DWG→DXF converter available (need QCAD Pro or ODA File Converter)")

    def dxf_to_dwg(self, dxf_path: str, dwg_path: str) -> bool:
        """DXF → DWG via QCAD Pro headless ECMAScript."""
        if self.qcad_bin:
            return self._qcad_dxf_to_dwg(dxf_path, dwg_path)
        if self.oda_converter:
            return self._oda_dxf_to_dwg(dxf_path, dwg_path)
        raise RuntimeError("No DXF→DWG converter available (need QCAD Pro or ODA File Converter)")

    def _oda_dwg_to_dxf(self, dwg_path: str, dxf_path: str) -> bool:
        # ODA File Converter operates on entire directories and refuses to
        # use the same dir for input and output ("Output folder must be
        # different than input folder"). When input and output share a
        # directory, run ODA with a sibling temp output dir, then move the
        # generated file into place.
        if not dwg_path.strip() or not Path(dwg_path).name:
            raise ValueError(f"Invalid dwg_path: {dwg_path!r}")
        if not dxf_path.strip():
            raise ValueError(f"Invalid dxf_path: {dxf_path!r}")
        src_stem = Path(dwg_path).stem
        if not src_stem:
            raise ValueError(f"dwg_path {dwg_path!r} has empty stem (name starts with dot?)")
        input_dir = Path(dwg_path).parent
        output_dir = Path(dxf_path).parent
        tmp_out = None
        if output_dir.resolve() == input_dir.resolve():
            tmp_out = input_dir / "_oda_out"
            tmp_out.mkdir(parents=True, exist_ok=True)
            oda_out_dir = tmp_out
        else:
            oda_out_dir = output_dir
        try:
            subprocess.run(
                [self.oda_converter, str(input_dir), str(oda_out_dir),
                 self.version, "DXF", "0", "1"],
                check=True, capture_output=True, text=True,
            )
            generated = oda_out_dir / f"{src_stem}.dxf"
            if generated.exists():
                shutil.move(str(generated), dxf_path)
                return True
            return False
        finally:
            if tmp_out and tmp_out.exists():
                shutil.rmtree(tmp_out, ignore_errors=True)

    def _oda_dxf_to_dwg(self, dxf_path: str, dwg_path: str) -> bool:
        if not dxf_path.strip() or not Path(dxf_path).name:
            raise ValueError(f"Invalid dxf_path: {dxf_path!r}")
        src_stem = Path(dxf_path).stem
        if not src_stem:
            raise ValueError(f"dxf_path {dxf_path!r} has empty stem (name starts with dot?)")
        input_dir = Path(dxf_path).parent
        output_dir = Path(dwg_path).parent
        tmp_out = None
        if output_dir.resolve() == input_dir.resolve():
            tmp_out = input_dir / "_oda_out"
            tmp_out.mkdir(parents=True, exist_ok=True)
            oda_out_dir = tmp_out
        else:
            oda_out_dir = output_dir
        try:
            subprocess.run(
                [self.oda_converter, str(input_dir), str(oda_out_dir),
                 self.version, "DWG", "0", "1"],
                check=True, capture_output=True, text=True,
            )
            generated = oda_out_dir / f"{src_stem}.dwg"
            if generated.exists():
                shutil.move(str(generated), dwg_path)
                return True
            return False
        finally:
            if tmp_out and tmp_out.exists():
                shutil.rmtree(tmp_out, ignore_errors=True)

    def _qcad_dwg_to_dxf(self, dwg_path: str, dxf_path: str) -> bool:
        safe_dwg = dwg_path.replace("'", "\\'")
        safe_dxf = dxf_path.replace("'", "\\'")
        script = f"""include(\"scripts/library.js\"); var storage = new RMemoryStorage(); var spatialIndex = new RSpatialIndexSimple(); var doc = new RDocument(storage, spatialIndex); var di = new RDocumentInterface(doc); var r1 = di.importFile('{safe_dwg}'); if (r1 !== RDocumentInterface.IoErrorNoError) {{ qcad.quit(1); }} di.exportFile('{safe_dxf}', 'DXF R2018'); QCoreApplication.quit(0);"""
        return self._run_qcad_script(script)

    def _qcad_dxf_to_dwg(self, dxf_path: str, dwg_path: str) -> bool:
        ecma_path = Path(__file__).with_suffix("").parent / "ecma" / "qcad_dxf2dwg.js"
        if ecma_path.exists():
            cmd = [
                self.qcad_bin,
                "-no-gui", "-platform", "offscreen",
                "-allow-multiple-instances",
                "-autostart", str(ecma_path),
                dxf_path, dwg_path,
            ]
            env = os.environ.copy()
            env.setdefault("QT_QPA_PLATFORM", "offscreen")
            qcad_dir = Path(self.qcad_bin).parent
            env["LD_LIBRARY_PATH"] = f"{qcad_dir}:{qcad_dir / 'plugins'}:{env.get('LD_LIBRARY_PATH', '')}"
            result = subprocess.run(cmd, capture_output=True, text=True, env=env)
            return result.returncode == 0 and Path(dwg_path).exists()

        # Fallback to inline script if ECMA file missing.
        # Includes layer-ON forcing to prevent ODA DWG writer from turning
        # layers OFF (negative colors), which makes cloned entities invisible.
        safe_dxf = dxf_path.replace("'", "\\'")
        safe_dwg = dwg_path.replace("'", "\\'")
        script = (
            'include("scripts/library.js"); '
            'var storage = new RMemoryStorage(); '
            'var spatialIndex = new RSpatialIndexSimple(); '
            'var doc = new RDocument(storage, spatialIndex); '
            'var di = new RDocumentInterface(doc); '
            f"var r1 = di.importFile('{safe_dxf}'); "
            'if (r1 !== RDocumentInterface.IoErrorNoError) { qcad.quit(1); } '
            'var lids = doc.queryAllLayers(); '
            'var lop = new RModifyObjectsOperation(); '
            'var lf = 0; '
            'for (var k = 0; k < lids.length; k++) { '
            '  var l = doc.queryLayer(lids[k]); if (!l) continue; '
            '  if (l.isOff()) { l.setOff(false); lop.addObject(l, false); lf++; } '
            '  if (l.isFrozen()) { l.setFrozen(false); lop.addObject(l, false); lf++; } '
            '} '
            'if (lf > 0) { di.applyOperation(lop); } '
            f"di.exportFile('{safe_dwg}', 'R32 (2018) DWG'); "
            'QCoreApplication.quit(0);'
        )
        return self._run_qcad_script(script)

    def _run_qcad_script(self, script: str) -> bool:
        qcad = self.qcad_bin
        if not qcad:
            return False
        with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
            f.write(script)
            script_path = f.name
        cmd = [qcad, "-no-gui", "-platform", "offscreen", "-allow-multiple-instances", "-autostart", script_path]
        env = os.environ.copy()
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        qcad_dir = Path(qcad).parent
        env["LD_LIBRARY_PATH"] = f"{qcad_dir}:{qcad_dir / 'plugins'}:{env.get('LD_LIBRARY_PATH', '')}"
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return result.returncode == 0
