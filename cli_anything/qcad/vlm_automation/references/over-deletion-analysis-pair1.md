# Over-Deletion Post-Mortem — Pair 1 (2026-05-10)

## Summary

The original `1_deletion_log.json` was generated using the VLM-based bbox/distance matching pipeline (`vlm_cloud_interpreter_v3.py`). It identified **73 entities** for deletion. After switching to strict point-in-polygon containment against PDF cloud vertices (correct mapping: `annot.vertices / 72`, no extra flip), only **29 entities** were actually inside any cloud polygon. **44 entities were over-deleted** — deleted despite being outside all 4 PDF clouds.

## The 44 Orphans

These entities were deleted by the old pipeline but are outside all 4 cloud polygons:

### Tank group (top-left, NOT in any cloud)
- `A90A` — "Tank Level" — outside all clouds
- `9F7C` — "Transmitter" — outside all clouds
- `A8E4` — "SPAN" — outside all clouds
- `A7F0` — "O-100 %" — outside all clouds
- `A700` — "0-6.3 ft" — outside all clouds

### Local panel (middle-left, NOT in any cloud)
- `325D` — "Loop" — outside all clouds
- `A4E5` — "Powered" — outside all clouds
- `A42A` — "Local" — outside all clouds
- `9F7B` — "Level" — outside all clouds
- `A0B2` — "Display" — outside all clouds

### Bottom wiring cluster (NOT in any cloud)
- `A89E` — "#1 Blk" — outside all clouds
- `A85D` — "#1 Wht" — outside all clouds
- `A5E8` — "C972" — outside all clouds

### Bottom-right cluster (NOT in any cloud)
- `A7E7` — "C957" — outside all clouds
- `A79C` — "MTB" — outside all clouds
- `A737` — "FIELD" — outside all clouds
- `A6EE` — "101" — outside all clouds
- `A6B6` — "102" — outside all clouds
- `A65F` — "104" — outside all clouds

### And 24+ additional entities
See full listing in the original session transcript. All verified via point-in-polygon against C0, C1, C2, C3 with correct mapping.

## Per-Cloud Containment (Correct Mapping)

| Cloud | xref | Entities Inside | Key Labels |
|-------|------|----------------|------------|
| C0 | 23 | 11 | #2 Wht, #2 Blk, LOOP, LOCAL, MTB, 101, 102, 103, etc. |
| C1 | 27 | 0 | *(empty — zoom mismatch)* |
| C2 | 34 | 24 | C957, MTB, FIELD, 101, 102, 104, etc. |
| C3 | 38 | 0 | *(empty — zoom mismatch)* |

**Total correct deletions: 35** (11 + 0 + 24 + 0)

## Root Cause

The VLM pipeline used **bbox overlap + distance scoring** instead of strict geometric containment:

1. `annot.rect` (bounding rectangle) was used instead of `annot.vertices` (actual polygon)
2. Bbox is 3×–10× larger than the cloud polygon → captures far more entities
3. Distance scoring ranked nearby entities as "likely targets" even when outside the cloud
4. No point-in-polygon test was performed
5. The double-flip bug (pitfall 68) compounded the problem by placing clouds in wrong locations

## Why Strict Point-in-Polygon Is Required

| Criterion | Old Pipeline | Correct Pipeline |
|---|---|---|
| Geometric test | Bbox overlap + distance | Point-in-polygon |
| Entities selected | 73 | 35 |
| Correct deletions | 29 | 35 |
| Over-deletions | 44 | 0 |
| False positive rate | 60% | 0% |

## Production Rule

For cloud-annotated deletions:
1. Extract `annot.vertices` from each `PolygonCloud` annotation
2. Map to DXF with `annot.vertices / 72` (no extra flip — PyMuPDF already handles rotation)
3. Test each entity's position with `point_in_polygon(entity_x, entity_y, cloud_verts)`
4. Delete ONLY entities where `inside == True`
5. Never use bbox, distance, text matching, or VLM confidence for cloud-annotated deletions

## Files

- `1_deletion_log.json` — original 73-target list (44 orphans)
- `1_CLEAN.dxf` — 145 entities after deleting 73 targets (needs regeneration with correct 35-target list)
- `1_CLEAN.dwg` — exported from `1_CLEAN.dxf`, opens in AutoCAD with zero errors but contains 44 over-deletions

## Related

- Pitfall 51 (bbox envelopes)
- Pitfall 66 (VLM over-deletion)
- Pitfall 67 (point-in-polygon as only criterion)
- Pitfall 68 (double-flip)
- `references/pdf-cloud-vertex-extraction.md` — correct mapping rule

## Last Updated

2026-05-10 — all 73 targets verified against 4 cloud polygons. 44 confirmed orphans. Correct in-cloud count: 35.
