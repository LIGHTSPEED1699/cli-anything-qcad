# Entity Duplication Pipeline (QCAD ECMAScript)

Proven pattern for cloning DXF/DWG entities with spatial offset and text transformation via QCAD Pro headless ECMAScript. Used when raw DXF byte insertion is impossible (Pitfall #100) and ezdxf cannot save (Pitfall #65).

## When to Use

- User wants to **copy existing entities** (wires, labels, blocks, arcs, curves) to new positions
- Text labels need **renaming** while being duplicated (e.g., PLC21→PLC22)
- Raw DXF byte insertion corrupts the file (Pitfall #100) and ezdxf cannot save (Pitfall #65)
- **All entity types** in a region must be copied, not just lines and text (cloud-based selection)

## Architecture Variants

### Variant A: Y-Level Heuristic (Simple, brittle)
Match entities by hard-coded y-coordinate ranges. Works only for perfectly horizontal rows.

### Variant B: Bounding-Box Intersection (Robust)
Match any entity whose bounding box intersects a source region `[xmin, xmax] × [ymin, ymax]`. Catches LINE, ARC, INSERT, LWPOLYLINE, TEXT, etc. without per-type guessing.

### Variant C: Cloud-Based Polygon Selection (User-directed)
Load a Polygon annotation from a cloud markup PDF, map vertices to DXF coordinates, and clone **all** entities whose bounding box intersects the polygon. Guarantees no entity is missed.

## Prerequisites

1. **QCAD Pro 3.32.x** with headless ECMAScript support
2. **Layer unlocking** BEFORE entity addition (Pitfall #103)
3. **Entity iteration** via `doc.queryAllEntities()` + `doc.queryEntity(id)`
4. **Per-type entity construction:** `RLineEntity`, `RArcEntity`, `RTextEntity`, `RPolylineEntity`, `RBlockReferenceEntity`

## Entity Type Support Table

| Type | Code | Clonable via | Notes |
|------|------|-------------|-------|
| LINE | 18 | `new RLineEntity(doc, new RLineData(start, end))` | Straightforward |
| ARC | 22 | `new RArcEntity(doc, new RArcData(center, r, startAng, endAng, reversed))` | Offset center Y only; angles unchanged |
| TEXT | 28 | `new RTextEntity(doc, new RTextData())` | Must set all fields manually; no `.clone()` |
| LWPOLYLINE | 15 | Clone `data.clone()`, iterate vertices, offset Y | Closed shapes (boxes) clone correctly |
| INSERT (BlockRef) | 13 | `new RBlockReferenceEntity(doc, data.clone())` | Set new position; block name preserved automatically |
| DIMENSION | 21 | Not yet implemented | Rarely needed for duplication |

## Proven Script: Bounding-Box Intersection (V6 Pattern)

Clones **all** entity types whose bounding box intersects a source region, then applies `Y_OFFSET`:

```javascript
var SRC_X_MIN = 13.0, SRC_X_MAX = 22.0;
var SRC_Y_MIN = 19.0, SRC_Y_MAX = 21.0;
var Y_OFFSET = -0.75;
var TEXT_RENAMES = { /* ... */ };

// ... import file, unlock layers ...

var allIds = doc.queryAllEntities();
var sourceIds = [];
for (var i = 0; i < allIds.length; i++) {
    var e = doc.queryEntity(allIds[i]);
    if (!e) continue;
    var bbox = e.getBoundingBox();
    var bmin = bbox.getMinimum();
    var bmax = bbox.getMaximum();
    if (bmax.getX() >= SRC_X_MIN && bmin.getX() <= SRC_X_MAX &&
        bmax.getY() >= SRC_Y_MIN && bmin.getY() <= SRC_Y_MAX) {
        sourceIds.push(allIds[i]);
    }
}

// Clone per type
for (var j = 0; j < sourceIds.length; j++) {
    var src = doc.queryEntity(sourceIds[j]);
    var typeNum = src.getType();
    var newEnt = null;
    if (typeNum === 18) { /* LINE */ }
    else if (typeNum === 22) { /* ARC */ }
    else if (typeNum === 28) { /* TEXT with rename */ }
    else if (typeNum === 15) { /* LWPOLYLINE */ }
    else if (typeNum === 13) { /* INSERT */ }
    if (newEnt) {
        newEnt.setLayerName(src.getLayerName());
        addOp.addObject(newEnt);
    }
}
```

## Cloud-Based Polygon Selection

### Coordinate Mapping Issue (Discovered 2026-05-13)

When using `3_cloud.pdf` markup on Pair 3, extracted polygon vertices mapped to **y ≈ 9.3–10.0** in DXF space, while the actual terminal entities reside at **y ≈ 19–20**. This indicates:
- The cloud PDF may be a **different page rendering** (cropped, scaled, or different orientation) than the source DXF
- Or the PDF coordinate system has an additional transformation not captured by the standard `swap_xy` mapping

**Resolution (2026-05-13):** All 4 standard mapping variants produced cloud bounds at x≈5–10, y≈7–11 — far from the actual terminals at x≈18–21, y≈19–20. The cloud PDF appears to have been drawn on a zoomed/cropped view or has a different `MediaBox`/`CropBox` than the base PDF. **When cloud coordinates don't match expected source regions after testing all transforms, proceed without the cloud** (use hard-coded source regions from known entity coordinates) and ask the user for a properly scaled cloud PDF. See `references/cloud-pdf-coordinate-mismatch.md` for full analysis, raw data, and troubleshooting flow.

### Pipeline
1. Extract Polygon vertices from cloud PDF (`fitz`/`pymupdf`)
2. Map to DXF coordinates using verified transformation (see [pdf-cloud-vertex-extraction.md](pdf-cloud-vertex-extraction.md))
3. Build polygon object in QCAD script
4. Test each entity's bounding-box center for point-in-polygon containment
5. Clone matching entities with offset and text renames

## Pitfalls Specific to Duplication

### API Methods That Do NOT Exist

| What you might try | Error | Correct approach |
|-------------------|-------|----------------|
| `data.clone()` | `TypeError: Property 'clone' of object RTextData [JS] is not a function` | Create `new RTextData()` and set each field manually |
| `entity.setData(newData)` | `TypeError: Property 'setData' of object RLineEntity [JS] is not a function` | Create `new RLineEntity(doc, newData)` from scratch |
| `vector.add(dx, dy)` | `TypeError: Property 'add' of object RVector [JS] is not a function` | `new RVector(x + dx, y + dy)` |
| `qcad.quit(0)` | `ReferenceError: qcad is not defined` | `QCoreApplication.quit(0)` (script scope) or simply let script end |

### INSERT Block Cloning
Block references (`RBlockReferenceEntity`) clone via `data.clone()` then `bd.setPosition(newPos)`. The block name and scale are preserved automatically. Do NOT attempt to reconstruct the block definition — just move the reference.

### Text clone with rename: match by EXACT string
The filter `TEXT_RENAMES.hasOwnProperty(origText)` ensures only named labels are cloned. Broad y-proximity filters catch extra texts like `(B)`, `(GND)`, `2C SPARE` — avoid unless explicitly requested.

### Layer locking causes silent entity skip
If you see `RTransaction::addObject: entity not editable` warnings, the entity was NOT added despite the transaction reporting success. Always run the layer unlock loop before `RAddObjectsOperation`.

### Color inheritance
Cloned entities should inherit source color via `entity.getColor()`. For BYLAYER yellow, ensure the target layer has color 2 (yellow) and set `newEnt.setColor(new RColor(256))` for BYLAYER, or copy the source's explicit color.

## Variant D: Python Text-Based Bulk Clone (Proven 2026-05-13)

When QCAD ECMAScript is unreliable (cloud coordinate mismatch, API gaps, layer locking issues) and ezdxf cannot save (Pitfall #65), a **pure Python text-based clone** is the fallback of last resort.

### How It Works

1. **Read source DXF as bytes** and parse with `ezdxf` (read-only) to identify entity handles in the source region
2. **Extract raw entity blocks** by handle search (`\r\n  5\r\nHANDLE\r\n`) in the ENTITIES section
3. **Clone each block** with modifications:
   - Strip group code 5 (handle) — QCAD ODA reassigns on import
   - Strip group code 330 (owner handle) — prevents cross-reference corruption
   - Apply coordinate offset (e.g., `dy = -1.25`) via regex on group codes 10/20/30
   - Apply text replacements (e.g., `PLC21 → PLC22`) via `bytes.replace`
4. **Insert cloned blocks before `ENDSEC`** in ENTITIES section (safe insertion point)
5. **Remove duplicate handles** — if any original entity handles were accidentally preserved, strip them
6. **Fix layer colors** via `fix_layer_visibility.py` (Pitfall #55)
7. **Export via proven QCAD script** (`qcad_convert_v9_simple.js`)

### Key Code Pattern

```python
import re

# 1. Find insertion point (before ENDSEC in ENTITIES)
ent_start = raw.find(b'\r\n  0\r\nSECTION\r\n\r\n  2\r\nENTITIES\r\n')
ent_end = raw.find(b'\r\n  0\r\nENDSEC\r\n', ent_start)
insert_at = raw.rfind(b'\r\n  0\r\n', ent_start, ent_end)  # last entity start

# 2. Clone each source entity
new_blocks = []
for h in source_handles:
    hpattern = f'\r\n  5\r\n{h}\r\n'.encode()
    hpos = raw.find(hpattern, ent_start, ent_end)
    estart = raw.rfind(b'\r\n  0\r\n', ent_start, hpos)
    eend = raw.find(b'\r\n  0\r\n', hpos, ent_end)
    block = raw[estart:eend]
    
    # Strip handle & owner
    block = re.sub(rb'\r\n  5\r\n[0-9A-Fa-f]+\r\n', b'', block)
    block = re.sub(rb'\r\n330\r\n[0-9A-Fa-f]+\r\n', b'', block)
    
    # Offset Y coordinates (group code 20)
    block = re.sub(
        rb'(\r\n 20\r\n)([\d.]+)(\r\n)',
        lambda m: m.group(1) + f"{float(m.group(2)) + dy:.5f}".encode() + m.group(3),
        block
    )
    
    # Text replacement
    block = block.replace(b'PLC21', b'PLC22')
    
    new_blocks.append(block)

# 3. Insert
raw[insert_at:insert_at] = b''.join(new_blocks)
```

### Terminal Label Duplication Pitfall (Pair 3 V1)

When cloning by y-level or bounding box, **terminal row labels** like `(2)`, `(3)`, `(4)` that sit in the source region get cloned along with functional content. At the destination, they produce **duplicate or misnumbered labels** (e.g., terminal row 7 now has both `(7)` from original design AND `(2)` from clone).

**Mitigation:**
1. **Pre-clone exclusion list:** Identify label TEXT entities by regex `^\(\d+\)$` or `^Terminal \d+$` and exclude from `source_handles`
2. **Post-clone cleanup:** After cloning, scan destination region for label-like TEXTs and remove duplicates
3. **Visual confirmation:** In overlay PNG, render cloned labels in orange so user can flag them

**Discovered 2026-05-13:** Pair 3 V1 cloned `(2)` and `(3)` labels; user must verify if these appear as duplicates at terminals 7/8/9.

### Duplicate Handle Cleanup

After bulk clone, verify no duplicate handles exist:

```python
handles = re.findall(rb'\r\n  5\r\n([0-9A-Fa-f]+)\r\n', raw)
dupes = [h for h in set(handles) if handles.count(h) > 1]
assert len(dupes) == 0, f"Duplicate handles: {dupes}"
```

If duplicates exist, strip ALL group-code-5 values from cloned blocks (QCAD ODA will reassign fresh handles on import).

### Pair 3 V1 Rejections & V2 Corrective Spec

For a detailed post-mortem of a real V1 rejection (wrong clone range, missing cable tag, title-block corruption, 55 KB data loss) and the resulting V2 corrective spec, see `references/pair3-duplication-rejections-v1.md`.

## Verified Capabilities

| Date | Variant | Entities Cloned | Output | Notes |
|------|---------|----------------|--------|-------|
| 2026-05-12 | Y-level heuristic | 5 LINE + 3 TEXT | 72.7 KB DWG | Simple, brittle |
| 2026-05-13 | Bounding-box | 88 candidates found | — | Export failed (debugging) |
| 2026-05-13 | Cloud polygon | — | — | Coordinate mismatch (see `pair3-cloud-coordinate-mismatch.md`) |
| 2026-05-13 | **Python text-based** | **51 entities** | **78.4 KB DWG** | **Proven for Pair 3 V1; all entity types cloned, text replaced, layers fixed** |

## Related Pitfalls in Main Skill

- Pitfall #100 — Raw DXF byte insertion corrupts (why ECMAScript is needed)
- Pitfall #101 — No `.clone()` or `.setData()` on QCAD data objects
- Pitfall #102 — `RVector.add()` does not exist
- Pitfall #103 — Layer unlocking prerequisite
- Pitfall #65 — ezdxf `saveas()` crashes on LibreDWG DXFs
- Pitfall #37 — `qcad-bin` headless launch pattern
- Pitfall #92 — Ground-reference L-shapes must be preserved (even when inside clone region)