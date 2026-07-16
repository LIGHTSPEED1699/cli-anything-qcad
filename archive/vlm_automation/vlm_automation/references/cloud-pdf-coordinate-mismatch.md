# Cloud PDF → DXF Coordinate Mismatch Analysis (Pair 3)

Date: 2026-05-13
Context: VLM-CAD automation pipeline, Pair 3 entity duplication via cloud polygon selection.
File: `3_cloud.pdf` in `/home/hongbin/.hermes/kanban/workspaces/testfiles_2026.05.07/`

## Problem Statement

When extracting polygon vertices from a cloud-markup PDF (`3_cloud.pdf`) and mapping them to DXF coordinates using standard transforms, the resulting polygon falls in an **empty region** of the target drawing, missing the actual source entities that need duplication.

## Raw Data

### PDF Annotation Vertices (from `annot.vertices`)
```
v0: (707.0107, 602.7500)
v1: (673.5000, 598.2070)
v2: (698.0124, 393.5000)
v3: (717.2500, 441.0690)
```

### Target Entities (terminals 4/5/6, known from DXF inspection)
```
Position: x ≈ 18–21, y ≈ 19–20
```

## Coordinates After Mapping Variants

| Variant | Formula | Result bounds (x, y) | Hits target? |
|---------|---------|---------------------|--------------|
| v1 | `x=y/72, y=(1224−x)/72` | x=[5.47–8.37], y=[7.04–7.65] | ❌ |
| v2 | `x=x/72, y=y/72` | x=[9.35–9.96], y=[5.47–8.37] | ❌ |
| v3 | `x=(792−x)/72, y=(1224−y)/72` | x=[1.04–1.65], y=[8.63–11.53] | ❌ |
| v4 | `x=(792−y)/72, y=(1224−x)/72` | x=[2.63–5.53], y=[7.04–7.65] | ❌ |
| v5 | `x=y/72, y=x/72` (swap_xy on raw) | x=[5.47–8.37], y=[9.35–9.96] | ❌ |
| v6 | `x=x/72, y=(1224−y)/72` | x=[9.35–9.96], y=[8.63–11.53] | ❌ |

None of the 6 variants land anywhere near the terminals at (x≈18–21, y≈19).

## Root Cause Hypotheses

1. **Page size mismatch** — The cloud PDF has a different `MediaBox`/`CropBox` than the base PDF.
2. **Zoomed / cropped view** — The user drew the cloud on a zoomed viewport, so the cloud coordinates are in a different scale/offset than the full-page DXF.
3. **Missing additional transformation** — The PDF might have an internal transformation matrix not captured by simple division by 72.
4. **Wrong source file** — The cloud was drawn on a different version of the drawing than the one loaded into the pipeline.

## Resolution Options

| Option | Description | Trade-offs |
|--------|-------------|-----------|
| A | Ask user for a new cloud PDF drawn on the exact same page scaling as the source DWG | Most reliable, but requires user action |
| B | Proceed without the cloud; use hard-coded source region `x=[16,23], y=[19.05,20.9]` known from terminal entity coordinates | Fast but loses user-directed selection flexibility |
| C | Attempt to derive additional scale/offset by fitting cloud polygon to known entity cluster | Requires at least partial known correspondences |

## Recommendation

**When cloud PDF coordinates do not match expected source regions after testing all 4 standard mapping transforms, immediately flag the mismatch and ask the user before proceeding.** Automated entity duplication on an incorrectly mapped polygon will clone the wrong geometry (or nothing at all), wasting time. Present the bounding-box comparison and the overlay screenshot for visual confirmation.

## Related Pitfalls

- `vlm-cad-automation` Pitfall #52 (PolygonCloud vertex extraction)
- `vlm-cad-automation` Pitfall #68 (PDF→DXF coordinate mapping must be empirically verified per drawing pair)
- `vlm-cad-automation` Pitfall #74 (Discord image size limits for overlay verification)
