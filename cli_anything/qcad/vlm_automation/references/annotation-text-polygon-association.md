# PDF Annotation Text-to-Cloud Association

Technique for mapping FreeText instruction annotations to their nearest geometric cloud/region markers (Polygon, PolyLine, Circle) when both are present on the same PDF page.

## The Problem

PDF markup tools (Bluebeam, Acrobat, AutoCAD PDF export) typically store revision instructions as:
- **FreeText** annotation: contains the instruction text (e.g., "delete clouded objects")
- **Polygon** annotation: the cloud shape drawn around the target region

These are separate objects. If you naïvely search the FreeText's bounding box in DXF space, you get **dozens or hundreds of entities** because the text box is huge. You must use the **cloud polygon's** bounding box instead.

## Algorithm

```python
import math

def associate_text_to_clouds(all_annots):
    """
    all_annots: list of dicts with keys 'idx', 'type', 'text', 'rect', 'cx', 'cy'
    Returns: list of {'text': annot, 'polygon': annot | None}
    """
    polygons = [a for a in all_annots if a["type"] == "Polygon"]
    texts    = [a for a in all_annots if a["type"] == "FreeText" and a["text"].strip()]
    
    def dist(a, b):
        return math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"])
    
    associations = []
    for t in texts:
        if "delete" in t["text"].lower():
            nearest = min(polygons, key=lambda p: dist(t, p)) if polygons else None
            associations.append({"text": t, "polygon": nearest})
    return associations
```

## Centroid Calculation

```python
def annot_centroid(annot_rect):
    x0, y0, x1, y1 = annot_rect
    return ((x0 + x1) / 2, (y0 + y1) / 2)
```

## PDF-to-DXF Coordinate Mapping

```python
def pdf_rect_to_dxf_bbox(rect, scale_x, scale_y, offset_x, offset_y, margin=2.0):
    """rect = (x0,y0,x1,y1) in PDF page coordinates."""
    return (
        rect[0] * scale_x + offset_x - margin,
        rect[1] * scale_y + offset_y - margin,
        rect[2] * scale_x + offset_x + margin,
        rect[3] * scale_y + offset_y + margin,
    )
```

Scale factors are computed from the DXF's actual text/point extents divided by the PDF page dimensions.

## Dry-Run Delete Report

Before executing deletions, generate a report showing exactly which entities would be deleted per cloud:

```python
def dry_run_delete_report(dxf_path, associations, scale_x, scale_y, offset_x, offset_y):
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    
    for assoc in associations:
        t = assoc["text"]
        p = assoc["polygon"]
        if not p:
            continue
        bbox = pdf_rect_to_dxf_bbox(p["rect"], scale_x, scale_y, offset_x, offset_y)
        inside = find_entities_in_bbox(msp, bbox)
        print(f"Text [{t['idx']}] '{t['text'][:40]}' → {len(inside)} entities in cloud:")
        for e in inside:
            print(f"  {e[0]} handle={e[2]} pos=({e[3][0]:.2f},{e[3][1]:.2f}) text='{str(e[1])[:40]}'")
```

## Overlap Detection

Adjacent clouds may share boundary entities. Before deleting, track handles to avoid double-delete errors:

```python
seen_handles = set()
for assoc in associations:
    bbox = pdf_rect_to_dxf_bbox(...)
    inside = find_entities_in_bbox(msp, bbox)
    to_delete = [e for e in inside if e[2] not in seen_handles]
    seen_handles.update(e[2] for e in to_delete)
    # now safe to delete to_delete
```

## Real-World Results (2026-05-08, Pair 1)

| Cloud | FreeText | Entities Found | Overlap? |
|-------|---------|----------------|----------|
| [0] | "delete clouded objects" | 17 | Shares 105, 106, 108 with [11] |
| [11] | "delete clouded objects" | 48 | Large region, overlaps [0] boundary |
| [4] | "delete clouded objects" + "delete" | 7 | Distinct upper-right region |
| [15] | "delete clouded objects" | 15 | Distinct lower-left region |

Without polygon association, searching the FreeText box returned 68–97 entities. With polygon association, the counts drop to 7–48 and are spatially accurate.

## Production Caution: Fixed Pipeline

The dry-run counts above (7–48 entities per cloud) are **envelope counts**, not actual in-cloud counts. The old pipeline (`pair1_execute_v2.py`) with `margin=2.0` cleared **63 of 84 TEXT entities** — catastrophic over-deletion caused by three independent bugs acting together:

| Bug | Impact | Fix |
|-----|--------|-----|
| `margin=2.0` DXF units | Expands a small cloud polygon into a capture box covering **1/4–3/4** of the entire drawing | `margin=0.2` (3.8×–10.4× area reduction) |
| No entity type filter | Catches `LINE`, `LWPOLYLINE`, `CIRCLE`, `ELLIPSE` — geometry leader lines wrongly counted as "targets" | Filter to `('TEXT', 'MTEXT')` only |
| No confidence scoring | Any entity whose center is inside the bbox gets cleared | Geometric proximity + text-overlap score with `min_confidence=0.30` |

### Fixed Algorithm (2026-05-09)

```python
def find_text_entities_in_bbox(msp, bbox, annot_text,
                               entity_types=('TEXT', 'MTEXT'),
                               text_match_bonus=0.5,
                               min_confidence=0.30):
    """
    score = distance_from_center + text_overlap * bonus
    Keep only candidates >= min_confidence, cap at top-3.
    """
    from difflib import SequenceMatcher
    bx0, by0, bx1, by1 = bbox
    bcx, bcy = (bx0 + bx1) / 2, (by0 + by1) / 2
    annot_lower = annot_text.lower()

    matches = []
    for ent in msp:
        if ent.dxftype() not in entity_types:
            continue
        pos = (ent.dxf.insert.x, ent.dxf.insert.y)
        if not (bx0 <= pos[0] <= bx1 and by0 <= pos[1] <= by1):
            continue
        txt = str(getattr(ent.dxf, 'text', getattr(ent, 'text', ''))).strip()
        dx = abs(pos[0] - bcx) / max(bx1 - bx0, 1e-6)
        dy = abs(pos[1] - bcy) / max(by1 - by0, 1e-6)
        dist_score = 1.0 - math.hypot(dx, dy) / math.sqrt(2)
        text_score = SequenceMatcher(None, annot_lower, txt.lower()).ratio()
        confidence = min(1.0, dist_score + text_score * text_match_bonus)
        matches.append({..., "confidence": confidence})

    filtered = [m for m in matches if m["confidence"] >= min_confidence]
    filtered.sort(key=lambda m: m["confidence"], reverse=True)
    return filtered[:3]  # cap per annotation
```

### Pair 1 Results

| Metric | Old (`margin=2.0`) | Fixed (`margin=0.2`) |
|---|---|---|
| Cloud #0 bbox area | 55.6 DXF² | 14.7 DXF² (3.8× smaller) |
| Cloud #4 bbox area | 32.5 DXF² | 3.2 DXF² (10.1× smaller) |
| Entities caught per polygon | 15, 6, 31, 14 = **76 total** | 8, 1, 14, 1 = **24 total** |
| False deletions (non-target text) | **55** | **0** |
| Correct deletions | ~8 wire labels | **8 wire labels** (`F175`, `F176`, `F171`, `C957`, `C975`, `Blk`, two `Tb703`) |
| Texts preserved vs original | 21 of 84 | **76 of 84** |

### Safe Production Thresholds

After implementing the fixes above, **still flag for human review** when:
- Per-cloud matched entities > **10**
- Total matched entities across all clouds > **15%** of modelspace `TEXT`/`MTEXT` count
- Any match has `confidence < 0.30`

Never auto-execute deletions on cloud-bbox-selected entities without generating the per-annot dry-run report and verifying the top matches by handle + text content.

## Pitfalls

1. **FreeText boxes are huge** — Never use the FreeText `rect` for spatial DXF searches. The cloud polygon `rect` is the correct boundary.
2. **Multiple texts per polygon** — In the Pair 1 case, "delete clouded objects" [27] and "delete" [29] both mapped to polygon [4]. Manual review is needed when distance > 200 px.
3. **Polygon association distance threshold** — If `dist(text, polygon) > 300`, the association is likely wrong. Flag for human review.
4. **Degenerate polygons** — Some cloud tools generate PolyLine or Line annotations instead of Polygon. Check for `type in ("Polygon", "PolyLine", "Line")` and filter by non-empty bounding boxes.
