# Annotation Rect-Based Entity Deletion (v6 Hybrid Pipeline)

**Date:** 2026-05-11  
**Status:** Validated on Pair 1 (v6)  
**Replaces:** PIP + strikethrough-line proximity union (v1), pure bbox (v2/v5)

## Version History

| Version | Method | Count | Issues |
|---------|--------|-------|--------|
| v1 | PIP expanded + line proximity tol=0.8 | 104 | 25 false positives |
| v2/v5 | Pure `annot.rect` bbox for all clouds | 96 | Over-catches thin clouds (C1/C3 too wide) |
| v3/v6 | Hybrid: bbox for large, polygon for thin | 93 | Label boxes wrongly deleted; HATCH dots missed |
| v4/v7 | Hybrid + HATCH + label box exclusion | 92 | Best result; all user issues addressed |

## Why v1 Failed

The v1 pipeline (`pip_in_expanded_cloud ∪ proximity_near_lines(tol=0.8)`) produced 104 deletions with 25 false positives. Entities like Tb703, 101, 102, 104, etc. were incorrectly deleted because they fell within the strikethrough line proximity tolerance but were outside the actual cloud boundary.

## Why v2/v5 (Pure Bbox) Over-Deletes Thin Clouds

Cloud C1 (right-top narrow strip) has a polygon only 0.4 DXF units tall but a bounding rectangle ~4.4 units wide. The bbox catches entities horizontally adjacent to the strip but vertically outside the actual cloud — wire labels, text, and structural lines that are on the same row but NOT inside the red markup.

Similarly C3 (right-bottom strip, 0.36 units tall) has a bbox spanning 4.4 units wide.

For large clouds (C0: 4.3×3.9 units, C2: 3.0×3.8 units), bbox and polygon boundaries are similar, so both methods work well.

## v6 Hybrid Method

### Algorithm

1. **Classify clouds** by bounding rectangle height:
   - Height >1 DXF unit → "large" → use bbox intersection testing
   - Height <1 DXF unit → "thin" → use strict polygon PIP with 0.03-unit buffer

2. **For large clouds (bbox)**: Test if any part of the entity intersects the `annot.rect` bounding box (with 0.05-unit buffer). Sample points along LINE/POLYLINE edges, CIRCLE perimeters, and TEXT bounding boxes.

3. **For thin clouds (polygon + explicit)**: Use strict point-in-polygon on the actual annotation vertices with a 0.03-unit buffer. Additionally, explicitly add specific entity handles that are confirmed deletion targets (e.g., thin red POLYLINEs with color=1 that run through the strip).

4. **Structural line exclusion**: Always exclude LINES spanning >80% of page height/width with color=256 (BYLAYER). These are page dividers, not deletion targets.

5. **User exclusion override**: After geometric matching, present deletion list for review. Common false positive patterns that should be excluded:
   - Wire labels outside the cloud (Tb703, 101-108)
   - Terminal block identifiers (F171-F178)
   - Power rail labels (0v, 24v)
   - Long vertical dividers (entity 4832 in Pair 1)
   - **Label-box POLYLINEs** (n=5 vertices) surrounding kept text entities — preserve these when their associated text is kept
   - **Page structural elements** (borders, dividers) identified by extreme length + color=256

6. **HATCH entity inclusion**: HATCH fill patterns (entity_type=HATCH) render as colored dots/circles in AutoCAD. Test HATCH edge coordinates against cloud bboxes/polygons and include matching HATCHes in the deletion list. A HATCH with 99-edge circular paths is typically a filled dot/circle.

7. **Text insertion-point edge case**: TEXT entities whose insertion point falls just outside the cloud bbox but whose content extends into the cloud area (e.g., "Hydrogen Peroxide" at x=0.82 where cloud starts at x=1.17) should be included in the deletion list via content-based override or insertion-point+text-width estimation.

### Implementation

```python
import fitz, numpy as np, ezdxf, json

def extract_clouds(pdf_path, mapping='swap_xy'):
    """Extract cloud polygons AND bounding rectangles, classify by size."""
    pdf = fitz.open(pdf_path)
    clouds = {}
    cloud_idx = 0
    for annot in pdf[0].annots() or []:
        if annot.type[0] == 6 and len(annot.vertices) > 2:
            verts = np.array(annot.vertices)
            rect = annot.rect
            
            if mapping == 'swap_xy':
                # Polygon vertices: swap x/y and divide by 72
                poly = np.column_stack([verts[:,1]/72.0, verts[:,0]/72.0])
                # Bounding rect: same transform
                bbox = (rect.y0/72.0, rect.x0/72.0, rect.y1/72.0, rect.x1/72.0)
            else:
                poly = np.column_stack([verts[:,0]/72.0, verts[:,1]/72.0])
                bbox = (rect.x0/72.0, rect.y0/72.0, rect.x1/72.0, rect.y1/72.0)
            
            height = bbox[3] - bbox[1]
            is_thin = height < 1.0  # Threshold for "thin cloud"
            
            clouds[f"C{cloud_idx}"] = {
                'polygon': poly,
                'bbox': bbox,
                'is_thin': is_thin,
            }
            cloud_idx += 1
    return clouds


def pip(px, py, poly):
    """Point-in-polygon using ray casting."""
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj-xi)*(py-yi)/(yj-yi) + xi):
            inside = not inside
        j = i
    return inside


def entity_intersects_polygon(entity, poly, buf=0.03):
    """Test if any point of entity is inside polygon with small buffer."""
    # Collect sample points for this entity type
    test_points = get_entity_points(entity)
    for px, py in test_points:
        if (pip(px, py, poly) or pip(px+buf, py, poly) or 
            pip(px-buf, py, poly) or pip(px, py+buf, poly) or 
            pip(px, py-buf, poly)):
            return True
    return False


def entity_intersects_bbox(entity, bbox, buf=0.05):
    """Test if any point of entity is inside bounding box with buffer."""
    xmin, ymin, xmax, ymax = bbox
    test_points = get_entity_points(entity)
    for px, py in test_points:
        if (xmin-buf <= px <= xmax+buf and ymin-buf <= py <= ymax+buf):
            return True
    return False
```

### Structural Line Exclusion

```python
EXCLUDED_HANDLES = set()  # Handles to always exclude

# Auto-detect page divider lines
for e in msp:
    if e.dxftype() == 'LINE':
        length = ((e.dxf.end.x - e.dxf.start.x)**2 + 
                  (e.dxf.end.y - e.dxf.start.y)**2)**0.5
        # Page height is ~11 inches; a divider spanning >80% is structural
        if length > 8.0 and e.dxf.color == 256:  # BYLAYER color
            EXCLUDED_HANDLES.add(e.dxf.handle)
```

## Pair 1 Results

| Cloud | Type | Height | v1 | v5(bbox) | v6(hybrid) | Change v5→v6 |
|-------|------|--------|----|----------|------------|---------------|
| C0 | Large | 4.31 | 34 | 43 | 43 | Same |
| C1 | Thin | 0.40 | 11 | 5 | 3+explicit | Restored 4B6E, 36F3, 41CC |
| C2 | Large | 2.99 | 46 | 40 | 40 | Same |
| C3 | Thin | 0.36 | 16 | 4 | 3+explicit | Restored 4832 (divider) |
| **Total** | | | **104** | **96** | **93** | **-3 over-deletions fixed** |

### v7 Corrections (2026-05-11)

User reported three remaining issues with v6:

1. **Label boxes wrongly deleted** — 8 POLYLINE n=5 rectangle borders surrounding kept texts (101, 102, 104, 105, 106, 108, F175, F176) were deleted. These are formatting boxes integral to the text label, not graphical content to be removed. v7 restores handles 3246, 324D, 3254, 34DA, 36E5, 36EC, 406D, 4467.

2. **HATCH dots missed** — 6 HATCH fill entities inside C0/C2 cloud areas render as small filled circles ("dots" in yellow/white) in AutoCAD. These were invisible to the V6 pipeline which only checked CIRCLE, TEXT, LINE, POLYLINE, LWPOLYLINE, ARC, ELLIPSE, INSERT, MTEXT. v7 adds handles 48DB, 48DD, 48DE, 48DF, 4B4B, 4B4C.

3. **"Hydrogen Peroxide" text missed** — TEXT entity 36DC at (0.82, 8.12) had its insertion point just outside C0's left edge (x=1.17) but content clearly extending into the cloud. v7 adds this handle explicitly.

**v7 final result: 92 deletions** (93 from v6 - 8 label boxes restored + 6 HATCHes + 1 text = 92 net)

| Cloud | v6 Deletions | v7 Changes | v7 Deletions |
|-------|-------------|-------------|-------------|
| C0 | 43 | -5 label boxes +3 HATCH +1 text | 42 |
| C1 | 3+2 explicit | -1 label box (406D is C0, not C1) | 3+2 explicit |
| C2 | 40 | -2 label boxes +1 HATCH | 39 |
| C3 | 3+2 explicit | no change | 3+2 explicit |
| **Total** | **93** | | **92** |

**Explicit additions for thin clouds:**
- C1: handle `4BB8` (POLYLINE, color=1, y=8.764, horizontal thin red line)
- C3: handle `4BDF` (POLYLINE, color=1, y=4.235, horizontal thin red line)

**Structural line excluded:**
- handle `4832` (LINE, color=256, x=11.69, spanning y=2.07→10.61, ~8.5 units — page divider)

## Always Start From Original DXF

Each iteration must start from the **original unmodified DXF**. Never apply a new deletion list to a previously modified DXF — entity boundaries and handle references shift after text-based deletion. The proven workflow:

1. Build handle list from coordinate analysis of original DXF
2. Run `delete_entities_text.py` against original DXF → produces modified DXF
3. Verify with ezdxf (check entity count, verify keep-texts present)
4. Fix layer colors if needed
5. Export via QCAD Pro ODA with force-visible script

## QCAD Export: Known Layer Visibility Bug (Confirmed 2026-05-11)

Three programmatic approaches have been tested and ALL fail to prevent hidden layers in exported DWGs:

1. **Pre-fix DXF 62 values + force-visible script**: DXF verified with ezdxf having all-positive 62 values. QCAD script reports "Fixed 0 layer states" because layers already ON. DWG STILL opens with all layers hidden.
2. **Double-pass DWG→QCAD→DWG**: Open hidden DWG in QCAD (where isOff()=false), force setOff(false), re-save. QCAD reports "Fixed 0 layer states". DWG STILL opens with all layers hidden.
3. **Root cause**: ODA writer caches layer visibility from an internal source that is NOT the DXF LAYER 62 values or the runtime RLayer state.

**Only reliable fix**: Open in AutoCAD/TrueView → auto-recovery makes all layers visible. Or manually "Show All Layers" in QCAD/AutoCAD → Save.

**Accept this as a known limitation.** All QCAD-exported DWGs in this workflow will start with hidden layers. The content is correct; only the initial view state is affected.