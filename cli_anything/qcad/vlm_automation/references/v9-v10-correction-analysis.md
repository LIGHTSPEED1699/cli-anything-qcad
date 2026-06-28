# V9→V10→V11 Correction Analysis (2026-05-12)

## V9 Failures Reported by User

Five categories of errors in the V9 DWG (`1_FINAL_v9.dwg`, 112 deletions):

### 1. HATCH rectangle came back (mid-right side)
- **Entity:** HATCH 4B4C — DOTS pattern, rectangle shape at x=[2.49–3.61], y=[6.01–7.50]
- **Root cause:** HATCH path uses `PolyEdgePath` with `LineEdge` objects. The `path.vertices` list is EMPTY for edge-based HATCHes, so centroid-based PIP returned (0,0) → always outside. The `path.edges` (LineEdge.start/.end) contain the actual coordinates.
- **Fix:** Test HATCH edge start/end/center coordinates against cloud polygons, not just `path.vertices`.

### 2. 11 white dots came back (left side)
- **Entities:** HATCH 48DD (3 circle paths), 48DE (2 circle paths), 48DF (3 circle paths), 4D73 (3 ArcEdge paths)
- **Total visible dots:** 3+2+3+3 = 11
- **Root cause:** Same as #1 — edge-based HATCHes had empty `path.vertices`. Also 4D73 uses `ArcEdge` objects with `.center`, `.start_angle`, `.end_angle` — needed to use `.center` as the test point.
- **Fix:** Unified HATCH testing that checks ALL edge types (LineEdge, ArcEdge) for coordinates, falls back to vertex testing for PolylinePath HATCHes.

### 3. Red line between F172 and B239 (top right, inside C1 cloud)
- **Entity:** POLYLINE 4BB8 — color=1 (red), spans x=[9.17–13.93] at y=8.76
- **Root cause:** `contains_point()` returns False for points exactly ON the polygon boundary. The POLYLINE's start x=9.17 matches the C1 left boundary at x=9.207, and end x=13.93 is near right boundary at x=13.625 — both endpoints sit on or near polygon edges.
- **Also deleted:** Arrowhead triangles 4B73 (near C1) and 4BA7 (near C3) — previously not caught.

### 4. "Hydrogen Peroxide Tank Level Display" text on right side
- **Entities:** TEXT 36E2 ("Hydrogen Peroxide"), TEXT 36E3 ("Tank Level"), TEXT 36E4 ("Display")
- **Positions:** x≈15.4 (past rightmost cloud boundary at x≈13.6)
- **Root cause:** Text insertion points are past the C1/C3 cloud right edges. These texts are associated with the clouded instrument display but their anchor points are outside the polygon.
- **Fix:** Content-based sweep after geometric matching — search for text substrings of known deleted items.

### 5. Wrongly deleted items (should NOT have been deleted)
**Restored texts (7 unique, 9 total with duplicates):**
- Tb703 (2 instances), 101, 102, 104, 105, 106, 108

**Restored label boxes (10 POLYLINE n=5):**
- Handling around 101 (3246), 102 (324D), 104 (3254)
- Handling around 105 (34DA), 106 (36E5), 108 (36EC)
- Handling around F172 (36F3), F174 (406D), F175 (41CC), F176 (4467)

**Root cause:** V9's label-box matching only checked proximity to DELETED texts. When texts like 101–108 and Tb703 were erroneously marked for deletion (inside C0/C2 clouds geometrically but meant to be kept), their surrounding boxes were also deleted. The matching logic should have been bidirectional — find boxes near ALL kept texts, not just near deleted ones.

## V10 Corrections

**Removed from deletion list (restored):** 18 handles
- 6 texts: Tb703 (×2 handles), 101, 102, 104, 105, 106, 108
- 10 label-box POLYLINEs: 3246, 324D, 3254, 34DA, 36E5, 36EC, 36F3, 406D, 41CC, 4467

**Added to deletion list (new):** 11 handles
- 7 HATCHes: 4B4C, 48DD, 48DE, 48DF, 4D73, 4B73, 4BA7
- 1 POLYLINE (red line): 4BB8
- 3 TEXTs: 36E2, 36E3, 36E4

**Net change:** 112 → 105 deletions (7 fewer)

## V10 Verification Results
- All 12 kept texts present: ✓ (101, 102, 104, 105, 106, 108, F172, F174, F175, F176, Tb703×2)
- All 10 label boxes present: ✓
- All HATCH entities eliminated: ✓ (0 remaining)
- Red line 4BB8 deleted: ✓
- File: `1_FINAL_v10.dwg` — 46,785 bytes (45.7 KB)

## V11 Corrections

### User feedback on V10
User reported V10 "looks very good" with one remaining issue: the two short lines on the right side of F174 were wrongly deleted. These lines represent F174's electrical ground-reference connection.

### V10→V11 change
**Removed from deletion list (restored):** 1 handle
- 4B6E — POLYLINE n=3, color=7, forming an L-shape:
  - Horizontal: (9.17, 8.35) → (9.39, 8.35)
  - Vertical: (9.39, 8.35) → (9.39, 8.20)
  - Connects F174 box right edge (36F3 x_max=9.17) to ground chevron symbol below

**Root cause:** The L-shape POLYLINE fell inside the C1 cloud polygon during spatial matching, so it was correctly "inside" the cloud geometrically — but it's a functional ground-reference wiring element, not a deletion target. The cloud markup was intended to remove the instrument display, not the ground wiring attached to a kept instrument tag.

**Why it's distinct from strikethrough lines:** Strikethrough lines run horizontally across the C1 strip. Ground-reference lines start at a kept instrument box and extend outward/downward to a ground symbol. Their first vertex matches the kept box edge, and they don't span across the drawing.

**Net change:** 105 → 104 deletions (1 fewer)

### V11 Verification Results
- 4B6E POLYLINE present: ✓ (ground-reference L-shape for F174)
- All 8 previously restored texts present: ✓
- All 10 previously restored label boxes present: ✓
- All 11 V10 newly-deleted items confirmed gone: ✓
- File: `1_FINAL_v11.dwg` — 46,753 bytes (45.7 KB)

## Version Summary Table

| Version | Deletions | Key changes from previous | Status |
|---------|-----------|---------------------------|--------|
| V9 | 112 | Spatial classification of clouds | 5 issues |
| V10 | 105 | V9 − 18 wrongly deleted + 11 HATCH/boundary/content | Ground line on F174 wrongly deleted |
| V11 | 104 | V10 − 1 ground-reference POLYLINE (4B6E) | Boundary-touching entities wrongly deleted |
| V12 | 102 | V11 − 2 boundary-touching entities (4067 arrow + 4152 F194) | User-confirmed correct |

## Key Code Patterns

### HATCH edge-coordinate extraction
```python
for e in msp.query('HATCH'):
    test_points = []
    for path in e.paths:
        if hasattr(path, 'edges'):  # PolyEdgePath
            for edge in path.edges:
                if hasattr(edge, 'start'):
                    test_points.append(edge.start[:2])
                if hasattr(edge, 'end'):
                    test_points.append(edge.end[:2])
                if hasattr(edge, 'center'):
                    test_points.append(edge.center[:2])
        if hasattr(path, 'vertices'):  # PolylinePath
            for v in path.vertices:
                test_points.append((v[0], v[1]))
    # Now test test_points against cloud polygons
```

### Bidirectional label-box matching
```python
# Build kept-text positions
kept_positions = {}
for e in msp.query('TEXT'):
    if e.dxf.handle not in v10_handles:
        kept_positions[e.dxf.handle] = (e.dxf.insert.x, e.dxf.insert.y, e.dxf.text.strip())

# Find label boxes near kept texts
for e in msp.query('POLYLINE'):
    if len(list(e.vertices)) == 5:
        cx = sum(v.dxf.location.x for v in e.vertices) / 5
        cy = sum(v.dxf.location.y for v in e.vertices) / 5
        for h, (tx, ty, txt) in kept_positions.items():
            if abs(cx - tx) < 0.25 and abs(cy - ty) < 0.25:
                preserve_boxes.add(e.dxf.handle)
```

### Ground-reference line detection
```python
# For each kept instrument tag box (POLYLINE n=5), find connecting L-shape POLYLINEs (n=3)
for box_e in msp.query('POLYLINE'):
    if len(list(box_e.vertices)) != 5 or box_e.dxf.handle in deleted_handles:
        continue
    box_xs = [v.dxf.location.x for v in box_e.vertices]
    box_right = max(box_xs)
    
    for poly_e in msp.query('POLYLINE'):
        if len(list(poly_e.vertices)) != 3 or poly_e.dxf.handle in preserved_handles:
            continue
        pts = [(v.dxf.location.x, v.dxf.location.y) for v in poly_e.vertices]
        # Check if first vertex is at box right edge (within 0.05 units)
        if abs(pts[0][0] - box_right) < 0.05 and abs(pts[0][1] - box_e_centroid_y) < 0.5:
            # Check it's short (total length < 1.0 DXF units) and doesn't span across drawing
            total_len = sum(((pts[i+1][0]-pts[i][0])**2 + (pts[i+1][1]-pts[i][1])**2)**0.5 
                          for i in range(len(pts)-1))
            if total_len < 1.0:
                preserve_ground_lines.add(poly_e.dxf.handle)
```