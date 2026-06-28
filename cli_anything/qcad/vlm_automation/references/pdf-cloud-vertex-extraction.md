# PDF Cloud Annotation Vertex Extraction → DXF Coordinate Mapping

**Discovered 2026-05-09. Corrected 2026-05-10.** Cloud/markup annotations in PDFs are stored as `Polygon` annotations with per-vertex coordinate data. These vertices can be extracted directly from the PDF without VLM, enabling deterministic point-in-polygon testing against DXF entity coordinates.

## The 1:72 Scale Rule

CAD applications export PDFs at a standard 1:1 mapping where **1 DXF model-space unit = 72 PDF points** (1 inch = 72 points). This is consistent across QCAD, AutoCAD, and LibreCAD PDF exports when the drawing limits match the paper size.

```
DXF_coordinate = PDF_coordinate / 72
```

**Proof (calibrated on Pair 1):**

| Line annotation endpoint | PDF | DXF (÷72) | Nearest entity | Distance |
|---|---|---|---|---|
| Line 1 | (467, 286) | (6.488, 3.965) | "#2 Wht" (6.215, 4.034) | **0.28** ✓ |
| Line 8 | (242, 203) | (3.360, 2.813) | "tank" (3.254, 3.108) | **0.31** ✓ |
| Line 12 | (300, 730) | (4.170, 10.140) | "FIELD" (3.850, 10.306) | **0.36** ✓ |

Distances < 0.5 DXF units confirm the scale is correct. Callout lines don't need pixel-perfect precision — they visually point toward entities.

## 2026-05-11 CORRECTION: The Correct Mapping Requires Coordinate Swap

**Previous correction (2026-05-10) was also incomplete.** The `annot.vertices / 72` mapping (no flip) places clouds correctly for C0 and C2 but returns **0 entities for C1 and C3**. The actual correct mapping accounts for the **orientation difference** between the PDF (portrait MediaBox internally, landscape `page.rect` for display) and the DXF (native landscape):

### The Correct Mapping

```python
# For a 270°-rotated portrait PDF (792×1224) mapped to a landscape DXF
# PyMuPDF annot.vertices are in the ROTATED (landscape display) space
# But the DXF is in a DIFFERENT landscape orientation
# A 90° coordinate swap bridges this:

# CORRECT — swap x and y, then divide by 72
# Using raw /Vertices from PDF dict (before PyMuPDF rotation):
raw_verts = extract_raw_vertices(annot)  # from /Vertices in annotation dict
dxf_verts = [(raw_y / 72.0, raw_x / 72.0) for raw_x, raw_y in raw_verts]

# OR using PyMuPDF annot.vertices (already rotated):
dxf_verts = [(annot_y / 72.0, annot_x / 72.0) for annot_x, annot_y in annot.vertices]
```

**Why this works:** The PDF page is portrait (792 wide × 1224 tall) but displayed at 270° rotation (1224 wide × 792 tall landscape). The raw `/Vertices` are in portrait coordinates. The DXF is in landscape orientation. A 90° swap (`x_dxf = y_portrait, y_dxf = x_portrait`) correctly transforms portrait coordinates to landscape DXF space.

### Verification

| Cloud | Mapping | C0 entities | C1 entities | C2 entities | C3 entities | Overlap |
|-------|---------|-------------|-------------|-------------|-------------|---------|
| `annot.vertices / 72` (no swap) | 11 | **0** | 24 | **0** | No |
| `swap_xy` (swap then ÷72) | 8 | **4** | 4 | **4** | No |

The `swap_xy` mapping is the **only one that finds entities for all four clouds** without artificial overlap. This was confirmed by overlay images shared with the user for visual verification.

### Previous Mappings and Why They Failed

1. **`raw_direct` (raw /Vertices ÷72, no swap):** Used by some bottom-half clouds in the old hybrid rule. Places clouds at portrait positions — wrong for landscape DXF.

2. **`pymupdf` (annot.vertices ÷72, no swap):** Correctly handles PyMuPDF's internal rotation but doesn't account for the portrait→landscape orientation gap. C0 and C2 work because they happen to align; C1 and C3 are empty.

3. **`swap_xy` (annot.vertices with y-first ÷72):** The **correct** mapping. Swaps coordinates to bridge the portrait PDF → landscape DXF orientation difference.

### Production Code (Corrected)

```python
import pymupdf
import ezdxf

def extract_cloud_polygons(pdf_path):
    """Extract PolygonCloud annotations from PDF, correctly mapped to DXF coords."""
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    clouds = []
    
    for annot in page.annots():
        if annot.type[1] == "Polygon":
            # Use PyMuPDF's already-rotated coordinates
            # BUT swap x/y to bridge portrait→landscape orientation
            verts = annot.vertices
            dxf_verts = [(v[1] / 72.0, v[0] / 72.0) for v in verts]
            clouds.append({
                'xref': annot.xref,
                'vertices': dxf_verts,
                'bbox': (
                    min(v[0] for v in dxf_verts),
                    min(v[1] for v in dxf_verts),
                    max(v[0] for v in dxf_verts),
                    max(v[1] for v in dxf_verts),
                )
            })
    
    doc.close()
    return clouds

def point_in_polygon(px, py, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

# Usage
doc_dxf = ezdxf.readfile("drawing.dxf")
msp = doc_dxf.modelspace()
clouds = extract_cloud_polygons("annotated.pdf")

for cloud in clouds:
    inside_entities = []
    for entity in msp:
        try:
            pos = entity.dxf.insert if hasattr(entity.dxf, 'insert') else entity.dxf.start
            if point_in_polygon(pos.x, pos.y, cloud['vertices']):
                inside_entities.append(entity)
        except AttributeError:
            continue
    print(f"Cloud xref={cloud['xref']}: {len(inside_entities)} entities")
```

### What About the `center_y > 700` Hybrid Rule?

**The hybrid rule is obsolete and wrong.** It was an artifact of testing with incorrect mappings. The correct rule is simple and universal:

**Always use `swap_xy` (`y_first, x_second / 72`). No per-cloud selector needed.**

If some clouds still return 0 entities after using the correct mapping, investigate:
1. **Zoom-level mismatch** — clouds drawn at different PDF zoom scales
2. **Entity type mismatch** — cloud targets LINE/CIRCLE/ARC, not TEXT insert points
3. **LINE endpoint disambiguation** — use LINE annotations inside the cloud to pinpoint targets

## Last Updated

2026-05-11 — corrected mapping to `swap_xy` (`y_first / 72, x_second / 72`). Previous `annot.vertices / 72` (no swap) was incomplete; it missed C1 and C3 entities. The swap is required because the portrait PDF's internal coordinates must be rotated 90° to match the landscape DXF orientation, not just scaled.

## Edge Cases

### Zoom-Level Mismatch (C1 and C3 on Pair 1)

Clouds C1 and C3 map to y≈9-13 in DXF space, which is far above the actual entity cluster at y≈3-5. They were recorded at a **different zoom level** in the PDF annotator.

**Symptoms:**
- Correct mapping places cloud in an empty region of the DXF
- Cloud polygon is narrow (width < 0.5 DXF units)
- LINE annotations inside the cloud also map to the empty region

**Fix options:**
1. **Zoom calibration** — calculate a separate scale factor for these clouds from known LINE endpoints
2. **LINE endpoint disambiguation** — map the LINE annotation endpoints (which point at the target) independently; they may use a different scale than the cloud polygon itself
3. **User confirmation** — render the cloud bbox overlay and ask the user which entities the cloud actually covers

### Multi-Scale / Off-Page Annotations
PDF annotations can have coordinates outside the page boundary. If `annot.rect.y1 > page.rect.height`, the annotation extends above the page. The visible portion is `[y0, min(y1, page_h)]`. At 1:72 scale, these partially-visible clouds may map to a narrow DXF x-band (0.3–0.5 units wide) — verify the cloud actually covers the expected entities before trusting the mapping.

### Verifying Cloud Coverage (When Vision Is Slow)
When VLM vision analysis times out on large CAD images:
1. **Render specific crop**: `page.get_pixmap(clip=Rect(x0, y0, x1, y1), dpi=72)`
2. **Annotate cloud bboxes**: Use PIL to draw colored rectangles on the rendered image
3. **Share with user**: Use `MEDIA:/path` to send annotated image for visual confirmation
4. **Lower DPI**: Try 72 DPI (1:1 page mapping) for faster renders; crops can use higher DPI

### Verify Scale with Line Annotations
Before running the pipeline, verify scale calibration by matching a few `Line` annotation endpoints against known DXF entity positions:

```python
for annot in page.annots():
    if annot.type[1] == "Line":
        end = annot.vertices[1]  # Line points AT the entity
        dxf_x = end[0] / 72.0
        dxf_y = end[1] / 72.0
        # Verify nearest DXF entity is within 0.5 DXF unit
```

### Non-Uniform Scale
If the DXF limits don't match the page size 72:1 (e.g., metric drawings at 1:100), compute the scale from `$LIMMIN`/`$LIMMAX` vs page rect before transforming.

## Advantages Over VLM Approach

| Aspect | VLM Cloud Interpretation | Vertex Extraction |
|---|---|---|
| Deterministic | ❌ Hallucinates handles | ✅ Exact geometric test |
| Speed | ~30s per cloud | <1s for all clouds |
| False positives | 12/13 for one cloud | 0 (exact polygon boundary) |
| Resolution dependent | Yes (cloud image quality) | No (raw vertex data) |
| Handles overlapping | Badly | Natively |

## Related Pitfalls

- **Pitfall 51** (bbox envelopes): Using `annot.rect` instead of `annot.vertices` captures 3×–10× more entities. Always use polygon vertices, not bounding rectangles.
- **Pitfall 66** (VLM over-deletion): VLM bbox/distance matching deleted 73 entities but only 29 were inside any cloud polygon. Strict point-in-polygon is the only valid criterion.
- **Pitfall 67** (double-flip): PyMuPDF `annot.vertices` already applies page rotation. Do NOT apply an additional y-flip.
- **Pitfall 54** (negative layer colors): Before clearing entities, verify DXF layer colors are positive (`62 ≥ 0`).

## Production Pipeline Integration

Replace the VLM cloud disambiguation step with this deterministic extraction:

```
PDF → pymupdf (extract Polygon vertices) → ÷72 → point-in-polygon tests → entity deletion list
```

No VLM call needed. No image rendering. No hallucination risk. No per-cloud mapping selector.

If a cloud returns 0 entities, investigate zoom-level mismatch or entity-type mismatch — do NOT fall back to hybrid/distance heuristics.
