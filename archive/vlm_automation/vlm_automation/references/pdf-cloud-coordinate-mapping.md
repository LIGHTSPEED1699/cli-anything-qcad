# PDF Cloud Annotation → DXF Coordinate Mapping

**Date:** 2026-05-11 (revised)
**Verified on:** Pair 1 (4 Polygon annotations on 270°-rotated portrait PDF)
**Source drawing rotation:** Portrait 792×1224 displayed at 270° (landscape 1224×792)

## No Single Mapping Works For All Drawings

The correct PDF→DXF coordinate mapping depends on the DWG's internal coordinate convention (origin corner, axis direction), which varies between files. **You must empirically verify per drawing pair** by testing all 4 candidate transforms and checking which one aligns clouds with actual entity clusters.

## The Four Candidate Mappings

| Mapping | Formula | When it works |
|---------|---------|---------------|
| `raw_direct` | `(x/72, y/72)` | Simple landscape PDFs with no rotation |
| `swap_xy` | `(y/72, x/72)` | Portrait→landscape with simple axis swap |
| `pymupdf_yflip` | `(x/72, (page_h−y)/72)` | Portrait with y-flip only |
| `pymupfi_swap` | `((page_h−y)/72, x/72)` | 270°-rotated portrait, DWG origin at top-right |

## Pair 1 Verification (2026-05-11)

**4 type=6 Polygon annotations + their internal Line (strikethrough) annotations.** Page rect: 1224×792 (landscape).

### Automated PIP counts (entity centroids only)

| Mapping | C0 | C1 | C2 | C3 | Total |
|---------|----|----|----|----|-------|
| `raw_direct` | 11 | **0** | 24 | **0** | 35 |
| `swap_xy` | 21 | **0** | 29 | **0** | 50 |
| `pymupfi_swap` | 8 | 4 | 4 | 5 | 21 |

### Why PIP alone is misleading

C1 and C3 are **thin horizontal strips** in DXF space after swap_xy (~0.4 units tall). They are strikethrough-style review markups that extend horizontally across the review area but are very narrow vertically. Entity insertion points (especially TEXT origin points) fall just outside these thin strips (0.1–0.5 units above/below).

**User ground-truth confirmed: `swap_xy` is correct for Pair 1.** The clouds visually align with the actual hand-drawn markup positions on the PDF. The pymupfi_swap mapping shifted clouds to different positions that *happened* to contain entities by coincidence (different entities from what was actually clouded).

### Strikethrough Line annotations fix the thin-cloud problem

Each Polygon (cloud) annotation is accompanied by Line (strikethrough) and FreeText annotations that point to the exact rows of entities to delete. These Lines span the entities' X-range in DXF space and provide precise spatial targeting:

- **C1**: 5 Line annotations, each spanning a specific text row on the right side of the drawing
- **C3**: 6+ Line annotations spanning rows on the right/upper area

Using the **union of cloud polygon (expanded) + strikethrough line proximity matching** found 104 entities for deletion vs. 0-50 from polygon PIP alone.

## Verification Protocol

### Step 1: Extract annotation structure

1. Extract **only type=6 Polygon annotations** as cloud boundaries
2. Extract **type=3 Line (strikethrough) annotations** grouped with each cloud
3. Extract **type=2 FreeText annotations** for instruction text ("delete clouded objects")

### Step 2: Generate overlay images

Render all DXF entities as colored dots/symbols. Overlay cloud polygons (dashed outlines labeled C0–C3) for each mapping. Generate one overlay per mapping at figsize 8×5, DPI 100 (target ~25–30 KB for reliable Discord delivery).

### Step 3: User visual confirmation

**User confirmation overrides automated PIP counts.** Thin annotation polygons can have 0 entities inside their strict boundary while still correctly marking the region. The strikethrough lines inside each cloud provide the precise spatial targeting.

### Step 4: Entity matching strategy

Use **cloud polygon (with buffer) UNION strikethrough line proximity** for entity matching:

```
handles_to_delete = handles_inside_cloud_polygon(expanded) | handles_near_strikethrough_lines(tolerance=0.8)
```

Where:
- `expanded`: add buffer to polygon vertices proportional to narrow dimension (min 0.3 units)
- `tolerance`: match entities within 0.8 DXF units of any strikethrough line endpoint

## Important: PDF annotation grouping

PDF annotations are ordered: Polygon → Line → Line → ... → FreeText. Group them sequentially:
- Annot 0 (Polygon C0) → Annot 1-3 (Lines) are C0's strikethroughs
- Annot 4 (Polygon C1) → Annot 5-9 (Lines), Annot 10 (FreeText "mark spare on both ends")
- etc.

FreeText annotations inside a group contain the user's instruction (e.g., "delete clouded objects", "mark spare on both ends").

## Related

- `references/pdf-cloud-mixed-scale-mapping.md`
- `references/direct-dwg-deletion-pipeline.md`
- `references/entity-labeling-overlay.md`