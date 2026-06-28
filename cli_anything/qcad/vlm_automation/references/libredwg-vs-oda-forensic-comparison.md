# LibreDWG vs ODA/QCAD DWG Writer — Forensic Comparison

Session: 2026-05-08  
Purpose: Explain why LibreDWG `dxf2dwg` produces DWGs that AutoCAD TrueView treats as damaged, and why QCAD/LibreCAD "Save As DWG R32 [2018] (OpenDesign)" produces clean DWGs.

## Files Compared

| File | Size | Writer | TrueView Result |
|------|------|--------|-----------------|
| `1.dwg` (original) | 226 KB | AutoCAD 2018 | ✅ Opens cleanly |
| `1_MODIFIED.dwg` | 213 KB | LibreDWG `dxf2dwg` | ❌ Recover triggered (34 errors, 36 objects removed) |
| `1_UNMODIFIED_roundtrip.dwg` | 213 KB | LibreDWG `dwg2dxf` → `dxf2dwg` (zero edits) | ❌ Recover triggered (34 errors, 36 objects removed) |
| `1_MODIFIED_2018.dwg` | **67 KB** | LibreCAD GUI → Save As → DWG R32 [2018] (OpenDesign) | ✅ **Opens cleanly** |

**Critical finding:** The unmodified round-trip (no edits at all) produces the **exact same** error count as the edited file. This definitively proves the corruption is in LibreDWG's DWG **writer**, not our edits.

## LibreDWG Reader Errors (on clean ODA DWG)

LibreDWG `dwgread` on the ODA-produced DWG reports different errors:

```
ERROR: Invalid ATTRIB.keep_duplicate_records 64
ERROR: Invalid ATTRIB.keep_duplicate_records 66
ERROR: Invalid ATTRIB.keep_duplicate_records 144
Warning: Ignore invalid handleoff (@541)
Warning: Skip HATCH common handles due to short handle stream
Warning: Unstable Class object 502 MATERIAL (0x481) 571/0
Warning: Unstable Class object 505 MLEADERSTYLE (0xfff) 595/529F
```

These are **LibreDWG reader limitations**, not file corruption. ODA uses newer DWG object flags that LibreDWG v0.13.4 doesn't understand, but **AutoCAD TrueView 2020 handles them perfectly**.

## LibreDWG Writer Errors (on corrupt DWG)

LibreDWG `dwgread` on its own `dxf2dwg` output shows:

```
ERROR: Invalid boundary_handles size 1. Need min. 8 bits, have 3 for HATCH. Set _size to 0
ERROR: Duplicate handle 4837 for object 2386 already points to object 2227
Warning: Object handle not found 21484/0x53EC
Warning: Object handle not found 18379/0x47CB in 2573 objects of max 0x53EC handles
Warning: Skip HATCH common handles due to short handle stream
```

**Root causes:**
1. **HATCH boundary handle bitstreams** — size mismatch (8 bits required, 1–6 bits written)
2. **Duplicate handle references** — same handle points to two different objects
3. **Lost object handles** — handles referenced in the object table do not exist

## Size Red Flag

QCAD/ODA re-save produces a file **3× smaller** (67 KB vs 213 KB). This happens because ODA rewrites the entire DWG binary object model from scratch, discarding:
- Corrupted handle streams
- Unstable class objects (TABLESTYLE, MATERIAL, MLEADERSTYLE)
- Redundant object references

When you see `dxf2dwg` producing a DWG roughly the same size as the source, but an ODA/QCAD re-save produces a much smaller one (~25–30% of original), the re-save is doing a true object-model rewrite. This is good — it means corruption was stripped.

## Control Test Procedure

Always run this before blaming your edits:

```bash
# Zero-edit round-trip (control)
/media/sdddata1/libredwg/bin/dwg2dxf -o CONTROL.dxf original.dwg
/media/sdddata1/libredwg/bin/dxf2dwg -o CONTROL.dwg CONTROL.dxf
```

Open `CONTROL.dwg` in TrueView/AutoCAD. If it also triggers Recover, LibreDWG's writer is incompatible with your file format. No script fix will help.

## Corrected Pipeline

```
1. Original DWG
     ↓ dwg2dxf (LibreDWG)
2. DXF
     ↓ Python script edits (raw-byte handle replacement)
3. Edited DXF
     ↓ dxf2dwg (LibreDWG) ← INTERMEDIATE ONLY, expect corruption
4. Intermediate DWG (may trigger AutoCAD Recover)
     ↓ LibreCAD GUI → File → Save As → DWG R32 [2018] (OpenDesign)
5. ✅ Final clean DWG (opens in TrueView/AutoCAD without Recover)
```

## Alternative Paths for Headless Systems

| Path | Tool | Headless? | Notes |
|------|------|-----------|-------|
| A | AutoCAD (full) + Save As | ❌ No | Open the clean DXF directly. AutoCAD's native DXF parser handles the file correctly. |
| B | ODA File Converter | ⚠️ Needs xvfb | Extract AppImage (`--appimage-extract`), run binary under `xvfb-run`. Qt6 GUI. |
| **C** | **QCAD Pro + ECMAScript** | **✅ Yes** | **Validated 2026-05-09.** `qcad-bin -no-gui -platform offscreen -autostart script.js input.dxf output.dwg`. Requires Pro license (~$42). See full invocation below. |
| D | LibreCAD GUI Save-As | ❌ No (GUI) | **Validated 2026-05-08.** Open intermediate DWG → Save As → DWG R32 [2018]. |
| E | Accept DXF deliverable | ✅ Yes | Many downstream tools (BIM, GIS, QCAD) consume DXF directly. |
| F | Accept PDF deliverable | ✅ Yes | ezdxf+matplotlib render for review/archive. |

### Path C — QCAD Pro Headless (Fully Validated 2026-05-09)

**Prerequisites:** QCAD Pro 3.32.7 Qt6, valid Pro license, `$HOME/opt/qcad-3.32.7-pro-linux-qt6-x86_64/`

**Full invocation (must use `qcad-bin`, NOT the `qcad` wrapper):**
```bash
QCADDIR="$HOME/opt/qcad-3.32.7-pro-linux-qt6-x86_64"
export QT_QPA_PLATFORM=offscreen
export LD_LIBRARY_PATH="$QCADDIR:$QCADDIR/plugins"

"$QCADDIR/qcad-bin" \
  -no-gui \
  -platform offscreen \
  -allow-multiple-instances \
  -autostart templates/qcad_ecmascript/qcad_dxf2dwg.js \
  input.dxf \
  output.dwg
```

**Results validated on 13-layer production DWG (Pair 1):**
- QCAD Pro ODA export: **0 errors, 0 objects erased** in AutoCAD TrueView 2020
- LibreDWG `dxf2dwg` on same DXF: **90 errors, 56 objects erased** (Recover dialog)
- Both have identical entity counts (317 entities, 13 layers)
- File sizes: QCAD output ~73 KB, LibreDWG output ~68 KB — both reasonable

**Layer visibility prerequisite:** If the source DXF has all layers with negative color indices (`62 = -7`, `-1`, etc.), the QCAD-exported DWG will still open blank. Fix with `scripts/fix_layer_visibility.py` **before** conversion. See `references/layer-freeze-bug.md` § "Root Cause B".

## DXF Version Marker

LibreDWG DXFs always start with:
```
999
LibreDWG 0.13.4.8160
  0
SECTION
  2
HEADER
  9
$ACADVER
  1
AC1015
```

`AC1015` = AutoCAD R2000 format. Even when reading a 2018 DWG, LibreDWG writes DXF in R2000 format, losing newer object classes.

## Key Files

- `scripts/verify_dxf_handle_integrity.py` — Standalone script to verify handle replacement correctness in a DXF
- `references/dxf-handle-replacement-pitfalls.md` — Handle-matching bugs and fixes
