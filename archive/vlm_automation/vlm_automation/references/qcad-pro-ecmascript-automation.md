# QCAD Pro Headless ECMAScript Automation

Session: 2026-05-08
Status: Validated on QCAD Pro Qt6, Ubuntu 24.04

**Note (2026-06-10):** This reference documents QCAD Pro 3.28/Qt5 era APIs. **QCAD Pro Qt6 has a completely different headless ECMAScript environment** — `RApplication`, `RDocumentInterface`, `Document`, and `RGlobal` are ALL undefined. Headless file I/O is NOT viable in the Qt6 build. See `references/qcad-headless-api-limitations-qt6.md` for the full forensic analysis and workaround options.

## Layer Manipulation (Thaw All Layers)

**Qt5 only. Does NOT work in QCAD Pro Qt6 headless mode.**

```javascript
// After di.importFile(inputFile) succeeds:
var op = new RModifyObjectsOperation();
var layerIds = doc.queryAllLayers();
for (var i = 0; i < layerIds.length; i++) {
    var layer = doc.queryLayer(layerIds[i]);
    if (layer !== null && layer.isFrozen()) {
        layer.setFrozen(false);
        op.addObject(layer, false);
        print("  Thawed: " + layer.getName());
    }
}
di.applyOperation(op);
// Then proceed to di.exportFile(outputFile, format)
```

**Critical API notes:**
- `doc.queryLayer(id)` returns the `RLayer` object
- `layer.isFrozen()` returns boolean
- `layer.setFrozen(false)` modifies the object in-memory
- `op.addObject(layer, false)` registers the change; the second argument is `useCurrentAttributes`
- `di.applyOperation(op)` commits all changes to the document
- `doc.setObjectId()` is NOT available — do not use it

## What Works

| Feature | Status | Notes |
|---------|--------|-------|
| `-no-gui` flag | ✅ | Required; prevents X11 connection |
| `-platform offscreen` | ✅ | Required on Qt6 builds; `-no-gui` alone is NOT sufficient |
| `print()` to stdout | ✅ | Visible in terminal output |
| `args[]` global arguments | ✅ | All argv, including qcad flags before script path |
| `RDocumentInterface.importFile()` | ✅ | Returns `RDocumentInterface.IoErrorNoError` on success |
| `RDocumentInterface.exportFile()` | ✅ | Returns boolean; uses ODA engine for DWG |
| `RModifyObjectsOperation` + layer thaw | ✅ | Use after import, before export; see above |
| File argument access | ✅ | `args[args.length - 2]` = input, `args[args.length - 1]` = output |

## What Does NOT Work

| Feature | Status | Notes |
|---------|--------|-------|
| `qcad` wrapper script | ❌ | Hardcodes `-platform xcb` at end of line; ignores your `-platform offscreen`. Always call `qcad-bin` directly. |
| `application.arguments` | ❌ | `application` is not defined in headless mode. Use the global `args[]` array. |
| LibreCAD (free) | ❌ | No ECMAScript, no `-no-gui`, no automation. Only QCAD Pro (~$42) has this. |
| `dwg2dwg` CLI helper | ❌ | Also calls `qcad` wrapper, crashes. Call `qcad-bin` with `-autostart` directly. |
| `-exec` flag | ⚠️ | Accepts script path but the official `-autostart` is more reliable for file I/O scripts. |
| `RTextData` full constructor | ❌ | Creates entity that never renders. Use setters: `td.setPosition()`, `td.setText()`, etc. |
| `entity.setPlainText()` | ❌ | "Property 'setPlainText' of object RTextEntity is not a function" |
| `entity.getData().setText()` then `entity.setData()` | ❌ | Data detached from entity, no visual output |
| LibreDWG roundtrip to verify text additions | ❌ | Drops MTEXT silently — use QCAD console output or visual inspection only |

## Correct Launcher Pattern

```bash
QCADDIR="$HOME/opt/qcad"
export QT_QPA_PLATFORM=offscreen
export QT_QPA_PLATFORMTHEME=""
export LD_LIBRARY_PATH="$QCADDIR:$QCADDIR/plugins"

"$QCADDIR/qcad-bin" \
  -no-gui \
  -allow-multiple-instances \
  -autostart /path/to/script.js \
  input.dxf \
  output.dwg
```

**Critical: Use `qcad-bin`, NOT `qcad`.** The wrapper script:
```bash
# qcad wrapper (WRONG — ignores your -platform flag)
QT_AUTO_SCREEN_SCALE_FACTOR=1 LD_LIBRARY_PATH="$DIR:$DIR/plugins" \
  "$binary" -platform xcb "$@"   # ← Always xcb!
```

## ECMAScript Templates

### Layer-Thaw Template (Fix A — Frozen Layers)

See `templates/qcad_ecmascript/convert_dxf2dwg_thaw.js` in the skill directory.

This template thaws all frozen layers after DXF import and before DWG export. It also detects and warns about layers that are OFF (negative color index) — those require the separate visibility fix script.

### Clean Conversion Template (Fix B — Pre-Fixed DXF)

See `templates/qcad_ecmascript/qcad_dxf2dwg.js` in the skill directory.

This template performs a straight DXF→DWG conversion with **no layer manipulation**. Use it when the input DXF has already been pre-processed by `fix_layer_visibility.py` (negative colors → positive). The resulting DWG opens in AutoCAD/TrueView without Recover errors because QCAD Pro's ODA engine writes clean DWG binaries, unlike LibreDWG `dxf2dwg`.

## Error Patterns

### "This application failed to start because no Qt platform plugin could be initialized"
→ You used `qcad` wrapper instead of `qcad-bin`. Always use `qcad-bin` with `QT_QPA_PLATFORM=offscreen`.

### "No output file. Try -h for help."
→ `dwg2dwg` helper script was called without input/output files, or the `-autostart` path is wrong.

### "application is not defined"
→ Using `application.arguments` instead of the global `args[]`. In headless QCAD, `application` is not available.

## Text Entity Creation (Adding Labels)

QCAD Pro ODA export converts `RTextEntity` to `MTEXT` in the DWG output. **LibreDWG `dwg2dxf` roundtrip silently drops MTEXT** — do not rely on roundtrip verification for text additions.

**Proven minimal pattern (confirmed V8/V9, 2026-05-12):**

```javascript
var td = new RTextData();
td.setPosition(new RVector(x, y));
td.setAlignmentPoint(new RVector(x, y));
td.setTextHeight(0.1);           // match existing label heights
td.setText("BLK");
td.setFontName("Standard");
td.setVAlign(RS.VAlignBase);
td.setHAlign(RS.HAlignLeft);
td.setDrawingDirection(RS.LeftToRight);
td.setLineSpacingStyle(RS.Exact);
td.setLineSpacingFactor(1.0);
var entity = new RTextEntity(doc, td);
entity.setLayerName("E-SYMB");
var ao = new RAddObjectsOperation();
ao.addObject(entity);
di.applyOperation(ao);
```

**What fails:**
- `RTextData` full constructor (position, alignmentPoint, height, width, valign, halign, ...) — silently creates empty/unusable entity
- `entity.setPlainText()` — "Property 'setPlainText' of object RTextEntity is not a function"
- `entity.getData().setText()` then `entity.setData()` — data detached from entity, no visual output
- Cloning existing entities and modifying — `clone()` + setData pattern fails silently

## Path Reference

| Path | Description |
|------|-------------|
| `$HOME/opt/qcad/qcad-bin` | Real binary |
| `$HOME/opt/qcad/qcad` | Wrapper script (BROKEN for headless) |
| `$HOME/opt/qcad/scripts/library.js` | Core utility includes |
| `$HOME/opt/qcad/scripts/Tools/arguments.js` | Argument parser (`testArgument()`) |
| `$HOME/.config/QCAD/QCAD3.conf` | User preferences |
