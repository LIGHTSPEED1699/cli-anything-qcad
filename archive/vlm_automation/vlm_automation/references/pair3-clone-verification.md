# Pair 3 Clone Verification Pipeline

Date: 2026-05-15

## Problem

V3/V4 clone operations produced corrupted DWGs because the source `3.dxf` had accumulated 73 LibreDWG internal handles ≥0x9800. Re-cloning from this file compounded the problem, creating overlapping entities and missing T7-T9 wires.

## Root Cause

- `dwg2dxf` assigns internal handles up to 0x9A81 (39553) even for original DWGs
- Cloning from a previously-processed DXF risks handle collisions and position drift
- V4 was worst because it cloned from the most-corrupted base (192 spurious clones)

## Solution (V5)

1. **Always regenerate clean DXF from original DWG** before each clone iteration
2. **Use dynamic handle gap discovery** instead of fixed SAFE_BASE
3. **Run automated verification BEFORE DWG export** to catch errors early

## Scripts

### `scripts/verify_pair3.py`

Automated DXF verification for clone operations. Checks:
1. No handle collisions (all handles unique)
2. T7 zone (y=18.8-19.3): TEXT ≥2, LINE ≥1, ARC ≥1
3. T8 zone (y=18.3-18.8): TEXT ≥2, LINE ≥1, ARC ≥1
4. T9 zone (y=17.8-18.3): TEXT ≥2, LINE ≥1, ARC ≥1
5. T4/T5/T6 original zones still have their original elements
6. Cable tag CA-1452 exists
7. Drawing number ends in -02
8. TO DWG clone (02733) exists
9. No negative layer colors

Returns exit code 0 on pass, 1 on fail with detailed issue list.

Usage:
```bash
python3 verify_pair3.py 3_cloned_v5_fixed.dxf
```

### V5 Clone Script Pattern

```python
# 1. Regenerate clean DXF
/media/sdddata1/libredwg/bin/dwg2dxf 3.dwg
# 2. Find handle gap
# 3. Clone T4/T5/T6 → T7/T8/T9 with dy=-1.25
# 4. Run verify_pair3.py
# 5. Fix layer colors
# 6. QCAD export to DWG
```

## Verified Results (V5)

| Zone | Entities | TEXT | LINE | ARC | INSERT | Status |
|------|----------|------|------|-----|--------|--------|
| T7 (clones) | 22 | 5 | 7 | 4 | 6 | ✅ |
| T8 (clones) | 26 | 9 | 6 | 6 | 5 | ✅ |
| T9 (clones) | 16 | 7 | 3 | 2 | 4 | ✅ |
| T4 (original) | 26 | - | - | - | - | ✅ |
| T5 (original) | 16 | - | - | - | - | ✅ |
| T6 (original) | 9 | - | - | - | - | ✅ |

Cable tag: CA-1452 at (20.875, 19.172)
Drawing number: 022-122-97024-00002-02
TO DWG clone: B-SAR-280-02733

## Pitfalls

108. **Always clone from original DWG-derived DXF** — Never re-clone from a previously-cloned DXF.
109. **Matplotlib rendering is unsuitable for VLM verification** — Use QCAD SVG export or entity-count verification instead.
110. **Verify per-zone entity types, not just totals** — Missing T7-T9 clones could be hidden by overlapping T4-T6 entities.
111. **Dynamic handle gap discovery** — Scan actual handle gaps, don't assume fixed base.
