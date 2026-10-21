"""QCAD headless ECMAScript runner: import, mutate and export a DWG/DXF.

This is the T3 backend. It replaces the DWG -> DXF -> ezdxf -> DWG round-trip with a
direct DWG-native edit, so blocks, attributes and handles survive untouched.

The JS is generated per call so the caller never has to know the ECMAScript API.
See the qcad-headless-scripting skill for the API traps this encodes.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Traps encoded here, all measured:
#   - queryEntityDirect, never queryEntity (queryEntity silently drops edits)
#   - new entities: add to the document FIRST, then move() (setPosition is a no-op)
#   - new geometry: RLineEntity with absolute coords (polylines cannot be authored)
#   - set the layer AFTER insertion (setLayerName before insertion is a no-op)
#   - getArgument() does not exist: paths are interpolated into the script

_PRELUDE = 'include("scripts/library.js");\n'

_JSON = "JSON"


def _qcad_dir(qcad_bin: Optional[str] = None) -> Path:
    if qcad_bin:
        return Path(qcad_bin).resolve().parent
    env = os.environ.get("QCAD_DIR")
    if env:
        return Path(env).expanduser()
    for cand in (Path.home() / "opt/qcad-3.32.7-pro-linux-qt6-x86_64",
                 Path.home() / "opt/qcad"):
        if cand.exists():
            return cand
    return Path.home() / "opt/qcad"


def _js_str(s: str) -> str:
    """Escape a Python string for embedding in an ECMAScript double-quoted literal."""
    return json.dumps(str(s))


def _js_json(value: Any) -> str:
    """Embed a JSON-serialisable value as an ECMAScript literal.

    Not the same as _js_str: JSON is valid JS literal syntax, so the value must go
    in unquoted or JS iterates over its characters.
    """
    return json.dumps(value, ensure_ascii=False)


class QcadHeadless:
    """Run ECMAScript against QCAD's scripting engine, headless."""

    def __init__(self, qcad_bin: Optional[str] = None, timeout: int = 250):
        self.qcad_dir = _qcad_dir(qcad_bin)
        self.qcad_bin = str(self.qcad_dir / "qcad")
        self.timeout = timeout
        if not Path(self.qcad_bin).exists():
            raise FileNotFoundError(f"qcad not found at {self.qcad_bin}")

    def run_script(self, script: str) -> Dict[str, Any]:
        """Execute ECMAScript; return the last JSON object printed as 'JSON {...}'."""
        with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
            f.write(script)
            script_path = f.name
        try:
            env = os.environ.copy()
            env["QT_QPA_PLATFORM"] = "offscreen"
            env["LD_LIBRARY_PATH"] = f"{self.qcad_dir}:{self.qcad_dir / 'plugins'}" + (
                f":{env['LD_LIBRARY_PATH']}" if env.get("LD_LIBRARY_PATH") else "")
            cmd = [self.qcad_bin, "-no-gui", "-platform", "offscreen",
                   "-allow-multiple-instances", "-autostart", script_path]
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  env=env, timeout=self.timeout)
            out = (proc.stdout or "") + (proc.stderr or "")
            result: Optional[Dict[str, Any]] = None
            for line in out.splitlines():
                line = line.strip()
                if line.startswith(_JSON + " "):
                    try:
                        payload = json.loads(line[len(_JSON) + 1:])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        result = payload
            if result is None:
                tail = "\n".join(out.splitlines()[-15:])
                raise RuntimeError(f"QCAD script produced no JSON result.\n{tail}")
            return result
        finally:
            Path(script_path).unlink(missing_ok=True)

    # ---- higher level helpers -------------------------------------------------

    def index(self, dwg_path: str) -> Dict[str, Any]:
        """Entity index: handles, layers, blocks, text and CAD positions."""
        script = _PRELUDE + f"""
function out(o) {{ print("JSON " + JSON.stringify(o)); }}
try {{
  var doc = new RDocument(new RMemoryStorage(), new RSpatialIndexSimple());
  var di = new RDocumentInterface(doc);
  var err = di.importFile({_js_str(dwg_path)});
  if (err !== RDocumentInterface.IoErrorNoError) {{
    out({{error: "import_failed", code: err}});
  }} else {{
    var ids = doc.queryAllEntities();
    var rows = [];
    var perLayer = {{}};
    var withText = 0, withPos = 0;
    for (var i = 0; i < ids.length; i++) {{
      var e = doc.queryEntityDirect(ids[i]);
      if (!e) continue;
      var layer = e.getLayerName();
      perLayer[layer] = (perLayer[layer] || 0) + 1;
      var row = {{h: e.getHandle(), layer: layer, block: e.getBlockName()}};
      try {{ row.type = e.getType(); }} catch (ex) {{}}
      var t = null;
      try {{ t = e.getPlainText(); }} catch (ex) {{}}
      if (t) {{ row.t = t; withText++; }}
      // A newly created MTEXT keeps its position in the alignment point; an
      // original one has both. Read both so a note we just added is not at 0,0.
      var pos = null, align = null;
      try {{ var ap = e.getAlignmentPoint(); align = [ap.x, ap.y]; }} catch (ex) {{}}
      try {{ var pp = e.getPosition(); pos = [pp.x, pp.y]; }} catch (ex) {{}}
      var use = pos;
      if (!use || (use[0] === 0 && use[1] === 0)) {{ use = align; }}
      if (use) {{ row.x = use[0]; row.y = use[1]; withPos++; }}
      if (align) {{ row.ax = align[0]; row.ay = align[1]; }}
      try {{ var s = e.getStartPoint(), q = e.getEndPoint();
            row.x1 = s.x; row.y1 = s.y; row.x2 = q.x; row.y2 = q.y; }} catch (ex) {{}}
      try {{ row.r = e.getRadius(); }} catch (ex) {{}}
      try {{ row.len = e.getLength(); }} catch (ex) {{}}
      rows.push(row);
    }}
    out({{entities: rows.length, withText: withText, withPosition: withPos,
          layers: doc.getLayerNames(), blocks: doc.getBlockNames().length,
          perLayer: perLayer, rows: rows}});
  }}
}} catch (ex) {{ out({{error: String(ex)}}); }}
qcad.quit(0);
"""
        return self.run_script(script)

    def find_text(self, dwg_path: str, needle: str) -> List[Dict[str, Any]]:
        """Entities whose text contains `needle` (case-insensitive)."""
        idx = self.index(dwg_path)
        n = needle.lower()
        return [r for r in idx.get("rows", []) if r.get("t") and n in r["t"].lower()]

    def find_near(self, dwg_path: str, x: float, y: float, radius: float = 1.0
                  ) -> List[Dict[str, Any]]:
        """Entities within `radius` CAD units of (x, y), nearest first.

        queryClosestXY() returns undefined in this API, so this scans the index.
        """
        idx = self.index(dwg_path)
        hits = []
        for r in idx.get("rows", []):
            if r.get("x") is None:
                continue
            d = ((r["x"] - x) ** 2 + (r["y"] - y) ** 2) ** 0.5
            if d <= radius:
                r = dict(r)
                r["dist"] = round(d, 4)
                hits.append(r)
        hits.sort(key=lambda r: r["dist"])
        return hits

    def apply(self, dwg_path: str, out_dwg: str, ops: List[Dict[str, Any]],
              audit: bool = True) -> Dict[str, Any]:
        """Apply a list of operations to a DWG/DXF and write a DWG.

        Ops are dicts:
          {"op": "set_text", "handle": 888, "text": "NEW"}
          {"op": "delete",   "handle": 888}
          {"op": "move",     "handle": 888, "dx": 1.0, "dy": 2.0}
          {"op": "add_text", "text": "...", "x": 26.0, "y": 12.5, "layer": "E-TEXT"}
          {"op": "add_line", "x1": 26, "y1": 8, "x2": 30, "y2": 12, "color": [255,0,0]}
          {"op": "add_box",  "x": 26, "y": 8, "w": 4, "h": 4, "color": [255,0,0]}

        The script reports per-op "ok", which only means the call did not throw.
        With audit=True the output file is re-read and every op is checked against
        it; success requires the audit to pass.
        """
        before_rows = self.index(dwg_path).get("rows", [])
        script = _PRELUDE + f"""
function out(o) {{ print("JSON " + JSON.stringify(o)); }}
function add(doc, di, e) {{ var op = new RAddObjectsOperation(); op.addObject(e, false); di.applyOperation(op); }}
try {{
  var doc = new RDocument(new RMemoryStorage(), new RSpatialIndexSimple());
  var di = new RDocumentInterface(doc);
  var err = di.importFile({_js_str(dwg_path)});
  if (err !== RDocumentInterface.IoErrorNoError) {{ out({{error: "import_failed", code: err}}); qcad.quit(0); }}

  var ops = {_js_json(ops)};
  var ids = doc.queryAllEntities();
  var byHandle = {{}};
  for (var i = 0; i < ids.length; i++) {{
    var e = doc.queryEntityDirect(ids[i]);
    if (!e) continue;
    byHandle[String(e.getHandle())] = e;
  }}
  var report = [];
  for (var o = 0; o < ops.length; o++) {{
    var op = ops[o];
    var kind = op.op;
    try {{
      if (kind === "set_text") {{
        var e1 = byHandle[String(op.handle)];
        if (!e1) {{ report.push({{op: kind, handle: op.handle, ok: false, error: "handle not found"}}); continue; }}
        e1.setText(op.text);
        e1.update();
        report.push({{op: kind, handle: op.handle, ok: true, value: op.text}});
      }} else if (kind === "delete") {{
        var e2 = byHandle[String(op.handle)];
        if (!e2) {{ report.push({{op: kind, handle: op.handle, ok: false, error: "handle not found"}}); continue; }}
        var dop = new RDeleteObjectsOperation();
        dop.deleteObject(e2);
        // dop.apply() is a no-op here. The document interface must apply it.
        di.applyOperation(dop);
        report.push({{op: kind, handle: op.handle, ok: true}});
      }} else if (kind === "move") {{
        var e3 = byHandle[String(op.handle)];
        if (!e3) {{ report.push({{op: kind, handle: op.handle, ok: false, error: "handle not found"}}); continue; }}
        e3.move(new RVector(op.dx, op.dy));
        e3.update();
        report.push({{op: kind, handle: op.handle, ok: true, dx: op.dx, dy: op.dy}});
      }} else if (kind === "add_text") {{
        var te = new RTextEntity(doc, new RTextData());
        // setTextHeight is REQUIRED: a text entity with height 0 exports with
        // empty text, whatever it reads back as in memory. Always set it.
        te.setTextHeight(op.height ? op.height : 1.0);
        te.setText(op.text);
        add(doc, di, te);
        // setPosition is a no-op on a new entity: add first, then move
        te.move(new RVector(op.x, op.y));
        if (op.layer) te.setLayerName(op.layer);
        if (op.color) te.setColor(new RColor(op.color[0], op.color[1], op.color[2]));
        report.push({{op: kind, ok: true, x: op.x, y: op.y, text: op.text}});
      }} else if (kind === "add_line") {{
        var ln = new RLineEntity(doc, new RLineData(new RVector(op.x1, op.y1), new RVector(op.x2, op.y2)));
        if (op.color) ln.setColor(new RColor(op.color[0], op.color[1], op.color[2]));
        add(doc, di, ln);
        report.push({{op: kind, ok: true}});
      }} else if (kind === "add_box") {{
        // polylines cannot be authored in this API: draw 4 line segments
        var x0 = op.x, y0 = op.y, x1 = op.x + op.w, y1 = op.y + op.h;
        var corners = [[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]];
        for (var s = 0; s + 1 < corners.length; s++) {{
          var seg = new RLineEntity(doc, new RLineData(
            new RVector(corners[s][0], corners[s][1]),
            new RVector(corners[s+1][0], corners[s+1][1])));
          if (op.color) seg.setColor(new RColor(op.color[0], op.color[1], op.color[2]));
          add(doc, di, seg);
        }}
        report.push({{op: kind, ok: true, x: x0, y: y0, w: op.w, h: op.h}});
      }} else {{
        report.push({{op: kind, ok: false, error: "unknown op"}});
      }}
    }} catch (ex) {{
      report.push({{op: kind, ok: false, error: String(ex)}});
    }}
  }}
  var exp = di.exportFile({_js_str(out_dwg)}, "R32 (2018) DWG");
  out({{success: true, ops: report, entities: doc.queryAllEntities().length,
        exported: exp === undefined ? true : exp}});
}} catch (ex) {{ out({{error: String(ex)}}); }}
qcad.quit(0);
"""
        result = self.run_script(script)
        if "error" in result:
            return result

        if audit:
            from cli_anything.qcad.backends.apply_audit import audit_ops
            after_rows = self.index(out_dwg).get("rows", [])
            report = audit_ops(ops, before_rows, after_rows)
            result["audit"] = report
            result["success"] = report["passed"]
            result["verified"] = report["verified"]
            result["failed"] = report["failed"]
        return result
