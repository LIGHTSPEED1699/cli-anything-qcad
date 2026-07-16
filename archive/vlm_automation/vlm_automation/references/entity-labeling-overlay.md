# Entity-Labeled Overlay Screenshots for Coordinate Mapping Verification

**Date:** 2026-05-11

## Why Entity Labels Are Essential

When multiple coordinate transforms are plausible (e.g., `annot.vertices/72` vs `swap_xy` vs PyMuPDF-flipped), a simple overlay showing cloud polygons and entity dots is **insufficient**. The user cannot correlate abstract dots with actual drawing elements.

The solution: label **every entity inside each cloud's bounding box** with its `HANDLE (TYPE)` so the user can cross-reference against the original CAD drawing.

## Rendering Specification

### Entity Plotting
- LINE: black dot, size 8
- DIMENSION: cyan dot, size 6
- MTEXT: red dot, size 10
- INSERT (block): green square, size 10

### Cloud Polygons
- Red outline, linewidth 2
- Cloud name (C0, C1, C2, C3) at centroid, bold red 16pt, white bg

### Entity Labels
- Text: `{HANDLE}\n({TYPE})` e.g. "3242\n(TEXT)"
- Blue text, yellow rounded box (alpha 0.7), 7pt font
- Thin blue arrow from label to entity position

### Figure Settings
- Size: (14, 10) inches
- DPI: 120 (produces ~400-500 KB; Discord drops files >~1 MB)
- Aspect: equal
- Grid: alpha-0.3

## Mapping Comparison Workflow

1. Extract raw vertices from each PDF PolygonCloud annotation
2. Try 3 candidate transforms:
   - `no_swap`: `(raw_x/72, raw_y/72)`
   - `swap_xy`: `(raw_y/72, raw_x/72)`
   - `pymupdf_flip`: `((page_h - raw_y)/72, raw_x/72)`
3. Generate 3 overlay PNGs (matplotlib + ezdxf)
4. Compress to <500 KB
5. Send to user via Discord; if dropped, copy to Google Drive
6. Ask: "Which overlay shows clouds on the entities you intended to mark?"
7. Use confirmed mapping for all subsequent matching

## Related
- `references/pdf-cloud-vertex-extraction.md` — correct `swap_xy` mapping
- Pitfall 68 (SKILL.md) — `swap_xy` is the correct universal transform
- Pitfall 73 (SKILL.md) — entity-labeled overlays are essential
