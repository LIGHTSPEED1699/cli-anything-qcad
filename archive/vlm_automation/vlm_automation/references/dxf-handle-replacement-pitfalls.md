# DXF Handle-Based Text Replacement — Pitfall Reference

Session: 2026-05-08  
Problem: DWG passes `dxf2dwg` but AutoCAD TrueView 2020 runs Recover and removes 36 objects.

## Symptom

- `dxf2dwg` exit=1 but writes DWG (normal for LibreDWG with warnings)
- AutoCAD TrueView opens DWG and immediately triggers Recover
- Recover report: "34 errors found, 36 objects removed"
- All entities to be cleared were TEXT objects with group-code-1 set to `.` (single dot)

## Root Cause (Two-Stage Failure)

### Stage 1: Coordinate substring match (naive search)

Handles are short hex strings like `3240`, `325B`. These appear as **substrings** inside coordinate values:

```
 10
13.838783**2407**8082
 20
8.40408351392842
```

A naive search `raw_bytes.find(handle.encode() + b'\r\n')` matches the `2407` substring inside the coordinate. The script then finds the next `\r\n  1\r\n` **after that position**, which belongs to a **different entity**, replacing its text value with `.` and corrupting that entity.

**Fix:** Only match handles preceded by `b'\r\n  5\r\n'` (group-code-5 line):

```python
handle_pat = b'\r\n  5\r\n' + handle.encode() + b'\r\n'
```

### Stage 2: OBJECTS-section double match

Even with strict group-code-5 matching, handles appear in **two** places:
1. **ENTITIES section** — the actual entity definition (first match, correct)
2. **OBJECTS section** — as a soft-pointer xref in a dictionary or reactor list

`raw_bytes.find()` returns the **first** match, which is in ENTITIES — safe. But if code accidentally uses `.replace()` or a `while True:` loop, the second match (in OBJECTS) is also modified. The next `\r\n  1\r\n` after an OBJECTS handle belongs to a dictionary entry (e.g., `AcLyLayerFilter`), replacing it with `.` and destroying the DWG cross-reference table.

**Fix:** Use `.find()` (first match only). Never use `.replace()` or global loops for handle-based replacement.

## Verification

To check whether a DXF has been corrupted by these bugs:

```python
with open("1_MODIFIED.dxf", 'rb') as f:
    raw = f.read()

for h in target_handles:
    pos = raw.find(b'\r\n  5\r\n' + h.encode() + b'\r\n')
    if pos > 0:
        gc1_pos = raw.find(b'\r\n  1\r\n', pos + len(b'\r\n  5\r\n' + h.encode() + b'\r\n'))
        val = raw[gc1_pos + len(b'\r\n  1\r\n') : raw.find(b'\r\n', gc1_pos + len(b'\r\n  1\r\n'))]
        print(f"  {h}: '{val.decode()}'")
```

## Control Test: Unmodified Round-Trip

If AutoCAD still reports errors after the fix, the problem may be LibreDWG itself, not the edits. Test with:

```bash
# Convert original DWG → DXF → DWG with ZERO edits
dwg2dxf -o 1_UNMODIFIED.dxf 1.dwg
dxf2dwg -o 1_UNMODIFIED.dwg 1_UNMODIFIED.dxf
```

Open `1_UNMODIFIED.dwg` in TrueView. If it **also** triggers Recover with the same error count, LibreDWG's DWG writer is fundamentally incompatible with AutoCAD for this file format. No script fix will help.

**Confirmation signs:**
- `dxf2dwg` exit=1 with warnings but writes DWG (normal LibreDWG behavior)
- `dwg2dxf` produces DXF with "Invalid boundary_handles size 1" HATCH warnings
- `dwg2dxf` produces DXF with "Object handle not found" warnings (~50+)
- TrueView Recover shows identical error/object counts on edited and unmodified round-trip DWGs

## Workarounds When LibreDWG DWG Writer Is Incompatible

| Path | Tool | Headless? | Notes |
|------|------|-----------|-------|
| **A** | AutoCAD (full) + Save As | ❌ No | Open the clean DXF directly in AutoCAD/LT. AutoCAD's native DXF parser handles the file correctly. Save as `.dwg`. This is the only guaranteed path. |
| **B** | ODA File Converter | ⚠️ GUI | Qt-based; needs `xvfb-run` or X11. You have the AppImage at `/media/sdddata1/libredwg/ODAFileConverter.AppImage`. Extract with `--appimage-extract` and run the binary under xvfb if headless. |
| **C** | QCAD / LibreCAD GUI + Save As | ❌ No (GUI) | **Validated 2026-05-08.** Open the LibreDWG-generated DWG in LibreCAD → File → Save As → DWG R32 [2018] (OpenDesign). Produces a **67 KB** clean DWG that opens in TrueView 2020 without Recover. Same engine as ODA File Converter but invoked through the GUI dialog. |
| **D** | TrueView only (no AutoCAD) | ❌ No | TrueView can **view** DXF but cannot save edits or re-export DWG. Use it for verification only. |
| **E** | Accept DXF deliverable | ✅ Yes | The DXF is clean. Many downstream tools (BIM, GIS, QCAD) consume DXF directly. |
| **F** | Accept PDF deliverable | ✅ Yes | Use ezdxf+matplotlib or LibreCAD to render the DXF to PDF for annotation verification. |

**Practical recommendation (validated):**  
1. Use LibreDWG for **DXF extraction and editing only** (T1 pipeline).  
2. Convert edited DXF → DWG with `dxf2dwg` (LibreDWG). This DWG is **intermediate only** — it will likely trigger AutoCAD Recover.  
3. Open the intermediate DWG in **LibreCAD GUI** → File → Save As → **DWG R32 [2018] (OpenDesign)**.  
4. Result: a **3× smaller** DWG (67 KB vs 213 KB) that opens cleanly in TrueView 2020.  
5. The ODA engine in LibreCAD's save dialog restructures the binary from scratch, discarding all LibreDWG handle corruption.

**File size red flag:** If `dxf2dwg` produces a DWG roughly the same size as the source, but the final ODA/QCAD re-save produces a much smaller one (~25–30% of original), the re-save is doing a true object-model rewrite. This is good — it means corruption was stripped.

## Replacement Value

| Value | AutoCAD TrueView | LibreDWG dxf2dwg | ezdxf |
|-------|------------------|------------------|-------|
| `''` (empty) | ❌ Rejects | ✅ Passes | ❌ Rejects |
| `' '` (space) | ✅ Passes | ✅ Passes | ✅ Passes |
| `'.'` | ✅ Passes | ✅ Passes | ✅ Passes |

Single dot `'.'` is the most robust choice — minimally visible in all viewers, valid for all parsers. Single space also works, but dot is preferred because some font engines render a space as a visible gap or placeholder. Note: if LibreDWG's DWG writer is incompatible with your file format, the choice between space and dot is irrelevant; the DWG will fail regardless.

**2026-05-08 correction:** The original recommendation was single space. Production testing confirmed dot is safer across viewer font engines.

## Extra CRLF Injection Bug

**Problem:** Replacing the text value with `b'.\r\n\r\n'` (dot + two CRLF pairs) corrupts the DXF sequence.

**Explanation:** The DXF format requires each group code and value pair to be separated by exactly one `\r\n` terminator. The file already contains `\r\n` after every value. If the replacement injects an additional `\r\n\r\n`, the next group code line ends up with a stray leading `\r\n`, orphaning the group code on its own line and shifting the value to the next line. This produces malformed DXF entries.

**Two-stage bug pattern:**
1. **First replacement** injects extra CRLF → next entity's group code is misaligned
2. **Second replacement** on a later handle now matches the wrong location because the file offsets have shifted
3. **Cascade effect:** every subsequent replacement corrupts a different entity
4. Result: 69 of 73 targets appear "cleared" in the file, but many are the wrong entities; `dxf2dwg` fails with "Failed to decode DXF file" (exit=1, no DWG written)

**Fix:** Replace with `b'.'` only (no trailing terminators). The original file's `\r\n` after the old value remains in place. The script finds `handle + \r\n`, then finds the next `\r\n  1\r\n`, and replaces the bytes between the end of `\r\n  1\r\n` and the next `\r\n` (exclusive) with `b'.'`. The original `\r\n` terminator is preserved.

**Verification:** After replacement, every cleared handle should have exactly one `\r\n  1\r\n` followed by `.` followed by `\r\n` (no double terminators):

```python
with open("1_MODIFIED.dxf", 'rb') as f:
    raw = f.read()

for h in target_handles:
    pos = raw.find(b'\r\n  5\r\n' + h.encode() + b'\r\n')
    gc1_pos = raw.find(b'\r\n  1\r\n', pos)
    end = raw.find(b'\r\n', gc1_pos + len(b'\r\n  1\r\n'))
    segment = raw[gc1_pos:end + 2]
    assert segment.count(b'\r\n') == 2, f"Double CRLF at {h}"
```

## QCAD Pro Headless DXF→DWG Conversion

For programmatic DWG generation from the cleaned DXF, QCAD Pro's ECMAScript interface can export DWG directly:

```javascript
// convert_dxf2dwg.js
var args = application.arguments;
var dxfPath = args[1];
var dwgPath = dxfPath.replace(/\.dxf$/, '_QCAD_FINAL.dwg');

document = new Document(dxfPath);
document.exportFile(dwgPath, "DWG R32 [2018] (OpenDesign)");
EAction.handleUserMessage("Saved to " + dwgPath);
qApp.exit(0);
```

Run:
```bash
export QCADDIR=/opt/qcad
export LD_LIBRARY_PATH="$QCADDIR:$QCADDIR/plugins"
$QCADDIR/qcad-bin -platform offscreen -no-gui -autostart convert_dxf2dwg.js -- 1_MODIFIED.dxf
```

This produces a clean DWG (ODA R32) that opens in TrueView 2020 without Recover.

## TrueView "Empty" Display — Viewport Zoom Issue

**Symptom:** TrueView opens the DWG without error dialogs, but only grid/axes are visible. User reports "no other contents displayed." Double-clicking the mouse wheel puts the axis in the bottom-left corner but content remains invisible.

**Root cause:** The file is structurally valid and complete. Content exists but is off-screen because TrueView's initial view is not at drawing extents.

**Diagnosis checklist (in order):**

| Check | Action | Expected Result |
|-------|--------|---------------|
| 1 | **Click the "Model" tab** at bottom of TrueView | Should show modelspace content directly |
| 2 | **Double-click mouse wheel** or `ZOOM` → `EXTENTS` | View should jump to show full drawing |
| 3 | **Check View tab** → **Navigation** → **Zoom Extents** icon | Same as mouse wheel zoom |
| 4 | **Verify `$EXTMIN` / `$EXTMAX`** via `ezdxf` | Should match source DXF (tolerance ±0.01) |
| 5 | **Check entity layer assignments** | All layers should be unfrozen (`frozen=False`) |

**Key TrueView limitation:** TrueView 2020 (free viewer) has **no persistent command line**. You cannot type `ZOOM` or `EXTENTS`. Use mouse wheel double-click or the View tab navigation panel instead.

**Pre-delivery verification via `ezdxf`:**

```python
import ezdxf
doc = ezdxf.readfile("1_MODIFIED.dxf")

# Check extents match source
header = doc.header
print("EXTMIN:", header.get("$EXTMIN"))
print("EXTMAX:", header.get("$EXTMAX"))

# Check VPORT (view center and height)
for v in doc.viewports:
    print(f"VPORT center={v.dxf.center} height={v.dxf.height}")

# Check all layers are unfrozen
for layer in doc.layers:
    flags = getattr(layer.dxf, 'flags', 0)
    print(f"Layer {layer.dxf.name}: frozen={bool(flags & 0x01)}")

# Check entity counts match source
by_type = {}
for e in doc.modelspace():
    by_type[e.dxftype()] = by_type.get(e.dxftype(), 0) + 1
print("Entities by type:", by_type)
```

**Validated 2026-05-08:** Pair 1 output has 218 modelspace entities, same view center (8.5, 5.5), same view height (11.08), same `$EXTMIN`/`$EXTMAX`, and all layers unfrozen. The file is correct — TrueView simply needs `ZOOM EXTENTS`.

**Note:** Some DWG converters (especially round-trip through LibreDWG) create a **paper space viewport in Layout1** that wasn't in the original. This viewport may clip the drawing if the user is on the Layout1 tab. Always instruct the user to click the **"Model"** tab first.

## Layer Freeze Bug — QCAD / LibreDWG Roundtrip Corrupts Layer Flags

**Symptom:** TrueView/QCAD opens the DWG without error, but shows a blank canvas (only grid/axes). User can "toggle layer visibility" in QCAD and content appears. In TrueView, content is simply invisible even after Zoom Extents.

**Root cause:** The QCAD / LibreDWG round-trip silently changes all `LAYER` table `flags` from `0` (unfrozen) to `1` (frozen). In DXF, bit `0x01` on a LAYER record is the frozen flag. AutoCAD/TrueView respects this and hides all content.

**Evidence (2026-05-08 session):**
```
Original DXF  LAYER flags:  0=0, E-SYMB=0, E-TEXT=0, ... (all 0)
QCAD export   LAYER flags:  0=1, E-SYMB=1, E-TEXT=1, ... (all 1 except Defpoints)
LibreDWG dwg2dxf roundtrip: 0=1, ... (also all 1)
```

**Fix — Post-process layer flags in the DXF before QCAD conversion:**

```python
with open("input.dxf", 'rb') as f:
    raw = f.read()

# Find LAYER table boundaries
layer_start = raw.find(b'TABLE\r\n  2\r\nLAYER\r\n')
layer_end   = raw.find(b'ENDTAB', layer_start)
layer_section = raw[layer_start:layer_end]

# Replace "\r\n 70\r\n     1\r\n" with "\r\n 70\r\n     0\r\n" inside LAYER table only
fixed_section = layer_section.replace(b'\r\n 70\r\n     1\r\n', b'\r\n 70\r\n     0\r\n')
fixed_raw = raw[:layer_start] + fixed_section + raw[layer_end:]
```

**Important:** The replacement value is **padded to width 5** (`"     1"` not `"1"`). A naive `replace(b' 70\r\n1\r\n', ...)` will miss it.

**Verification after fix:**
```python
import ezdxf
doc = ezdxf.readfile("fixed.dxf")
for layer in doc.layers:
    assert layer.dxf.flags == 0, f"Layer {layer.dxf.name} still frozen: {layer.dxf.flags}"
```

**2026-05-08 validated pipeline (complete):**
1. Edit DXF with `safe_dxf_text_clear_v2.py` (strict handle match, dot replacement, no extra CRLF)
2. **Post-process layer flags** to `0` (see code above)
3. Run QCAD headless ECMAScript (`convert_dxf2dwg.js`) to produce DWG R32
4. Verify in TrueView: open → Model tab → Zoom Extents → all content visible

## False Positive Filter

PDF annotation bbox matching can select non-TEXT entities (CIRCLE, ELLIPSE) as "targets to clear." These have no group-code-1 text field, so the replacement naturally skips them. Verify by checking entity types before processing:

```python
# After roundtrip DWG→DXF→ezdxf inspection
# 10 "failed" targets were actually CIRCLEs/ELLIPSEs
# 63/73 actual TEXT targets were successfully cleared
```

Always include entity-type filtering in the annotation-to-handle pipeline, or accept that some annotations will be false positives and verify post-clear.

## Files

- `scripts/safe_dxf_text_clear_v2.py` — production-ready implementation with all fixes
- `pair1_execute_v2.py` — integrated pipeline script (PDF annotation → DXF edit → DWG)
- `scripts/dxf_to_pdf.py` — DXF/DWG → PDF with automatic round-trip fallback