# Pair 1 V9→V13 Deletion Pipeline Hardening (2026-05-12 to 05-16)

## Context

Drawing: Pair 1 (PID / instrument loop diagram)  
Source: PDF with 4 cloud annotations (C0, C1, C2, C3) + strikethrough lines + FreeText annotations  
Pipeline: PDF annotation → DXF coordinate mapping → polygon containment → entity deletion → layer fix → QCAD DWG export

## V9 Baseline (112 deletions)

First version with correct cloud coordinate mapping (swap_xy confirmed) and spatial classification:
- C0 = LEFT-TOP (x=1.17–5.10)
- C2 = LEFT-BOTTOM (x=1.39–5.19)
- C1 = RIGHT-TOP (x=9.21–13.63)
- C3 = RIGHT-BOTTOM (x=9.17–13.60)

## V9 User-Reported Issues (5 categories)

1. **HATCH rectangle came back** — HATCH 4B4C (DOTS pattern) at mid-right  
2. **11 white dots came back** — SOLID circle-fill HATCHes on left side  
3. **Red line between F172 and B239** — POLYLINE 4BB8, color=1, inside C1  
4. **"Hydrogen Peroxide Tank Level Display" text** — 3 TEXTs at x≈15.4, past cloud edges  
5. **Wrongly deleted labels** — Tb703, 101, 102, 104, 105, 106, 108 + their label boxes

## V10 Corrections (105 deletions)

**Restored (removed from deletion list):** 18 handles
- 8 TEXTs: Tb703 (×2), 101, 102, 104, 105, 106, 108
- 10 POLYLINE label boxes: 3246, 324D, 3254, 34DA, 36E5, 36EC, 36F3, 406D, 41CC, 4467

**Added (new deletions):** 11 handles
- 7 HATCHes: 4B4C, 48DD, 48DE, 48DF, 4D73, 4B73, 4BA7
- 1 POLYLINE (red strikethrough): 4BB8
- 3 TEXTs: 36E2, 36E3, 36E4

**Root causes and fixes:**

### HATCH Edge-Based Geometry
HATCH entities with `PolyEdgePath` use `edges` (LineEdge/ArcEdge) instead of `vertices`. The `path.vertices` list is EMPTY for edge-based HATCHes.

```python
for e in msp.query('HATCH'):
    test_points = []
    for path in e.paths:
        if hasattr(path, 'edges'):  # PolyEdgePath
            for edge in path.edges:
                if hasattr(edge, 'start'):  test_points.append(edge.start[:2])
                if hasattr(edge, 'end'):    test_points.append(edge.end[:2])
                if hasattr(edge, 'center'): test_points.append(edge.center[:2])
        elif hasattr(path, 'vertices'):  # PolylinePath
            for v in path.vertices:
                test_points.append((v[0], v[1]))
    # Test test_points against cloud polygons
```

### Boundary-Touching Lines
POLYLINE 4BB8 (red strikethrough) had endpoints exactly on C1 polygon boundary. `contains_point()` returns False for boundary points.

**Fix:** Use expanded polygon with small margin OR test endpoints directly:
```python
# Option A: expanded polygon
expanded_poly = MplPath(cloud_verts * 1.02)  # slight outward expansion
if expanded_poly.contains_point(endpoint):
    mark_for_deletion()

# Option B: test all vertices, not just insertion point
for vx, vy in polyline_vertices:
    if any(cloud_poly.contains_point((vx, vy), radius=+0.05) for cloud in clouds):
        mark_for_deletion()
        break
```

### Content-Based Text Sweep
Texts at x≈15.4 were past all cloud boundaries but needed deletion ("Hydrogen Peroxide", "Tank Level", "Display").

**Fix:** After geometric matching, do a content sweep for known deleted-item substrings:
```python
deleted_substrings = ['Hydrogen Peroxide', 'Tank Level', 'Display']
for e in msp.query('TEXT'):
    text = e.dxf.text.strip()
    if any(sub.lower() in text.lower() for sub in deleted_substrings):
        if e.dxf.handle not in deleted_handles:
            content_sweep_deletions.add(e.dxf.handle)
```

### Bidirectional Label-Box Matching
V9 only matched boxes near DELETED texts. When labels were wrongly marked for deletion, their boxes were also deleted.

**Fix:** Match boxes near ALL kept texts:
```python
kept_positions = {}
for e in msp.query('TEXT'):
    if e.dxf.handle not in all_deleted_handles:
        kept_positions[e.dxf.handle] = (e.dxf.insert.x, e.dxf.insert.y)

for e in msp.query('POLYLINE'):
    if len(list(e.vertices)) == 5:  # label box pattern
        cx = sum(v.dxf.location.x for v in e.vertices) / 5
        cy = sum(v.dxf.location.y for v in e.vertices) / 5
        for h, (tx, ty) in kept_positions.items():
            if abs(cx - tx) < 0.25 and abs(cy - ty) < 0.25:
                preserve_boxes.add(e.dxf.handle)
```

## V11 Correction (104 deletions)

User reported: "Only thing I see wrongly deleted is the two short lines on the right side of F174, which should be kept on the drawing. These two lines are representing F174 is wired to electrical ground as voltage reference."

- **Restored:** Handle 4B6E — POLYLINE n=3, L-shape (horizontal 0.22 + vertical 0.15 = 0.37 total)
- **Vertices:** (9.17, 8.35) → (9.39, 8.35) → (9.39, 8.20)
- **Context:** Adjacent to kept instrument label F174 (box handle 406D)

**Root cause:** 4B6E is geometrically inside C1 cloud (strict PIP, radius=-0.08). It's a functional ground-reference wiring symbol, not obsolete markup. Automated containment cannot distinguish wiring symbols from deletion targets.

## V12 Correction (102 deletions)

User reported two boundary-touching false positives:
1. **Handle 4067** — POLYLINE n=4 (triangle), color=1, callout arrow F175 → +24V. Caught by expanded PIP near C3 boundary.
2. **Handle 4152** — TEXT "F194" at (9.458, 4.512), sitting exactly on C3 max-y boundary (y=4.512).

**Root cause:** Both were caught by `contains_point(radius=+0.08)` (expanded outward) but NOT by `contains_point(radius=-0.08)` (contracted inward). They are on or near the cloud boundary, not inside the interior.

**Fix — Strict vs Expanded PIP Classification:**
```python
for e in candidate_entities:
    pt = entity_center(e)
    strict = cloud_poly.contains_point(pt, radius=-0.08)  # genuinely inside
    loose  = cloud_poly.contains_point(pt, radius=+0.08)  # near boundary
    
    if strict:
        delete_list.append(e.handle)
    elif loose:
        review_list.append((e.handle, e.dxftype(), "boundary-touching"))
    else:
        keep_list.append(e.handle)

# Only delete strict_inside. Boundary-touching default to KEEP.
```

## V13 Correction (101 deletions)

User confirmed V12 but V10-V12 still deleted 4B6E in some iterations. Final fix: **explicitly whitelist 4B6E as a ground-reference wiring symbol**.

## Summary Table

| Version | Deletions | Key Change | What Fixed |
|---------|-----------|------------|------------|
| V9 | 112 | Correct cloud mapping | Clouds on correct sides |
| V10 | 105 | -18 wrongly deleted + 11 missed | HATCH edges, boundary lines, content sweep, bidirectional box matching |
| V11 | 104 | Restore 4B6E ground symbol | Wiring symbol inside cloud |
| V12 | 102 | Restore 4067 + 4152 boundary-touching | Strict vs expanded PIP rule |
| V13 | 101 | Final explicit whitelist | Ground symbol permanently protected |

## Production Rules (Hardened)

### Rule 1: HATCH Testing
Always test edge coordinates (LineEdge.start/end, ArcEdge.center) for HATCH entities. Never rely on `path.vertices` alone.

### Rule 2: Strict PIP Only
Only entities passing `contains_point(radius=-0.08)` are unambiguously inside. Boundary-touching entities (caught by expanded PIP but not strict) default to KEEP.

### Rule 3: Content Sweep
After geometric matching, search for text substrings of known deleted items. Geometric position alone may miss text whose insertion point is outside the cloud.

### Rule 4: Bidirectional Box Matching
Label boxes must be protected near ALL kept texts, not just near deleted ones.

### Rule 5: Wiring Symbol Review List
Short POLYLINEs (n≤8, total length < 0.5 units) inside clouds may be wiring symbols. Generate a review list and present to user. Default to KEEP.

### Rule 6: Text Insertion Point Awareness
TEXT entities anchor at their bottom-left. For right-aligned or centered text, the insertion point may be far from the text center. Test text bounding boxes, not just insertion points.

### Rule 7: Layer Color Fix
Always use text-based layer color fix (`fix_layer_visibility.py`). ezdxf `saveas()` crashes on malformed materials table (`AttributeError: 'str' object has no attribute 'dxf'`).

### Rule 8: QCAD Headless Export
- Call `qcad-bin` directly, NOT the `qcad` wrapper (wrapper forces `-platform xcb`)
- Set `LD_LIBRARY_PATH` to QCAD's own directory
- Kill lingering QCAD processes before retry: `pkill -9 -f qcad`

## Related References

- `references/v9-v10-correction-analysis.md` — Detailed HATCH/boundary/content analysis
- `references/v12-boundary-touching-analysis.md` — Strict vs expanded PIP deep dive
- `references/wiring-reference-line-exclusions.md` — Ground symbol detection patterns
- `references/over-deletion-analysis-pair1.md` — VLM bbox vs strict PIP comparison
- `references/v10-v12-iteration-log.md` — Session-by-session iteration log
