"""T2 backend: QCAD Pro headless ECMAScript execution."""
import os
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
        category = annotation.get("category", "")
        text = annotation.get("text", "").lower()

        if category == "move" or "move" in text:
            return self.move_entities(annotation, file_path)
        if category == "add" or "add" in text:
            return self.add_entities(annotation, file_path)
        if category == "block_swap" or "block" in text:
            return self.swap_block(annotation, file_path)
        if category == "reorder" or "reorder" in text:
            return self.reorder_entities(annotation, file_path)

        return {
            "backend": "qcad_ecma",
            "success": False,
            "message": "No matching T2/T3 action; falls back to VLM",
            "annotation": annotation.get("text"),
        }

    def run_script(self, script: str, file_path: str, out_path: str = None) -> Dict[str, Any]:
        """Execute an arbitrary ECMAScript against a DWG/DXF file.

        If out_path is not given, the script is expected to overwrite file_path.
        """
        qcad = self._find_qcad()
        qd = Path(qcad).parent
        with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
            f.write(script)
            script_path = f.name
        out_path = out_path or file_path
        cmd = [qcad, "-no-gui", "-platform", "offscreen",
               "-allow-multiple-instances", "-autostart", script_path,
               file_path, out_path]
        env = {
            "HOME": os.environ.get("HOME", "/root"),
            "QT_QPA_PLATFORM": "offscreen",
            "DISPLAY": os.environ.get("DISPLAY", ":0"),
            "LD_LIBRARY_PATH": f"{qd}:{qd}/plugins",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True, env=env, timeout=120)
            return {"backend": "qcad_ecma", "success": True, "stdout": result.stdout, "output": out_path}
        except subprocess.CalledProcessError as e:
            return {"backend": "qcad_ecma", "success": False, "error": e.stderr, "stdout": e.stdout}
        except subprocess.TimeoutExpired as e:
            return {"backend": "qcad_ecma", "success": False, "error": f"timeout: {e}"}
        finally:
            Path(script_path).unlink(missing_ok=True)

    def move_entities(self, annotation: Dict[str, Any], file_path: str) -> Dict[str, Any]:
        """Move selected entities by dx/dy via ECMAScript."""
        dx = annotation.get("dx", 0.0)
        dy = annotation.get("dy", 0.0)
        handle_filter = annotation.get("handles", "")
        script = (
            'include("scripts/library.js");\n'
            'function main() {\n'
            '  var storage = new RMemoryStorage();\n'
            '  var spatialIndex = new RSpatialIndexSimple();\n'
            '  var doc = new RDocument(storage, spatialIndex);\n'
            '  var di = new RDocumentInterface(doc);\n'
            '  var err = di.importFile(getArgument(0));\n'
            '  if (err !== RDocumentInterface.IoErrorNoError) { qcad.quit(1); }\n'
            '  var ids = doc.queryAllEntities();\n'
            f'  var filter = "{handle_filter}";\n'
            f'  var dx = {dx}; var dy = {dy};\n'
            '  var op = new RModifyObjectsOperation();\n'
            '  for (var i = 0; i < ids.length; i++) {\n'
            '    var e = doc.queryEntity(ids[i]);\n'
            '    if (!e) continue;\n'
            '    if (filter && e.getHandle().toString() !== filter) continue;\n'
            '    e.move(new RVector(dx, dy));\n'
            '    op.addObject(e, false);\n'
            '  }\n'
            '  di.applyOperation(op);\n'
            '  di.exportFile(getArgument(1), "DXF R2018");\n'
            '  qcad.quit(0);\n'
            '}\n'
            'if (typeof(including) === "undefined" || !including) main();\n'
        )
        return self.run_script(script, file_path, file_path)

    def add_entities(self, annotation: Dict[str, Any], file_path: str) -> Dict[str, Any]:
        """Add a simple LINE entity at given coordinates."""
        x1 = annotation.get("x1", 0.0)
        y1 = annotation.get("y1", 0.0)
        x2 = annotation.get("x2", 0.0)
        y2 = annotation.get("y2", 0.0)
        script = (
            'include("scripts/library.js");\n'
            'function main() {\n'
            '  var storage = new RMemoryStorage();\n'
            '  var spatialIndex = new RSpatialIndexSimple();\n'
            '  var doc = new RDocument(storage, spatialIndex);\n'
            '  var di = new RDocumentInterface(doc);\n'
            '  var err = di.importFile(getArgument(0));\n'
            '  if (err !== RDocumentInterface.IoErrorNoError) { qcad.quit(1); }\n'
            '  var line = new RLineEntity(doc, new RLineData(new RVector(x1, y1), new RVector(x2, y2)));\n'
            '  var op = new RAddObjectsOperation();\n'
            '  op.addObject(line, false);\n'
            '  di.applyOperation(op);\n'
            '  di.exportFile(getArgument(1), "DXF R2018");\n'
            '  qcad.quit(0);\n'
            '}\n'
            f'var x1 = {x1}; var y1 = {y1}; var x2 = {x2}; var y2 = {y2};\n'
            'if (typeof(including) === "undefined" || !including) main();\n'
        )
        return self.run_script(script, file_path, file_path)

    def swap_block(self, annotation: Dict[str, Any], file_path: str) -> Dict[str, Any]:
        """Replace block references: old_name -> new_name."""
        old_name = annotation.get("old", "")
        new_name = annotation.get("new", "")
        script = (
            'include("scripts/library.js");\n'
            'function main() {\n'
            '  var storage = new RMemoryStorage();\n'
            '  var spatialIndex = new RSpatialIndexSimple();\n'
            '  var doc = new RDocument(storage, spatialIndex);\n'
            '  var di = new RDocumentInterface(doc);\n'
            '  var err = di.importFile(getArgument(0));\n'
            '  if (err !== RDocumentInterface.IoErrorNoError) { qcad.quit(1); }\n'
            '  var ids = doc.queryAllBlockReferences();\n'
            f'  var oldName = "{old_name}";\n'
            f'  var newName = "{new_name}";\n'
            '  var op = new RModifyObjectsOperation();\n'
            '  var count = 0;\n'
            '  for (var i = 0; i < ids.length; i++) {\n'
            '    var e = doc.queryEntity(ids[i]);\n'
            '    if (!e || !e.data) continue;\n'
            '    if (e.data.getBlockName() === oldName) {\n'
            '      e.data.setBlockName(newName);\n'
            '      op.addObject(e, false);\n'
            '      count++;\n'
            '    }\n'
            '  }\n'
            '  di.applyOperation(op);\n'
            '  di.exportFile(getArgument(1), "DXF R2018");\n'
            '  qcad.quit(0);\n'
            '}\n'
            'if (typeof(including) === "undefined" || !including) main();\n'
        )
        return self.run_script(script, file_path, file_path)

    def reorder_entities(self, annotation: Dict[str, Any], file_path: str) -> Dict[str, Any]:
        """Reorder entities via draw-order operation (not fully supported in headless)."""
        return {
            "backend": "qcad_ecma",
            "success": False,
            "message": "Draw-order reorder requires GUI or unsupported API",
        }
