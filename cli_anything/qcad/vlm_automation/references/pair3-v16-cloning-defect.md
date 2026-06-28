# Pair 3: V16 Clone Script — Pitfall Log

## Status (as of 2026-05-18 session)

- **Latest DXF**: `3_cloned_v16_fixed.dxf` (667,290 bytes, 230 entities)
- **Latest DWG**: `3_FINAL_v11.dwg` is **0 bytes** — invalid
- **No valid DWG exists for v16+** in the workspace

## Critical Pitfall: Text-Only Cloning

`clone_pair3_v16.py` clones **only TEXT and MTEXT entities** (`if ent in ('TEXT','MTEXT')`). It does NOT clone wire geometry (LINE, ARC, LWPOLYLINE, POLYLINE).

**Result**: T7/T8/T9 get the cloned labels (`(W)`, `(B)`, `(GND)`, `PLC22`, `CA-1452`) but do NOT get the actual connecting lines and arcs.

**Why this happened**: v11 cloned all entity types but had contamination/wrong-offset issues. v12-v16 progressively tightened constraints and ended up only cloning TEXT to avoid errors. The core requirement — cloning wire geometries — was broken in the process.

## What v16 Does Correctly

1. Removes target terminal instrument labels before cloning (avoids duplicate terminal numbers)
2. Duplicate-position detection (no overlapping text)
3. Overlap detection against original entities
4. Layer color fix (all positive)
5. Cable tag text updates: PLC21→PLC22, CA-1451→CA-1452
6. Drawing number suffix update: 02732→02733
7. dy offset = −0.750 per row (correct based on terminal spacing Δy=0.250)

## What v16 Does Wrong

1. Only clones TEXT/MTEXT — no wire geometries
2. No valid DWG export exists for v16+
3. VLM verification in v16 script checks for labels that may not exist in source: T6 source has `(GND)` and `(RED & BLUE)`; T5 has `(W)`; T4 has `(B)` — verification code expects T8=(W), T9=(GND)

## Original Terminal Geometry (from `3_clean.dxf`)

| Terminal | Y-Position | Lines | Arcs | Texts |
|----------|-----------|-------|------|-------|
| T4 | 20.125 | 3 | 5 | `(3)`, `(B)`, `EPAC G1 14 H`, `PLC21`, `TO DWG. B-SAR-280-02732` |
| T5 | 19.875 | 3 | 5 | `(4)`, `(W)`, `EPAC G1 14 N` |
| T6 | 19.625 | 1 | 2 | `(GND)`, `(RED & BLUE)`, `2C SPARE` |

Cloning T4→T7 needs: 3 lines + 5 arcs + text labels
Cloning T5→T8 needs: 3 lines + 5 arcs + text labels  
Cloning T6→T9 needs: 1 line + 2 arcs + text labels

## Next Steps (for future session)

Option A: Fix v16 to clone ALL entity types (TEXT + LINE + ARC + LWPOLYLINE + POLYLINE), not just TEXT. Remove the `if ent in ('TEXT','MTEXT')` guard.

Option B: Go back to v11/v12 (clones all types) and fix the contamination/dy issues there.

Option C: Write a new script from scratch that: (1) discovers all wire geometry per terminal row, (2) clones with correct dy, (3) deduplicates, (4) updates text, (5) fixes layers, (6) exports DWG via QCAD.

Recommended: Option A is the fastest — modify v16's `get_src()` to also collect LINE/ARC/LWPOLYLINE/POLYLINE entities within the same y-tolerance, and update the `clone_entity` call to accept all entity types.

## Verification Bug

The v16 `verify()` function checks:
- T7: `['(B)']` expected — T4 has `(B)`, correct
- T8: `['(W)']` expected — T5 has `(W)`, correct
- T9: `['(GND)']` expected — T6 has `(GND)`, correct

BUT: verification also checks for `PLC22`, `CA-1452`, `02733` at group level — these are T4→T7 text labels and should be present.

The key issue: verification passes on TEXT alone but the drawing is incomplete without wire geometries.
