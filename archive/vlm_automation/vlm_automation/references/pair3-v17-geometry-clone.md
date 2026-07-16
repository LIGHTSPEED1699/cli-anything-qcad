# Pair 3 V17: Geometry Clone Pass

## Goal

Add wire geometry (LINE/ARC/LWPOLYLINE) to terminals T7/T8/T9 after a prior text-only clone (v16) produced only TEXT entities.

## Why This Was Needed

V16 script restricted cloning to `TEXT`/`MTEXT` only to avoid contamination issues from earlier versions. This correctly produced text labels at T7–T9 but **left wire geometry behind**. Cloning must produce both text AND geometry.

## Approach: Separate-Pass Geometry Clone

Instead of modifying the increasingly fragile v16 script, run a dedicated geometry-clone pass:

1. **Source**: `3_clean.dxf` (the original, pre-clone DXF — clean handles, no contaminated clones).
2. **Target**: `3_cloned_v16_fixed.dxf` (already has text clones, positive layer colors).
3. **Discovery**: For each source row T{4,5,6}, find LINE/ARC/LWPOLYLINE within ±0.15 of terminal_y, skipping duplicates already present at target row in v16.
4. **Clone**: Raw DXF text-block copy — new handle, dy offset = −0.75, owner reset to modelspace (330→1F), strip reactors (360) and XDICTIONARY.
5. **Insert**: Append before ENDSEC.
6. **Export**: QCAD headless with `-platform offscreen`.

## Results

- T4→T7: 9 new geometry entities (2 duplicates skipped)
- T5→T8: 5 new geometry entities (5 duplicates skipped)
- T6→T9: 2 new geometry entities (6 duplicates skipped)
- Total new clones: 12 genuine geometry entities

## Duplicate Detection

v16_fixed already contained some geometry clones (because earlier scripts like v8-v11 had cloned all entity types). Duplicate detection uses `(entity_type, round(y,3), round(x_avg,3))` as key. Any entity at the same position is skipped.

## Handle Allocation

Start from `max_handle(target) + 1` to avoid collisions. In this case: `0x9A81` → new handles from `0x9A82` onward.

## Known Issue: CA-1452 Cable Tag

The CA-1451→CA-1452 text replacement failed because the source CA-1451 entity had an empty `1` (text string) group code, so `replace("CA-1451", "CA-1452")` found nothing. This is a v16 bug, not a v17 regression. Fix: add post-clone explicit text replacement targeted at the correct handle.

## V18 Stray Cleanup & Cable Tag Fixes

After V17 geometry clone, three defects appeared:

### 1. Stray Open-End ARC and LINE at T7

**Symptom:** A disconnected ARC and LINE appeared at T7 (x≈19.6, y≈19.5), not connected to any terminal block.

**Root cause:** Source ARC `9884` at T3 (y≈20.5) was within the ±0.15 tolerance for T4 source discovery. When cloned with dy=−0.75 to T7 (y≈19.5), it became a stray disconnected arc. The T3→T7 mapping is wrong — T3 is not a source row.

**Fix:** Post-clone scan for ARCs with x > 18.0 at target row y-levels that don't connect to a known terminal block. Remove handles `9A84` (ARC), `9A85` (LINE), `9A89` (vertical LINE to y=10.00), `9A8A` (LWPOLYLINE).

### 2. Missing CA-1452 Cable Callout Group

**Symptom:** No CA-1452 cable tag at T7.

**Root cause:** The geometry-clone pass only cloned entities within ±0.15 of the terminal row. The CA-1451 cable callout group (WFEND INSERT, WECOIL INSERT, LWPOLYLINE, LINE, TEXT) sits at y≈20.375 in the T3 area — above the T4 row (y=20.125). It was never cloned because it wasn't near any source terminal row.

**Fix:** Explicitly clone the CA-1451 group from `3_clean.dxf` as a separate cable-clone operation. Apply `dx=−1.4` offset to avoid overlapping "RED & BLUE" at x≈20.19.

| Entity | Source Handle | New Handle | dx | dy |
|--------|--------------|------------|-----|-----|
| TEXT "CA-1452" | 95E5 | 9A8E | −1.4 | −0.75 |
| LINE | 95E6 | 9A8F | −1.4 | −0.75 |
| LWPOLYLINE | 95E7 | 9A90 | −1.4 | −0.75 |
| INSERT WFEND | 95E8 | 9A91 | −1.4 | −0.75 |
| INSERT WECOIL | 95E9 | 9A92 | −1.4 | −0.75 |

**Result:** CA-1452 at (19.48, 19.67), clearance to "RED & BLUE" (20.19, 19.55) = **0.71 units**.

### 3. Cable Tag Overlap with "RED & BLUE"

**Symptom:** If cloned without dx offset, CA-1452 would sit at x≈20.8, overlapping "RED & BLUE" at x≈20.2.

**Fix:** Apply `dx=−1.4` to the entire cable callout group during clone. Shift leftward while maintaining y association.

## V18 Final Verification

| Check | Result |
|-------|--------|
| Stray entities removed | 9A84, 9A85, 9A89, 9A8A absent |
| CA-1452 group present | 5 entities at y≈19.375 |
| Overlap clearance | 0.71 units to "RED & BLUE" |
| No ARCs at T7–T9 with x > 18 | Confirmed |
| DWG export | 76,561 bytes, QCAD headless OK |

## Updated Three-Pass Clone Pattern

1. **Text-only pass** (v16) — clone TEXT/MTEXT labels, handle cable tag renumbering, fix layer colors.
2. **Geometry pass** (v17) — clone LINE/ARC/LWPOLYLINE from clean source, skip duplicates.
3. **Stray cleanup + cable fix** (v18) — remove open-end paths, fix overlaps, add missing cable callout groups.

## Script Reference

See `scripts/clone_pair3_v17.py` for the V17 raw-DXF clone script (basis for V18 patching).
