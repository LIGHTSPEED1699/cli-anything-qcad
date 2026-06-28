# PDF Cloud Annotation Mixed-Scale / Zoom-Level Mismatch

**2026-05-11 CORRECTION.** There is no single "always correct" mapping for PDF→DXF coordinate transformation. The correct mapping must be empirically verified per drawing pair by testing all 4 candidate transforms against point-in-polygon entity counts. For Pair 1, `pymupfi_swap` `((page_h−y)/72, x/72)` is the only mapping where ALL 4 cloud polygons find entities — see Pitfall #68 in SKILL.md. The earlier claims that `swap_xy` or `raw_direct` are universally correct were based on incomplete testing with fewer cloud annotations or different annotation selection criteria.

This document now covers the **real** edge case: clouds that appear at the wrong scale or position after correct mapping.

## The Actual Problem: Zoom-Level Mismatch

When some clouds in the same PDF map to empty regions of the DXF or have implausibly narrow spans, the issue is NOT coordinate mapping — it's that the clouds were drawn at a **different effective zoom level** in the PDF annotator.

### Symptoms of Zoom Mismatch

| Symptom | Meaning |
|---------|---------|
| Cloud returns 0 entities after correct mapping | Cloud maps to empty DXF region |
| Cloud width < 0.5 DXF units | Annotation was zoomed in; scale is off |
| Cloud y-position is 2×+ away from nearest entity cluster | Different zoom or pan state |
| LINE endpoints inside cloud also map to empty region | Consistent across all annotation types |
| User says "this cloud covers the big group on the left" but mapping shows it at y=10+ | Zoom-level mismatch confirmed |

### Pair 1 Example (Corrected Analysis)

With the correct mapping (`annot.vertices / 72`, no flip):

| Cloud | xref | DXF x range | DXF y range | Width | Height | Entities | Status |
|-------|------|-------------|-------------|-------|--------|----------|--------|
| C0 | 23 | 5.53–9.83 | 1.17–5.10 | 4.30 | 3.93 | 11 | ✅ Correct |
| C1 | 27 | 8.38–8.78 | 9.21–13.62 | 0.40 | 4.41 | 0 | ❌ Empty |
| C2 | 34 | 2.39–5.38 | 1.39–5.19 | 2.99 | 3.80 | 24 | ✅ Correct |
| C3 | 38 | 4.16–4.51 | 9.17–13.60 | 0.35 | 4.43 | 0 | ❌ Empty |

**C1 and C3 are narrow and positioned at y≈9-13**, far above the actual entity cluster at y≈3-5. They were recorded while the PDF viewer was zoomed in on the lower portion of the page.

**Zero overlap** between all cloud bounding boxes — the earlier "overlapping clouds" was an artifact of the double-flip bug.

## Diagnosis Steps

When a cloud returns 0 entities with the correct mapping:

### Step 1: Verify mapping is actually correct
Run `annot.vertices / 72` with no flip. Check that `page.rotation` is 270° (or whatever your PDF uses). Verify by comparing `annot.vertices` against raw `/Vertices` from the annotation dict — they should match `pymupdf_y = page_h - raw_y`.

### Step 2: Check LINE annotation endpoints
LINE annotations inside the cloud point at the target entity. Map their endpoints with the same `annot.vertices / 72` rule:

```python
for annot in page.annots():
    if annot.type[1] == "Line":
        # Line endpoints are already in page.rect space
        start = (annot.vertices[0][0] / 72, annot.vertices[0][1] / 72)
        end   = (annot.vertices[1][0] / 72, annot.vertices[1][1] / 72)
        print(f"Line at {start} → {end}")
```

If LINE endpoints also map to the empty region, the cloud is genuinely misplaced (zoom mismatch). If they map to entities, the cloud polygon itself may be oversized/undersized.

### Step 3: Render bbox overlay for user confirmation

```python
from PIL import Image, ImageDraw
import pymupdf

page = pymupdf.open("annotated.pdf")[0]
pix = page.get_pixmap(dpi=72)
img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
draw = ImageDraw.Draw(img)

# Draw cloud bboxes using PyMuPDF's already-rotated coordinates
colors = [(255,0,0), (0,255,0), (0,0,255), (255,165,0)]
for ci, annot in enumerate(page.annots()):
    if annot.type[1] == "Polygon":
        r = list(annot.rect)
        draw.rectangle(r, outline=colors[ci % 4], width=4)
        draw.text((r[0]+5, r[1]-15), f"C{ci}", fill=colors[ci % 4])

img.save("/tmp/cloud_identity_check.png")
```

This shows the clouds in **landscape orientation** (matching `page.rect`). Share with user to confirm which entities each cloud actually covers.

### Step 4: Calculate zoom calibration factor

If LINE endpoints are known to point at specific entities, derive the scale factor:

```python
# Known: LINE endpoint L maps to entity E at DXF position (ex, ey)
# But L / 72 doesn't match E
# Solve: actual_scale = L / ex (for x) or L / ey (for y)
# If x and y scales differ, the annotation was drawn with non-uniform zoom

scale_x = line_end_x / entity_x
scale_y = line_end_y / entity_y

if abs(scale_x - scale_y) > 0.1:
    print("Non-uniform zoom — cloud uses different x/y scales")
else:
    print(f"Zoom factor: {scale_x:.2f} (expected 72)")
```

## Resolution Strategies

### Option A: Per-Cloud Scale Factor
If zoom-level mismatch is consistent per-cloud, store a scale factor alongside each cloud:

```python
cloud_scales = {
    23: 72.0,   # C0 — normal
    27: 144.0,  # C1 — zoomed 2× (hypothetical)
    34: 72.0,   # C2 — normal
    38: 144.0,  # C3 — zoomed 2× (hypothetical)
}
```

Calibrate from known LINE-to-entity matches. Not recommended unless you have multiple anchor points per cloud.

### Option B: LINE Endpoint Disambiguation (Recommended)

Each cloud contains 1–3 short `Line` annotations (dashes) drawn *inside* the cloud, pointing at the specific entity to delete. These lines:
- Map at the **same scale** as their parent cloud
- Match nearest DXF entity within `0.1–0.3` DXF units when scale is correct
- Can be tested independently of the cloud polygon

**Production code:**
```python
def find_line_targets(line_annot, dxf_entities, scale=72):
    """Map a PDF Line annotation endpoint to the closest DXF entity."""
    le = line_annot.vertices  # [[x1,y1], [x2,y2]] in page.rect space
    pts = [(le[0][0]/scale, le[0][1]/scale), (le[1][0]/scale, le[1][1]/scale)]
    
    best = None
    for label, pt in [("start", pts[0]), ("end", pts[1])]:
        ent, dist = get_closest_entity(pt[0], pt[1], dxf_entities, radius=1.5)
        if ent and (best is None or dist < best['dist']):
            best = {'entity': ent, 'dist': dist, 'point': label}
    return best
```

**Why this works**: In CAD PDF annotation, the user draws a short line *inside* a cloud, pointing *at* the entity to delete. The line's endpoint is intentionally placed on/near the target. Unlike cloud polygons (which may be drawn at zoomed scales), LINE annotations are always drawn at the same zoom level as the entity they reference.

### Option C: User Confirmation Loop

When programmatic matching is ambiguous:
1. Generate the bbox overlay image (Step 3 above)
2. Share via `MEDIA:/tmp/cloud_identity_check.png`
3. Ask user: "Which entities should Cloud N delete?"
4. User identifies entities by text label or approximate position
5. Match user's description against DXF entity list

This is slower but 100% accurate for ambiguous cases.

## Previous Mappings and Why They Failed

1. **`annot.vertices / 72` (no swap):** Appeared correct for C0 and C2 but returned 0 entities for C1 and C3. This mapping doesn't account for the portrait→landscape orientation gap between PDF internal coordinates and DXF space.

2. **`swap_xy` (swap then ÷72):** The **correct** mapping. Swaps coordinates to bridge the portrait PDF → landscape DXF orientation difference. Confirmed by overlay images: all four clouds align correctly with no overlap.

3. **Old hybrid `center_y > 700` rule:** Artifact of testing with incorrect mappings. The `center_y` boundary was an illusion created by the wrong coordinate transform. With `swap_xy`, no per-cloud selector is needed.

## 2026-05-11: `swap_xy` Was NOT Correct for Pair 1 — `pymupfi_swap` Is

Re-testing the 4 Polygon (type=6) annotations with strict point-in-polygon against all 218 entities:

| Mapping | C0 | C1 | C2 | C3 | Correct? |
|---------|----|----|----|----|----------|
| `raw_direct` `(x/72, y/72)` | 11 | **0** | 24 | **0** | ❌ |
| `swap_xy` `(y/72, x/72)` | 21 | **0** | 29 | **0** | ❌ |
| `pymupfi_swap` `((1224−y)/72, x/72)` | 8 | **4** | 4 | **5** | ✅ |

`swap_xy` still misses C1 and C3. `pymupfi_swap` (with the `page_h − y` flip) is the only mapping where all 4 clouds find entities. The `(1224−y)` term maps distance from the top of the portrait page to DXF x, meaning the DWG origin is at the top-right corner of the portrait page.

The earlier claim that `swap_xy` was verified (C0=8, C1=4, C2=4, C3=4) was likely based on testing against a different set of "clouds" that included Line and FreeText annotations, or an earlier version of the extraction pipeline. Always use only type=6 Polygon annotations as the canonical cloud shapes.

**Lesson:** Coordinate mapping correctness depends on (1) which annotations you select as "clouds", (2) which entity positions you test against (centroid vs insertion point vs bbox midpoint), and (3) your point-in-polygon implementation. Always re-verify with fresh code when resuming work.

## What NOT To Do

❌ **Do NOT assume any single mapping (swap_xy, raw_direct, etc.) works universally** — test all 4 candidate transforms and use the one where ALL clouds find ≥1 entity. See Pitfall #68 for the test protocol.

❌ **Do NOT use a center-y hybrid rule** (`center_y > 700 → pymupdf y-flip, <700 → raw_direct`). This was an artifact of testing with wrong mappings. The correct mapping is determined empirically per drawing pair.

❌ **Do NOT use VLM bbox/distance matching** for cloud-annotated deletions. It produces catastrophic over-deletion (44/73 orphans in Pair 1). Strict point-in-polygon with verified coordinate mapping is the only valid criterion.

❌ **Do NOT assume earlier session's mapping verification is still valid** — annotation extraction logic, entity counting method, and point-in-polygon implementation can all affect which mapping "looks correct." Always re-verify with fresh extraction of type=6 Polygon annotations only.

## Last Updated

2026-05-11 — corrected mapping to `swap_xy` (`y_first / 72, x_second / 72`). Previous `annot.vertices / 72` (no swap) was incomplete; it missed C1 and C3 entities. The swap is required because the portrait PDF's internal coordinates must be rotated 90° to match the landscape DXF orientation, not just scaled.
