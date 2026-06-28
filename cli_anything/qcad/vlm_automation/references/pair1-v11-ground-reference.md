# Pair 1 V11: Ground Reference Line Falsely Deleted by Cloud Polygon

## Session
2026-05-17. VLM-CAD Pair 1 cloud-deletion pipeline.

## Problem
After V10 passed 5 user-reported issues, the user noted:
> "two short lines on the right side of F174, which should be kept on the drawing. These two lines are representing F174 is wired to electrical ground as voltage reference."

## Root Cause
Handle `4B6E` is an L-shaped POLYLINE with vertices:
- `(9.1678, 8.3461)` → `(9.3883, 8.3461)` → `(9.3883, 8.1965)`

It was flagged for deletion because its first vertex `(9.1678, 8.3461)` falls inside the C1 cloud polygon (`x=9.21–13.63`, `y` upper-right). The point-in-polygon heuristic had no way to distinguish:
- A cloud/strikethrough annotation drawn over the right side of the sheet
- A legitimate ground-reference wiring symbol extending from F174 toward the page margin

## Detection
A systematic scan of all DELETED entities near F174 revealed only two candidates:
- `4B6E` — the L-shaped ground reference (color 7, layer 0, n=3 vertices)
- `4BB8` — a long horizontal strikethrough line (color 1, endpoints from 9.16→13.93) — **correctly deleted**

## Fix (V11)
Removed handle `4B6E` from the V10 deletion list (105 handles → 104 handles). Rebuilt DXF/DWG via the standard pipeline:
```
original DXF → text-based deletion by handle → layer color fix → QCAD Pro DWG export
```

## Verification
- Programmatic: `4B6E` confirmed present in `1_v11_deleted_fixed.dxf`
- Visual: Matplotlib DXF render showed V10 had empty black space right of F174; V11 shows the restored lime-green L-shaped line
- VLM confirmed the difference when shown side-by-side comparison PNG

## New Pitfall to Embed

**Ground reference lines and other wiring symbols** (L-shaped, horizontal short lines extending from instrument labels toward page margins) can be **spatially inside cloud polygons** but are NOT part of cloud content. They must be preserved.

**Mitigation for next round:**
1. After building a deletion list from cloud polygons, **sweep all deleted handles** to check if any are:
   - L-shaped POLYLINEs (n=3 vertices, right-then-down or right-then-up pattern)
   - Horizontal LINEs or LWPOLYLINEs extending from an F-label rectangle toward the margin
   - Color 7 (white/bylayer) on layer 0 — typical for wiring symbols
2. **Cross-check against nearby TEXT handles** — if a deleted entity is within ~1.5 units of an F-label (F171–F178), investigate before committing
3. **Add a "wiring symbol whitelist"** — known-good patterns that should survive cloud deletion regardless of spatial overlap

## File Size Trajectory
| Version | Deletions | DWG Size | Notes |
|---------|-----------|----------|-------|
| V9 | 112 | 61,409 bytes | C1/C3 mapped correctly |
| V10 | 105 | 46,785 bytes | Fixed HATCH, boundary lines, label text |
| V11 | 104 | 46,817 bytes | Restored 4B6E ground reference |
