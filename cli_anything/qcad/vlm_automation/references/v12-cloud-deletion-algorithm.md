# V12 Cloud Deletion Algorithm — Complete Specification

## Overview

The V12 algorithm takes a PDF with cloud/strikethrough annotations and a DXF, identifies which DXF entities should be deleted based on cloud containment, and produces a clean DWG with all layers visible.

## Key Rules (V1→V12 lessons)

### 1. Coordinate Mapping
- PDF→DXF mapping is **per-drawing**, not universal. Test all 4 candidate transforms.
- For Pair 1 (landscape 1224×792, `dwg2dxf` origin bottom-left): `swap_xy` (`x_dxf = y_pdf/72, y_dxf = x_pdf/72`) was correct.
- Always verify by checking PIP counts per cloud and confirming with user overlay.

### 2. Entity Types to Test
| Type | Test Method | Notes |
|------|-------------|-------|
| TEXT/MTEXT | Insertion point PIP | Also check insertion + estimated text width for multi-word texts past cloud boundary |
| CIRCLE/ARC | Center point PIP | |
| LINE | Both endpoint PIP (any vertex inside = matched) | |
| POLYLINE | **Vertex-level PIP** — any vertex inside = matched | Circles (n=100), squares (n=5), label boxes, ground lines |
| LWPOLYLINE | **Vertex-level PIP** — any vertex inside = matched | |
| HATCH | **Edge-level test** — test ArcEdge.start/end/center + LineEdge.start/end against cloud polygon | `path.vertices` is EMPTY for PolyEdgePath HATCHes. Must use `path.edges`. HATCH dot count = sum of boundary paths. |
| INSERT | Insertion point PIP | |

### 3. Cloud Containment — Strict Inside Only (Pitfall #93)
- Use `matplotlib.path.Path.contains_point(pt, radius=-0.08)` for **strict inside** testing.
- Entities caught only by expanded PIP (`radius >= 0`) but NOT by strict PIP (`radius < 0`) are **boundary-touching false positives** — do NOT include them.
- Boundary-touching objects (e.g., F194 text on C3 max-y edge, arrow 4067 near C3 boundary) were NOT intended for deletion by the reviewer.

### 4. Exclusion Rules — Must Preserve These Despite Cloud Containment

**Label boxes (Pitfall #82):** POLYLINE n=5 rectangles surrounding kept text labels must be preserved. Bidirectional matching: find POLYLINE boxes near kept TEXTs AND find kept TEXTs near POLYLINE boxes.

**Ground-reference L-shapes (Pitfall #92):** POLYLINE n=3 forming an L-shape (horizontal + vertical segments, total length < 1.0 DXF unit) connecting an instrument tag box to a ground symbol. First vertex at box right edge. Preserve these even if inside cloud polygon.

**Arrow triangles near boundaries (Pitfall #93):** POLYLINE n=4, color=1, small red triangles (total extent < 0.15 units) connecting instrument tags to power labels near cloud boundaries. Preserve if caught only by boundary-expanded PIP.

**Instrument labels inside cloud boundary:** Text labels at x≈5.2 (C0/C2 right boundary) like 101, 102, 104, 105, 106, 108, Tb703 are instrument tag numbers that the reviewer did NOT intend to delete. Preserve them and their label boxes.

**Page-divider lines (Pitfall #77):** LINE entities spanning >80% of page height/width at color=256 (BYLAYER) are structural, not deletion targets. Always exclude.

### 5. Strikethrough Lines
PDF Line annotations inside each cloud group mark the specific entity rows to delete. Map LINE endpoints to DXF using the same coordinate transform as their parent cloud. Find color=1 (red) POLYLINEs/LINEs that pass through or near these endpoints. These are strikethrough marks and should be deleted.

### 6. Content-Based Text Sweep (Pitfall #91)
After geometric matching, search for TEXT entities whose content matches known annotation targets (e.g., "Hydrogen Peroxide", "Tank Level", "Display", "+24V", "0v", "4-20 ma") on the correct side of the drawing, even if their insertion point falls outside cloud boundaries.

### 7. Cloud Classification (Pitfall #87)
After mapping vertices to DXF coordinates, classify clouds by **spatial position**:
- LEFT: x_center < 7
- RIGHT: x_center >= 7
- TOP: y_center > 7
- BOTTOM: y_center <= 7

Do NOT assume cloud order matches visual position. Annotation xrefs are PDF object IDs, not spatial labels.

### 8. Pipeline Steps

1. **Extract** PDF cloud Polygon vertices + Line annotation endpoints + FreeText content
2. **Map** all 4 candidate transforms, count PIP entities per cloud per transform
3. **Verify** with user (send overlay images showing entity positions vs cloud polygons)
4. **Build deletion list** using strict PIP (radius=-0.08) for all entity types
5. **Add HATCH entities** via edge-coordinate bbox testing against cloud polygons
6. **Add content-based text sweep** for known annotation targets outside cloud geometry
7. **Add strikethrough lines** (color=1 POLYLINEs/LINEs crossing through cloud regions)
8. **Apply exclusion rules**: label boxes, ground-reference L-shapes, arrows, instrument labels, page dividers, boundary-touching entities
9. **Delete entities** via `delete_entities_text.py` (text-based DXF editing, preserves structure)
10. **Fix layer colors** via `fix_layer_visibility.py` (negative 62 → positive)
11. **Export DWG** via QCAD Pro headless (`qcad-bin -platform offscreen -autostart script.js`)
12. **Verify**: entity counts, kept texts present, deleted texts absent, all layers ON

### 9. Always Start from Original DXF
Never chain modifications on modified files. Each iteration rebuilds from the original `1.dxf`. Handle references shift after deletion, so the original DXF is the only stable source.

## V1→V12 Version History

| Version | Count | Method | Key Issue |
|---------|-------|--------|-----------|
| v1 | 104 | PIP expanded + line proximity tol=0.8 | 25 false positives |
| v2/v5 | 96 | annot.rect for all clouds | Over-catches thin clouds |
| v3/v6 | 93 | hybrid bbox+polygon | Label boxes wrongly deleted |
| v4/v7 | 92 | hybrid + HATCH + label exclusion | Missing +24V texts, red line |
| v8 | 95 | v7 + explicit text/line additions | C1/C3 on wrong side |
| v9 | 112 | Classified clouds LEFT/RIGHT by polygon position | Wrongly deleted labels + texts |
| v10 | 105 | v9 − 18 labels + 11 HATCH/edge additions | Ground-reference L-shape deleted |
| v11 | 104 | v10 − 1 ground-reference POLYLINE | Boundary-touching arrow+F194 deleted |
| v12 | 102 | v11 − 2 boundary-touching entities | ✅ Final |