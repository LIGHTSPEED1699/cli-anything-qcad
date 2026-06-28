# Pair 3 V23–V24: Visual Collision Detection & Overlap Prevention

## Context

After cloning the CA-1452 cable tag callout from the T5 reference position to the T7 wire end, the new TEXT entity (CA-1452) and its leader LINE were positioned at coordinates derived from the source callout plus a spatial offset. However, a pre-existing TO DWG note existed nearby. Visually, the two text strings overlapped in the exported DWG preview.

## Problem

Naive coordinate-based positioning (e.g., "place at x=20.75") does not account for:

1. **Text width**: A 6-character label like "CA-1452" at text-height 0.125 is ~0.45 DXF units wide with standard font metrics.
2. **Font metrics**: Descenders and ascenders extend beyond the insertion point bounding box.
3. **Block/LEADER geometry**: A leader line adds visual extent that simple insertion-point checks miss.
4. **Existing nearby geometry**: A note like "TO DWG TITLE-BLOCK FOR CABLE GAUGE" at x≈20.71 had its own width extending leftward.

The V23 first attempt (shift +0.5 from the original clone position) eliminated most overlap but left a ~0.004-unit residual collision between the "T" of "TO DWG" and the "C" of "CA-1452". This was below visual tolerance.

## Solution: Font-Aware Bounding-Box Overlap Detection

### Step 1 — Compute 2D AABB for Every Entity in the Region

| Entity Type | Bounding-Box Rule |
|---|---|
| `TEXT` | `[insert_x - pad, insert_y - th*0.2, insert_x + len(text)*th*0.6*xscale, insert_y + th*1.2]` |
| `MTEXT` | `[insert_x, insert_y - line_count*th*1.5, insert_x + width, insert_y]` |
| `LWPOLYLINE` | `[min(verts.x), min(verts.y), max(verts.x), max(verts.y)]` |
| `LINE` | `[min(start.x, end.x), min(start.y, end.y), max(start.x, end.x), max(start.y, end.y)]` |
| `INSERT` | `[insert_x, insert_y, insert_x + block_width*sx, insert_y + block_height*sy]` (approx) |
| `ARC` | `[center.x - radius, center.y - radius, center.x + radius, center.y + radius]` |

The TEXT width factor `0.6` is a conservative monospace-ish approximation. For proportional fonts, the actual width varies by character ("I" vs "W"), but `0.6` works as a safe upper bound for collision detection.

### Step 2 — Filter to Region of Interest

Instead of checking the entire drawing (performance), filter to the spatial region where the shift occurred:

```python
region = (18.0, 18.5, 22.0, 22.0)  # minx, miny, maxx, maxy
entities = [e for e in msp.query('TEXT MTEXT INSERT LWPOLYLINE LINE ARC')
            if bbox(e) and bbox_intersects_region(bbox(e), region)]
```

### Step 3 — Pairwise Overlap Check with Margin

```python
def overlaps(a, b, margin=0.02):
    """a, b are (minx, miny, maxx, maxy) tuples."""
    return not (a[2] < b[0] - margin or a[0] > b[2] + margin or
                a[3] < b[1] - margin or a[1] > b[3] + margin)
```

The `margin` parameter (default 0.02 DXF units ≈ 0.5 mm at typical drawing scale) provides visual breathing room. If two boxes are separated by less than this, they are reported as colliding.

### Step 4 — Iterative Shift & Re-Check

If overlaps are found, shift the entire related entity group (callout text, leader line, bracket, wire-end symbols, and any dependent notes) by a uniform Δx or Δy. Re-run the checker. Repeat until `len(collisions) == 0`.

For Pair 3, the wire-end assembly was shifted **+0.5** in X (from V21) to get V23. Zero overlaps were found after an additional **+0.5** shift (total +1.0 from V21), yielding V24.

## Pitfalls

1. **Insertion-point-only checks are insufficient.** A TEXT entity's insertion point is its bottom-left corner. Its actual footprint extends rightward and upward. Never check `insert_x == other_x` as a collision test.
2. **Fixed-width assumptions fail on narrow/wide characters.** The `0.6` factor is a compromise. For multi-line text or mixed-case strings, consider using `0.65` or measuring with `ezdxf.fonts` if available.
3. **Block INSERT extents need block definition lookup.** The script uses approximate block scaling. For exact geometry, resolve the block definition and compute its bounding box from constituent entities.
4. **Margin must be context-appropriate.** 0.02 works for text-text overlap. For text-line or text-hatch overlap, a larger margin (0.05) may be needed to account for visual weight.
5. **Do not skip re-check after export.** The collision detector runs on DXF. After DWG export, layer scaling or font substitution might alter text extents. A final visual inspection of the DWG preview is still required.
6. **Shift entire assembly, not individual entities.** When relocating a callout group attached to a wire, extend the wire AND shift all dependent symbols (WFEND, WECOIL, bracket, leader, tag, notes) by the same Δx. Shifting one entity and not others causes the tag to float off the wire. See `references/pair3-v19-v24-iteration-log.md`.
7. **Margin ≥ 0.05 for text-to-text.** The V21→V22 micro-overlap (0.004 units) was below visual tolerance but still annoyed the user. Use a minimum margin of 0.05 DXF units (≈ 1.3 mm) for all text-to-text checks.

## Reusable Script

See `scripts/check_text_collision.py` in this skill directory for a standalone CLI tool implementing the above algorithm.

## Command Example

```bash
python scripts/check_text_collision.py 3_cloned_v24.dxf --margin 0.02 --region 18,18.5,22,22
```

## Related

- `references/recover-deleted-entities.md` — If collision repair requires deleting and re-inserting entities.
- `references/pair1-completion-status.md` — Pair 1 never required collision detection because deletions only removed entities.
