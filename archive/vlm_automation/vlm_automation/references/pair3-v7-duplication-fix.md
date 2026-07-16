# Pair 3 V6→V7 Lessons — Duplicate Terminal Label Fix

## Root Cause

The original `3_clean.dxf` already contained terminal labels `(7)`, `(8)`, `(9)` and instrument labels (`EPAC G1 16 H`, etc.) at the target positions. The V6 clone script indiscriminately cloned **all** T4-T6 labels including terminal numbers and instrument tags, producing exact duplicates.

**V6 output**: Each target label appeared **twice** at the exact same coordinates, because both the original labels from `3_clean.dxf` and the clones from T4-T6 were present.

## Correct Approach: Wire-Only Cloning

When cloning wire runs from one terminal block to an **already-existing** target terminal block, clone **only** the following categories:

| Clone? | Category | Examples |
|--------|----------|----------|
| ✓ Yes | Wire geometry (LINE, ARC, LWPOLYLINE) | Connection lines, bends, cables |
| ✓ Yes | Connection metadata (TEXT) | `(W)`, `(GND)`, `2C SPARE`, `RED & BLUE` |
| ✓ Yes | PLC/cable tags (TEXT) | `PLC21 (FUTURE)`, `CA-1451` |
| ✓ Yes | Wire end INSERTs (no ATTRIBs) | WLGND, WFEND, WECOIL |
| ✗ No | Terminal number TEXTs | `(4)`, `(5)`, `(6)` → becomes duplicate `(7)` etc. |
| ✗ No | Instrument label TEXTs | `EPAC G1 14 N` → becomes duplicate `EPAC G1 17 N` |
| ✗ No | Terminal block INSERTs | Wlterm1, Wlltermn (already at target) |

## V6 Handle List (Wrong — 40 handles)

Included terminal labels 9639 `(4)`, 964D `(5)`, 9644 `(6)`, and instrument labels 9852, 9643, 9853, 9638.

## V7 Handle List (Correct — 31 handles)

Excluded all terminal labels and instrument tags. Cloned only wire geometry + connection metadata + PLC/cable tags.

| Group | Count | Offsets | Cloned Handles (hex) |
|-------|-------|---------|----------------------|
| T4→T7 | 23 | dy=-1.250 | 5458–546E |
| T5→T8 | 5  | dy=-1.000 | 546F–5473 |
| T6→T9 | 3  | dy=-1.250 | 5474–5476 |

## Key Pitfall

**Pitfall #88: Cloning terminal labels onto existing terminals**
Always check whether target terminal positions already have labels before cloning. When target terminal blocks already exist with their own labels, exclude terminal number TEXTs and instrument labels from the clone source. Clone only wire path geometry and connection metadata.

## Verification Checklist

After cloning, verify:
1. Terminal numbers appear exactly once per position: `(7)`, `(8)`, `(9)`
2. Instrument labels appear exactly once per terminal
3. Wire connections (LINEs, ARCs) exist at target positions
4. PLC/cable metadata properly renamed (PLC21→PLC22, CA-1451→CA-1452)
