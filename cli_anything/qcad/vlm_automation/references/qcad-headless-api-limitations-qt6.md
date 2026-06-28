# QCAD Pro Headless ECMAScript API Limitations (2026-06-10)

**QCAD Pro 3.32.7 Qt6 build — headless ECMAScript automation is NOT viable for file I/O.**

## What Is Missing

The following APIs — documented in QCAD's official scripting reference and used in every online example — are **undefined** in the headless ECMAScript environment:

| API | Expected Role | Actual Status | Error |
|-----|--------------|---------------|-------|
| `RApplication` | Application singleton, file I/O scheduling | ❌ Undefined | `ReferenceError: RApplication is not defined` |
| `RDocumentInterface` | Document open/import/export | ❌ Undefined | `ReferenceError: RDocumentInterface is not defined` |
| `Document` | Alternative document API | ❌ Undefined | `ReferenceError: Document is not defined` |
| `RGlobal` | Global application state | ❌ Undefined | `ReferenceError: RGlobal is not defined` |

## What IS Available

Low-level entity classes exist but are useless without a document context:

```javascript
// These all work:
RAttributeEntity, RBlockReferenceEntity, RHatchEntity, RTextEntity,
RAddObjectsOperation, RModifyObjectsOperation, RVector, RLayer, etc.

// But there is no way to:
// - Open a DWG/DXF file
// - Import into a document
// - Export to DWG/DXF
// - Query entities by layer, type, or handle
```

## Verified via Introspection

Dumped all global objects from a running `qcad-bin -no-gui -platform offscreen -autostart introspect.js`:

```javascript
// introspect.js
var globals = [];
for (var key in this) {
    if (typeof this[key] === 'function' || typeof this[key] === 'object') {
        globals.push(key + " (" + typeof this[key] + ")");
    }
}
globals.sort();
for (var i = 0; i < globals.length; i++) {
    print(globals[i]);
}
```

Confirmed: **No `RApplication`, `RDocumentInterface`, `Document`, or `RGlobal` in the global namespace.**

## What Works (Confirmed)

Only the `args[]` global array is reliable for reading command-line arguments:

```javascript
// args[0] = qcad-bin path
// args[1] = -no-gui
// args[2] = -platform
// ...
// args[args.length - 2] = input file
// args[args.length - 1] = output file
var inputFile = args[args.length - 2];
var outputFile = args[args.length - 1];
```

## Attempted Script Patterns (All Failed)

| Script | Approach | Error |
|--------|----------|-------|
| `export_dxf3.js` | `qApp.arguments()` | TypeError: Cannot read property 'arguments' of undefined |
| `export_dxf4.js` | `RGlobal` | ReferenceError |
| `export_dxf5.js` | `RApplication` | ReferenceError |
| `export_dxf6.js` | `Document` | ReferenceError |

## Implication

**QCAD headless ECMAScript cannot be used for DWG↔DXF conversion in QCAD Pro 3.32.7 Qt6.** The `-no-gui -platform offscreen` launch mode initializes the scripting engine but not the high-level application APIs.

### Previous Documentation Was Wrong

The `references/qcad-pro-ecmascript-automation.md` reference documented `RDocumentInterface.importFile()` and `RDocumentInterface.exportFile()` as working. This was based on earlier QCAD Pro 3.28/Qt5 builds. **In QCAD Pro 3.32.7/Qt6, these APIs are absent in headless mode.**

## Workaround Options

| Option | Feasibility | Notes |
|--------|-------------|-------|
| Use QCAD GUI with Geisterhand | ✅ Viable | Requires X11 session, xdotool works on Qt5 widgets but NOT Qt6 |
| Use ODA File Converter GUI | ✅ Viable | See `references/oda-file-converter-gui-automation.md`; Qt6 swallows xdotool too |
| Use LibreDWG `dwg2dxf`/`dxf2dwg` | ⚠️ Partial | Works for non-block geometry; corrupts BLOCK/ATTDEF/ATTRIB |
| Use ezdxf direct DWG edit | ✅ Best | Edit original DWG directly, no round-trip needed |
| Downgrade to QCAD Pro 3.28/Qt5 | ❌ Not viable | Not available, not tested |

## Bottom Line

For DWG↔DXF conversion on QCAD Pro 3.32.7 Qt6:
- **QCAD headless ECMAScript is NOT an option** — missing core APIs
- **ODA File Converter GUI is the only reliable path** — but requires human-in-the-loop for the Start button
- **LibreDWG is acceptable for inspection only** — never for production pipelines with block data
- **ezdxf direct DWG editing is the future** — when the drawing can be edited without DXF round-trip
