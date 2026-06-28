# V21→V22 Lesson: Spatial Collision Detection for Cloned Callouts

## Problem

During Pair 3 V21 generation, the CA-1452 cable tag was cloned from the T5 position (y≈20.42) to T7 (y≈19.37) using the same x-offset. The resulting tag at (20.875, 19.672) overlapped the existing note TEXT "TO DWG. B-SAR-280-02732" at (20.875, 20.078) — only 0.406 units vertical separation, which is less than the text height (~0.094) plus necessary margin.

This was caught only by visual review (Discord → user feedback), not by the automated pipeline.

## Root Cause

The VLM-CAD pipeline's entity duplication logic currently:
1. Identifies source entities by geometric region
2. Clones them to destination coordinates via raw DXF text manipulation
3. Re-exports to DWG

It does **not** perform a spatial collision check between newly placed entities and existing modelspace texts.

## Fix Applied (V22)

Shifted the entire T7 callout assembly right by +0.5 units:

| Element | V21 x | V22 x |
|---|---|---|
| Bracket (9A8A) | 20.531 | 21.031 |
| Leader start (9A94) | 20.531 | 21.031 |
| Leader end (9A94) | 20.813 | 21.313 |
| CA-1452 text (9A93) | 20.875 | 21.375 |

This clears the overlap with "TO DWG. B-SAR-280-02732" at x=20.875, y=20.078 while preserving the same vertical relationship to the wire at y=19.375.

## Prevention Rule (Pipeline Add)

**Before finalizing clone placement, perform text-collision scan:**

```python
def check_text_collision(dxf_entities, new_text_x, new_text_y, min_v_gap=0.35):
    """Return list of existing TEXT/MTEXT entities within spatial bbox."""
    conflicts = []
    for e in dxf_entities:
        if e.dxftype() in ('TEXT', 'MTEXT'):
            ex, ey = e.dxf.insert.x, e.dxf.insert.y
            if abs(ex - new_text_x) < 1.5 and abs(ey - new_text_y) < 0.6:
                if abs(ey - new_text_y) < min_v_gap:
                    conflicts.append({
                        'handle': e.dxf.handle,
                        'text': e.dxf.text or '',
                        'pos': (ex, ey),
                        'v_dist': abs(ey - new_text_y)
                    })
    return conflicts

# Usage before placement
conflicts = check_text_collision(doc.entities, 20.875, 19.672)
if conflicts:
    print("WARNING: Potential text overlap detected:", conflicts)
    present_options_to_user()
```

## User Preference Signal

When collision is detected, **do not auto-correct**. Present explicit options:

> "Option A: shift entire callout right by +0.5 x to clear note"
> "Option B: shift callout down to sit below '2C SPARE' text"

Spot selected Option A in this session. This pattern (explicit A vs B presentation) should be used for all layout placement choices where visual tradeoffs exist.

## Files

- V21 DXF: `3_cloned_v21.dxf` (243 entities, callout at x=20.53-20.88)
- V22 DXF: `3_cloned_v22.dxf` (243 entities, callout at x=21.03-21.38)
- V21 DWG: `3_FINAL_v21.dwg` (76,560 bytes)
- V22 DWG: `3_FINAL_v22.dwg` (76,561 bytes)
