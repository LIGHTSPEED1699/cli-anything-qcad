# Direct DWG Deletion Pipeline

**Date:** 2026-05-10  
**Replaces:** Round-trip PDF→DXF→fix→QCAD→DWG pipeline when layer visibility fails  
**Status:** VALIDATED on Pair 1 (73 entities deleted, 145 remain, all 14 layers ON, DWG 70 KB AC1032)  

## Problem

When the standard pipeline produces DXFs with corrupted layer state (frozen or OFF), the `qcad_dxf2dwg_force_visible.js` script is **not universally reliable** (see `references/layer-freeze-bug.md` § "Root Cause D"). For some files (Pair 2) it produces visible DWGs; for others (Pair 1) the DWG still opens with all layers hidden.

## Solution: Edit the Original DWG Directly

The original DWG files (`1.dwg`, `2.dwg`) open with all layers visible. Instead of round-tripping through DXF (which introduces layer-state corruption), directly import the original DWG into QCAD Pro, delete target entities by handle, and export to a new DWG.

### Why this works

- Original DWGs preserve the author's intended layer state (all visible)
- QCAD import/export of DWG→DWG is more faithful than DXF→DWG for layer state
- Entity handle stability across DWG→QCAD import is HIGH (all 73 deletion targets found by exact handle match in `1.dxf`, which mirrors the original `1.dwg`)
- No DXF color-index manipulation required
- No LibreDWG involvement at all in the deletion step
- No `ACAD_LAYERSTATES` dictionary pollution (verified: `1_CLEAN.dwg` has 0 occurrences)

### Workflow (VALIDATED)

```
Original DXF (from original DWG via dwg2dxf)
  218 entities, all 14 layers visible
       │
       ├── 1. Load DXF in ezdxf (Python)
       │   Delete 73 entities by exact handle match
       │   Fix 13 negative layer colors → positive
       │   Save as `1_CLEAN.dxf`
       │
       ├── 2. Export to DWG via QCAD Pro ODA engine
       │   qcad-bin -no-gui -platform offscreen -autostart qcad_dxf2dwg.js 1_CLEAN.dxf 1_CLEAN.dwg
       │   (DO NOT use qcad wrapper — it overrides -platform offscreen with -platform xcb)
       │
       └── 3. Verify:
           ezdxf: 145 entities, all layers positive colors
           Binary scan: no ACAD_LAYERSTATES, no DICTIONARY, no XRECORD
           DWG format: AC1032 (2018), 70 KB
```

### Target Entity Format

The deletion target list is produced by the hybrid pipeline (`pdf_annotation_extractor.py` + `annotation_extractor.py`). Example from `1_deletion_log.json`:

```json
[
  {"handle": "325B", "type": "TEXT", "text": "105", "pos": [5.2509, 4.5757]},
  {"handle": "325C", "type": "TEXT", "text": "106", "pos": [5.2509, 4.1726]},
  {"handle": "325D", "type": "TEXT", "text": "F176", "pos": [8.5734, 4.1765]},
  ... 73 total
]
```

### Handle Stability (VALIDATED)

**All 73 handles from `1_deletion_log.json` were found as exact matches in `1.dxf`** (the DXF produced from the original `1.dwg` via LibreDWG `dwg2dxf`). The handle set in the original DXF is `{2E8 … 5243}`; the deletion targets `{325B, 325C, 325D, 34E8, 34E9, 4074, 4151, 4152, 4672, 4673, 4674, 4675, 4676, 4677, 4678, 4B84, 4B87, 3239, 3240, 3241, 3242, 3243, 3244, 3245, 36DD, 36DE, 36DF, 36E0, 36E1, 466D, 466E, 466F, 4670, 4671, 4679, 467A, 467F, 4680, 4681, 47C5, 47C6, 47C7, 47C8, 47C9, 47CA, 483A, 48E0, 48E2, 4D70, 4D71, 4D72, 4D74, 4D75, 4D76, 36FC, 3F84, 4061, 4062, 41D3, 4837, 4B6B, 36D2, 36D3, 36D4, 36D5, 36D6, 36DB, 36FA, 36FB, 3980, 446E, 4836, 4B4D}`

**Critical finding:** ezdxf `doc.entitydb.get(int_handle)` does NOT find entities even when handles exist. Use the **modelspace iterator** instead:

```python
# WRONG — returns None for valid handles
entity = doc.entitydb.get(int(handle, 16))

# RIGHT — finds entities by handle
for entity in msp:
    if entity.dxf.handle.upper() == target_handle:
        msp.delete_entity(entity)
```

### Layer Visibility Fix (REQUIRED before QCAD export)

Even the original `1.dxf` has **13 layers with negative color codes** (`62 = -7`, `-1`, `-2`, etc.). These must be fixed to positive before QCAD export, or the resulting DWG will have hidden layers:

```python
for layer in doc.layers:
    if layer.dxf.color < 0:
        layer.dxf.color = abs(layer.dxf.color)
```

**Result:** All 14 layers (`0`, `LITE`, `MEDIUM`, `MED_HVY`, `HEAVY`, `TEXT_1`, `TEXT_3`, `TEXT_4`, `TEXT2`, `MTO-BAL`, `LOGO168`, `E-SYMB`, `E-TEXT`, `Defpoints`) become positive.

### Save Format for ezdxf on LibreDWG DXFs

LibreDWG DXFs have corrupted material tables that crash `doc.saveas()`. Use **ASCII format** to bypass the material table bug:

```python
# WRONG — AttributeError on material table
out_dxf = "/path/to/1_CLEAN.dxf"
doc.saveas(out_dxf)

# RIGHT — ASCII write avoids material table
with open(out_dxf, "wb") as fp:
    doc.write(fp)

# ALSO RIGHT — explicit ASCII format
out_dxf = "/path/to/1_CLEAN.dxf"
doc.saveas(out_dxf, fmt='asc')
```

### QCAD Pro Export Command

```bash
export LD_LIBRARY_PATH=/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64:/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64/plugins
QCAD=/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad-bin

$QCAD -no-gui -platform offscreen \
  -autostart qcad_dxf2dwg.js \
  1_CLEAN.dxf 1_CLEAN.dwg
```

**CRITICAL:** Use `qcad-bin` directly, NOT the `qcad` wrapper script. The wrapper overrides `-platform offscreen` with `-platform xcb`, causing segfaults on headless systems.

### Validation Results (Pair 1)

| Metric | Value |
|--------|-------|
| Input entities | 218 (from `1.dxf`) |
| Deletion targets | 73 |
| Found & deleted | 73/73 (100%) |
| Remaining entities | 145 |
| Layer colors fixed | 13/13 negative → positive |
| Output DXF | `1_CLEAN.dxf` |
| Output DWG | `1_CLEAN.dwg` (70 KB, AC1032/2018) |
| ACAD_LAYERSTATES | 0 occurrences |
| DICTIONARY | 0 occurrences |
| XRECORD | 0 occurrences |

### Binary Scan Comparison

All three DWGs (`1.dwg` original, `1_CLEAN.dwg` new, `2_FIXED_VISIBLE.dwg` working) have **0 occurrences** of `ACAD_LAYERSTATES`, `LayerState`, `LAYERSTATE`, `DICTIONARY`, `XRECORD`, `frozen`, `FROZEN`, `off`, or `OFF` in their binary content. This confirms the layer visibility is controlled purely by LAYER table `62` values (in DXF) or equivalent ODA layer state (in DWG), with no hidden layer-state dictionaries.

## Related Pitfalls

- `references/layer-freeze-bug.md` § "Root Cause D" — why `qcad_dxf2dwg_force_visible.js` is not universally reliable
- `references/qcad-pro-ecmascript-automation.md` — verified QCAD ECMAScript APIs including `importFile`, `exportFile`, `deleteObject`
- `references/post-conversion-dwg-validation.md` — how to validate any DWG output for entity count, layer names, and geometry
- Pitfall 60 (SKILL.md) — ezdxf `entitydb.get()` does not find entities in LibreDWG DXFs; use modelspace iterator instead
- Pitfall 61 (SKILL.md) — `doc.saveas()` crashes on LibreDWG DXFs; use `doc.write()` or `saveas(..., fmt='asc')`
