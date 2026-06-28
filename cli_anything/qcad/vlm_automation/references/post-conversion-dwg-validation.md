# Post-Conversion DWG Validation Checklist

Session: 2026-05-08  
Context: After producing a DWG from any converter (QCAD, LibreDWG, ODA), verify structural integrity before delivery.

## Purpose

DWG is a closed binary format. Silent corruption (entity drops, handle reassignment, layer corruption, coordinate scaling) is common after format conversion. This checklist catches issues before the file reaches the end user.

## Validation Pipeline

### Step 1: Round-Trip → DXF

Use LibreDWG `dwg2dxf` to convert the DWG back to DXF for inspection:

```bash
dwg2dxf output.dwg intermediate.dxf
```

`dwg2dxf` is more tolerant of binary quirks than `ezdxf`; it can read DWGs that ezdxf cannot.

### Step 2: `ezdxf` Inspection

```python
import ezdxf

doc = ezdxf.readfile("intermediate.dxf")

# 2a — Entity type counts
source_counts = {"TEXT": 84, "LINE": 20, "CIRCLE": 9, "ELLIPSE": 4, "HATCH": 9, "INSERT": 8, "LWPOLYLINE": 4, "POLYLINE": 80}
types = {}
for e in doc.modelspace():
    types[e.dxftype()] = types.get(e.dxftype(), 0) + 1

for t, c in source_counts.items():
    actual = types.get(t, 0)
    # POLYLINE→LWPOLYLINE consolidation is normal
    if t == "POLYLINE" and actual == 0 and types.get("LWPOLYLINE", 0) == c:
        print(f"  {t}: {c} → LWPOLYLINE (normal consolidation)")
    elif actual != c:
        print(f"  ⚠ {t}: expected {c}, got {actual}")
    else:
        print(f"  ✓ {t}: {c}")

# 2b — Layer assignments
by_layer = {}
for e in doc.modelspace():
    by_layer[e.dxf.layer] = by_layer.get(e.dxf.layer, 0) + 1
print(f"Entities by layer: {by_layer}")

# 2c — Color distribution
colors = {}
for e in doc.modelspace():
    colors[e.dxf.color] = colors.get(e.dxf.color, 0) + 1
print(f"Colors: {colors}")

# 2d — Viewport settings (catches zoom-to-blank-area bug)
header = doc.header
print(f"EXTMIN: {header.get('$EXTMIN')}")
print(f"EXTMAX: {header.get('$EXTMAX')}")
for v in doc.viewports:
    print(f"VPORT center={v.dxf.center} height={v.dxf.height}")
```

### Step 3: Comparison Against Source DXF

| Check | Must Match | Notes |
|-------|------------|-------|
| Total entity count | ✓ ±0 | POLYLINE→LWPOLYLINE consolidation is OK |
| TEXT count | ✓ ±0 | Cleared text should still exist as entities |
| Layer names | ✓ exact | New or missing layers = corruption |
| Layer entity counts | ✓ ±0 per layer | Redistribution indicates handle bug |
| Color distribution | ✓ exact | Color changes indicate entity corruption |
| `$EXTMIN` / `$EXTMAX` | ✓ ~0.01 tolerance | Shift indicates coordinate scaling bug |
| VPORT `center` | ✓ exact | Change causes "empty display" in viewers |
| VPORT `height` | ✓ exact | Change causes "empty display" in viewers |

### Step 4: Text Content Verification (for cleared targets)

```python
# For cleared targets: verify group-code-1 values
for h in target_handles:
    entity = doc.query(f"TEXT[handle=='{h}']")[0]
    val = entity.dxf.text
    if val == '.':
        print(f"  ✓ {h}: cleared to '.'")
    elif val == '':
        print(f"  ⚠ {h}: empty (TrueView may reject)")
    else:
        print(f"  ? {h}: '{val}' (unexpected)")
```

## False Positive Handling

PDF annotation bbox matching may select non-TEXT entities (CIRCLE, ELLIPSE) as "targets." These have no group-code-1 field, so text clearing naturally skips them. Do NOT count them as failures.

Expected pattern from Pair 1 (2026-05-08):
- 73 total matched targets
- 63 TEXT → successfully cleared to `.`
- 10 CIRCLE/ELLIPSE → skipped (no text field)
- Result: 63/63 TEXT cleared, 0 false failures

## Known Limitations

### LibreDWG `dwg2dxf` Cannot Validate Layer Flags

LibreDWG's `dwg2dxf` DXF writer **also** corrupts the LAYER table during roundtrip, changing `flags` from `0` (unfrozen) to `1` (frozen). This happens regardless of whether the input DWG was produced by QCAD, ODA, or LibreDWG itself.

**Impact:** If you fix layer flags in your source DXF, run it through QCAD export to produce a DWG with `flags=0`, then roundtrip that DWG back through `dwg2dxf` for validation, the resulting DXF will show `flags=1` for all layers. This does **NOT** mean the QCAD DWG is broken — it means LibreDWG's validation path is untrustworthy for layer state.

**What you can still validate with `dwg2dxf` roundtrip:**
- ✅ Entity counts by type
- ✅ Entity colors
- ✅ Layer names (existence, not state)
- ✅ Geometry coordinates
- ✅ `$EXTMIN` / `$EXTMAX`
- ✅ VPORT settings

**What you CANNOT validate with `dwg2dxf` roundtrip:**
- ❌ Layer frozen/thaw state (`flags`)
- ❌ Layer lock state
- ❌ Layer plot state

**How to validate layer state correctly:**
1. Check the **source DXF** (before any conversion) with `ezdxf`: `doc.layers[0].dxf.flags == 0`
2. Open the final DWG directly in **AutoCAD TrueView** or **QCAD** and verify content is immediately visible
3. If using QCAD headless, use the ECMAScript layer-thaw template (`templates/qcad_ecmascript/convert_dxf2dwg_thaw.js`) which explicitly sets `layer.setFrozen(false)` before export

### LibreDWG `dxf2dwg` Produces AutoCAD-Incompatible DWGs

LibreDWG `dxf2dwg` can write a DWG that AutoCAD/TrueView treats as damaged even when the DXF is structurally valid. Symptoms:
- TrueView opens a **Recover** dialog: "90 errors found, 56 objects erased" (exact counts vary)
- After recovery completes, the drawing displays correctly (all layers visible, geometry intact)
- File size stays roughly the same as the input DXF (no object-model rewrite)
- Zero-edit round-trips (dwg2dxf → dxf2dwg, no changes) produce the same error/object counts

**This is a LibreDWG writer limitation, not an edit bug.** The same DXF, when converted by QCAD Pro's ODA engine, produces a DWG that opens in TrueView without Recover errors.

**Validation:** Compare two DWGs from the same fixed DXF:
1. `libredwg_dxf2dwg output.dwg` → TrueView shows Recover dialog (error count > 0)
2. `qcad_pro_oda output.dwg` → TrueView opens directly (error count = 0)

Both files have identical entity counts and geometry. The difference is only in DWG binary format conformance.

**Recommended:** Use LibreDWG only for `dwg2dxf` (reading), not for `dxf2dwg` (writing). Use QCAD Pro ODA or ODA File Converter for DWG production.

## Common Corruption Signatures

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Entity count dropped | `dxf2dwg` deleted invalid objects | Use QCAD/ODA instead of LibreDWG for DWG generation |
| TEXT → ATTRIB or CIRCLE | Handle reassignment bug | Strict group-code-5 matching; don't use `dxf2dwg` |
| Layer names changed | OBJECTS dictionary corruption | Round-trip through ODA/QCAD re-writer |
| `$EXTMIN`/`$EXTMAX` shifted | Coordinate scaling during save | Check export format string (R32 vs R2018) |
| "Empty" in TrueView | VPORT center/height mismatch | `ZOOM` → `EXTENTS`; or re-export with correct viewport |
| **"Blank" in TrueView/QCAD (no error)** | **Layer freeze bug** (flags 0→1) | See `references/layer-freeze-bug.md` § "Fix Options" |

## Files

- `scripts/verify_dxf_handle_integrity.py` — Post-edit handle integrity check
- `scripts/safe_dxf_text_clear_v2.py` — Production DXF clearing with strict matching
- `references/layer-freeze-bug.md` — Layer freeze bug: symptoms, fixes, validation traps