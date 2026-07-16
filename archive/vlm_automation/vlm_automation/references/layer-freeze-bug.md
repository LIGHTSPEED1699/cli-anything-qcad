# Root Cause D — QCAD ODA Export Caches Original Import-Time Layer State

**Discovered 2026-05-10.** Even when the source DXF has all-positive layer colors (`62 > 0`) and all layers are thawed (`flags = 0`), QCAD Pro headless ODA export can produce a DWG that opens with all layers hidden. This is a distinct root cause from negative colors (B) and frozen flags (A).

## Symptom

1. Source DXF passes `ezdxf` inspection: all `layer.dxf.color > 0`, all `layer.dxf.flags == 0`
2. `fix_layer_visibility.py` reports "All layers already positive — no fix needed"
3. Export via `qcad_dxf2dwg.js` (plain, no layer manipulation)
4. Open resulting DWG in QCAD or AutoCAD TrueView
5. **All layers hidden** — only grid and axes visible
6. Click "Show All Layers" → all content appears immediately
7. Save the now-visible DWG → re-opens correctly

## Diagnosis

Run `qcad_layer_diagnostic.js` on the exported DWG:
```
Layer '0'          color=7       ON/OFF=Off      THAWED
Layer 'Defpoints'  color=7       ON/OFF=Off      THAWED
...
```

Despite positive colors, `ON/OFF=Off`. The ODA export writer baked OFF state into the DWG.

## Root Cause

QCAD's ODA DWG export writer appears to derive layer ON/OFF state from the **original import-time layer state**, not from the final in-memory `RLayer` state. When QCAD imports a DXF, it creates internal ODA layer state records. Even if you later modify the `RLayer` object (e.g., `layer.setColor(7)`), the ODA export may use the cached import-time state.

This is similar to how DWG `ACAD_LAYERSTATES` dictionaries preserve layer state (pitfall 55), but occurs even when no such dictionary exists in the binary.

## Fix

Use `qcad_dxf2dwg_force_visible.js` instead of `qcad_dxf2dwg.js`:

```javascript
// After importFile(dxfPath)
var layers = document.queryAllLayers();
for (var i = 0; i < layers.length; i++) {
    var layer = layers[i];
    layer.setFrozen(false);
    layer.setOff(false);
    var c = layer.getColor();
    if (c < 0) layer.setColor(Math.abs(c));
    op.addObject(layer, false);
}
di.applyOperation(op);
// Then exportFile(dwgPath)
```

This explicitly sets every layer to visible before export, ensuring the ODA writer receives the correct state.

## Verification

| Script | Source DXF colors | Result DWG in QCAD | Result DWG in TrueView |
|--------|-------------------|-------------------|------------------------|
| `qcad_dxf2dwg.js` | All positive | Hidden (all OFF) | Hidden |
| `qcad_dxf2dwg_force_visible.js` | All positive | Visible ✅ | Visible ✅ |
| `qcad_dxf2dwg_force_visible.js` | Some negative | Visible ✅ | Visible ✅ |

## Prevention Rule

**`qcad_dxf2dwg_force_visible.js` is the default and required export script.** The plain `qcad_dxf2dwg.js` should only be used when:
1. The source DXF was created by ezdxf (not LibreDWG `dwg2dxf`)
2. You have independently verified the output DWG opens with all layers visible
3. You are in a debug/diagnostic context where you want to test raw export behavior

## Related

- Pitfall 45 (Layer Freeze Bug) — `flags=1` corruption in QCAD/LibreDWG roundtrip
- Pitfall 49 (Negative Color OFF) — `62 < 0` hides layers
- Pitfall 54 (QCAD native DWG hidden layers) — same issue, different phrasing
- Pitfall 55 (DWG internal state OFF) — `color > 0` but `isOff()=true`
- Pitfall 68 (QCAD export caches import state) — this document

## 2026-05-11 Confirmation: Layer Visibility Persists Even After Full Pipeline

After applying the complete validated pipeline to Pair 1:
1. Text-based handle deletion from original `1.dxf` (46 entities removed)
2. `fix_layer_visibility.py` (all 13 layers positive)
3. QCAD Pro ODA export via `qcad_dxf2dwg.js` (plain)
4. Verified output `1_FINAL.dwg` (71 KB, AC1032, 172 entities)

**Result:** `1_FINAL.dwg` still opens in QCAD with **all layers hidden**. Clicking "Show All Layers" immediately reveals all content. This confirms Root Cause D is **not fully resolved** by pre-fixing the DXF alone.

**Implication:** The plain `qcad_dxf2dwg.js` is insufficient for production. `qcad_dxf2dwg_force_visible.js` (which manipulates `RLayer` state after import and before export) must be used as the default. If even `force_visible.js` fails, the issue is deeper in the ODA writer's layer state serialization and requires post-export GUI fix (open in QCAD → Show All Layers → Save).

## Last Updated

2026-05-11 — confirmed `1_FINAL.dwg` (full pipeline, positive colors, text-deletion) still opens with hidden layers in QCAD. Pre-fixing DXF colors alone is insufficient; explicit `setOff(false)` in ECMAScript is required.
