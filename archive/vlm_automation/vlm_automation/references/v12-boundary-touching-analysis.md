# V12 Boundary-Touching Correction (2026-05-12)

## Problem

V11 (104 deletions) wrongly deleted two entities that were on or touching the cloud polygon boundary but not strictly inside it:

1. **Handle 4152** — `F194` label text at (9.458, 4.512), sitting exactly on C3 cloud's max-y boundary (y_max=4.512)
2. **Handle 4067** — Red arrow POLYLINE n=4 (triangle) between F175 and +24V, at x=[9.16,9.27] y=[4.63,4.69], entirely outside all clouds (above C3's y_max=4.512)

User feedback: *"When the object is just touching the clouds, it is not meant for deletion as the objects sit on the boundary."*

## Root Cause

The cloud-detection pipeline used `matplotlib.path.Path.contains_point(pt, radius=+0.08)` — an expanded (outward) margin that catches entities near the cloud boundary. This was needed to avoid missing entities whose insertion points fall just outside the polygon (e.g., TEXT entities extending rightward from their left-anchored insertion point). But it also creates false positives for entities that merely touch or are near — but definitively outside — the cloud.

## Diagnosis Method

Test all deletion candidates against strict inside-PIP with inward contraction:

```python
from matplotlib.path import Path as MplPath

# For each cloud polygon:
polygon = MplPath(cloud_vertices)

strictly_inside = polygon.contains_point(pt, radius=-0.08)  # inward contraction
expanded_inside = polygon.contains_point(pt, radius=+0.08)    # outward expansion

# Entity classification:
# - strictly_inside=True  → genuinely inside cloud → DELETE
# - strictly_inside=False AND expanded_inside=True → boundary-touching → REVIEW (likely KEEP)
# - strictly_inside=False AND expanded_inside=False → outside → KEEP
```

## V11→V12 Results

| Handle | Type | Position | Strict PIP (-0.08) | Expanded PIP (+0.08) | Action |
|--------|------|----------|--------------------|-----------------------|--------|
| 4152 | TEXT 'F194' | (9.458, 4.512) | False | True (on C3 boundary) | KEEP |
| 4067 | POLYLINE n=4 | x=[9.16,9.27] y=[4.63,4.69] | False | False (outside all) | KEEP |

20 entities in V11's deletion list were NOT strictly inside any cloud. Most were intentional (strikethrough lines, HATCH dots, content-based text sweeps). Only 2 were false positives from expanded-PIP matching.

## Rule

**Entities whose containment depends solely on expanded PIP (radius > 0) should be flagged for review.** Only entities that pass strict inside-PIP (radius < 0) are reliably inside the cloud boundary. Boundary-touching entities are NOT deletion targets — the cloud annotation marks the interior, not the edge.

## Arrow Preservation

Handle 4067 is a POLYLINE n=4 (triangle/arrowhead) connecting F175 to +24V:
- Vertices: (9.27, 4.63), (9.16, 4.66), (9.27, 4.69), (9.27, 4.63)
- This is a callout arrow on the red line between instrument tag F175 and power supply label +24V
- It was caught by the expanded-PIP because its bounding box overlaps slightly with C3's expanded boundary

**Pattern:** Small POLYLINE n=4 triangles (color=1, footprint < 0.2 units) near cloud boundaries are typically callout arrows pointing from instrument tags to power/ground symbols. Preserve them unless they are explicitly inside the cloud interior.