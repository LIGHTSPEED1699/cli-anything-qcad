# DXF Text-Based Editing Patterns

Reference for zero-dependency DXF editing via group-code pattern matching. These patterns are used by `delete_entities_text.py`, `clone_v2_final.py`, and similar scripts in the Pipeline A workflow.

## 1. CRLF Line Endings for AutoCAD TrueView

**Problem:** DXF files written with Unix LF line endings (default Python `open()`) hang AutoCAD TrueView 2020 on open. The parser appears to expect `\r\n` (CRLF) line terminators and enters an infinite loop on bare LF.

**Fix:** Always convert text-edited DXFs to CRLF before delivery to AutoCAD users.

```bash
# Option 1: sed
sed -i 's/$/\r/' output.dxf

# Option 2: unix2dos
unix2dos output.dxf

# Option 3: Python (before write)
with open(path, 'w', newline='\r\n') as f:
    f.write(content)
```

**Verification:** `file output.dxf` should show "ASCII text, with CRLF line terminators" (not "with very long lines").

**When:** All text-edited DXFs shipped to AutoCAD TrueView users.

## 2. Point-in-Polygon Boundary Point Bug

**Problem:** Shapely `polygon.contains_point(point)` returns `False` when the point lies exactly on the polygon boundary. This causes edge-touching entities (strikethrough lines, border rectangles, label boxes) to be missed entirely in deletion lists.

**Seen:** V9 missed a red strikethrough line (handle 4BB8) because its endpoints sat exactly on the C1 cloud polygon edge. The line was inside the cloud but its endpoints were on the boundary.

**Fix:** Use an expanded polygon test or explicit boundary check:

```python
from shapely.geometry import Point, Polygon

# Option A: Margin expansion for broad match
polygon = Polygon(vertices)
broad_polygon = polygon.buffer(0.08)  # slightly larger
if broad_polygon.contains_point(Point(x, y)):
    # Candidate — boundary points now included
    pass

# Option B: Explict endpoint check for LINEs/POLYLINEs
from shapely import prepared
prep = prepared.prep(polygon)
if prep.contains(Point(x1, y1)) or prep.contains(Point(x2, y2)):
    # At least one endpoint is inside or on boundary
    pass
```

**Rule:** For deletion workflows, if `contains_point()` returns `False` for all vertices/insertion points, try a `buffer(0.08)` expanded polygon before concluding the entity is outside the cloud.

## 3. Instrument Label Preservation — Visual Ground Rules

**Problem:** Automated polygon intersection cannot distinguish between:
- A random short line inside a cloud (should be deleted)
- A ground-reference line or connector attached to an instrument label like F174 (should be kept)

**Seen:** V10 deleted two short lines on the right side of F174 (handle unknown). These lines were inside the C0 cloud polygon but represented earth-ground wiring for F174.

**Fix:** When processing instrument labels, do a visual pre-check:

1. Identify all instrument labels in the area (F170–F176, 101–108, Tb703)
2. Before deleting any entity near a label, check if it appears to be a connector, ground symbol, or reference line
3. If the entity is < 0.5 units long and touches a label box or label text, **keep it** pending user confirmation
4. Mark it as "needs-verification" in the handle list rather than adding to deletion set

**Heuristic:** Short LINEs (< 0.3 units) near instrument labels at x < 6.0 are likely ground-reference or connector lines. Do not auto-delete without visual overlay confirmation.

## 4. HATCH Detection via Bounding Box

**Problem:** HATCH entities use `PolyEdgePath` with `edges` (ArcEdge/LineEdge) instead of `vertices`. The `path.vertices` list is empty. Standard point-in-polygon tests on vertices therefore miss all HATCH entities.

**Fix:** For HATCH detection, calculate a bounding box from the edge coordinates and test rectangle-rectangle intersection with the cloud polygon:

```python
# HATCH edge extraction (works for ArcEdge and LineEdge)
bbx1 = min(edge.start.x for edge in path.edges)
bby1 = min(edge.start.y for edge in path.edges)
bbx2 = max(edge.end.x for edge in path.edges)
bby2 = max(edge.end.y for edge in path.edges)

hatch_box = box(bbx1, bby1, bbx2, bby2)
if cloud_polygon.intersects(hatch_box):
    # HATCH is inside or touching cloud
    pass
```

**Pattern match:** HATCH boundary paths with `solid` fill (group code 70 = 1) produce visible dots, circles, or rectangles on the drawing. One HATCH entity may have multiple paths = multiple visible "dots".

## 5. QCAD ODA Export — When NOT to Use

**Problem 1:** QCAD's ODA DWG export silently strips any entity whose handle is outside the original DWG's handle range. Cloned entities with handles > original max are dropped.

**Problem 2:** QCAD ODA export strips the entire BLOCKS section (~76% file size loss). Title-block revision history (REV 00/01/01A) inside BLOCK ATTDEF/ATTRIB is destroyed.

**Workaround:** Deliver DXF directly to AutoCAD users (after CRLF fix). Do not rely on QCAD headless for:
- Entity cloning that introduces new handles
- Title-block revision data preservation
- Any operation requiring BLOCK/ATTDEF data

**Safe use:** QCAD headless is reliable only for DXF→DWG conversion of files where all handles are within the original range and no BLOCK data is needed.

## 6. Empty Text Strings Break TrueView (Pair 3 Clone Lesson)

**Problem:** When text-based DXF cloning copies entities with group code `1` (text content), if the source text is empty or whitespace-only, the cloned DXF will have `1` followed by an empty line. AutoCAD TrueView rejects zero-length text strings and hangs.

**Root cause:** Text-based clone scripts copy group-code blocks verbatim. If a TEXT/ATTRIB entity in the source has `""` or `" "` as its text value, the clone will too. TrueView's R15 parser treats empty group-code-1 values as fatal errors.

**Fix:** After any text-based DXF generation, scan for empty text values and replace with a single space or single dot:

```python
# Post-process: ensure no group code 1 is followed by empty line
fixed_lines = []
for i, line in enumerate(lines):
    fixed_lines.append(line)
    if line.strip() == '1' and i+1 < len(lines) and lines[i+1].strip() == '':
        fixed_lines.append(' ')  # or '.' for visibility
```

**Also applies to:** group codes 2, 3, 4, 6, 7, 9 — any string-valued group code that cannot be empty in the target DWG version.

**Verification:** `grep -c "^  1$" file.dxf` followed by checking next line is not empty.

**Related:** See `references/pair3-clone-failures-and-workarounds.md` for full Pair 3 post-mortem including binary data block removal and handle collision analysis.

## 10. Recover Accidentally Deleted Entities from an Earlier DXF Version

**Problem:** An automated or manual deletion removes entities that should have been kept (e.g. user reports "two short lines on the right side of F174" were wrongly deleted by a cloud polygon sweep). The deletion was applied via text-based handle removal, and an earlier clean DXF is available.

**This is NOT cloning** — recovery uses ORIGINAL coordinates, not offsets. The entities are copied verbatim from an earlier clean DXF and inserted into the current working DXF.

### Steps

1. **Identify the missing entity handles** by comparing the current deletion list against the user's report, or by inspecting the earlier DXF directly.
2. **Extract raw entity blocks from the source DXF** using regex on group code 5:
   ```python
   pattern = re.compile(r'  0\n(\w+)\n(.*?)\n  5\n' + handle + r'\n', re.MULTILINE | re.DOTALL)
   ```
3. **Find an insertion landmark in the target DXF** — a nearby entity that still exists, or a known y-level block boundary.
4. **Insert the extracted block before the next `0 ENDSEC` or after a known entity terminator** in the ENTITIES section.
5. **Guard against structural corruption:**
   - Ensure no duplicate `0` lines appear between the inserted block and the following entity (causes `Invalid group code "ARC"` parse errors at load time).
   - Verify the target DXF's handle range doesn't collide with the inserted handles (reuse original handles if they don't conflict; remap if they do).
   - For modelspace entities, ensure group code 330 (owner) points to the modelspace dictionary handle.

### Example — V19 T7 Wire Restoration

In Pair 3, stray ARC `9A83` was present in V18 but should not have been there. Simultaneously, T7 wire entities (`9A84` ARC, `9A85` LINE, `9A89` LINE, `9A8A` LWPOLYLINE) that existed in V17 were missing in V18.

- Deleted stray ARC `9A83` from V18 via text-based handle deletion.
- Extracted each T7 wire entity block from `3_cloned_v17.dxf` using raw line scanning.
- Inserted the four blocks after ARC `9A82` in V18.
- **Discovered duplicate `0` line** at the boundary between inserted LINE `9A89` and ARC `9A86` — removed it, changing line count from 64541→64540 and allowing ezdxf parse.
- Result: `3_cloned_v19.dxf` loaded in ezdxf with all expected entities present.

### Why This Matters

When entities exist in an earlier version but are missing in the current version due to accidental deletion, the cleanest fix is often NOT to redo the entire pipeline, but to **surgically extract and re-insert the specific entity blocks** from the earlier version into the current DXF, then re-export.

---

## 11. Title Block Revision Data Lives in BLOCK ATTDEF Defaults, Not Modelspace ATTRIBs

**Problem:** The user asks to update revision history (e.g. change "01" to "02", add row "01A / 2026/05/04 / IFR") in a title block. Running ezdxf query on the modelspace INSERT shows only one ATTRIB instance (e.g. tag=`PIPELINE_SYSTEM`), not revision fields.

**Root Cause:** Title block data is often stored as **ATTDEF default text values inside the BLOCK definition**, not as attached ATTRIB instances in modelspace. Example from PLAINS-D-CAN block:

```
BLOCK
  2
PLAINS-D-CAN
...
ATTDEF
  ...
  2
Revision          ← tag name
  3
A                 ← default text value (what AutoCADshows if no ATTRIB override)
...
ENDSEC
```

The INSERT in modelspace has `group code 66 = 1` (ATTRIBs follow), but the actual ATTRIB entities may NOT exist in the DXF — AutoCAD creates them dynamically when the block is inserted. Some DXF writers include ATTRIBs, but QCAD ODA export strips them.

### Implications

- Text-based editing of revision data requires modifying the **BLOCKS** section, not the ENTITIES section.
- If the workflow already did QCAD ODA export (which strips BLOCK definitions), the revision data is **permanently lost** from the exported DWG. Only the original DWG retains it.
- The VLM-CAD pipeline (which operates at ENTITIES level and exports via QCAD ODA) **cannot preserve or edit BLOCK-level ATTDEF defaults** because QCAD strips BLOCKS.

### Mitigation

- For revision edits, work directly on the **original DWG** using a DWG-native editor (AutoCAD, QCAD GUI with "Edit Block Definition"), not the QCAD-exported version.
- If programmatic DXF editing is required, parse the BLOCKS section directly using regex/text search for ATTDEF entries with tag names "Revision", "Line 1-8 Revision", etc., and modify their default text (group code 3), then convert back using LibreDWG `dxf2dwg` (not QCAD ODA).

---

### V21 Pattern: Coordinate-Shift Recovery (Cloned / Relocated Entities)

When recovering an entity that must appear at a **different position** than its original source location (e.g., a cable callout shifted from T5 to T7, or a symbol moved to wire end):

**Extract raw block from source DXF → apply dx/dy coordinate shift via string replacement → assign free handle → insert before ENTITIES ENDSEC**

This is **NOT cloning with dy offset** (which replicates many entities) — it is surgical single-entity relocation using raw text manipulation.

```python
# Shift all x-coordinates in a raw DXF text block by +1.4
import re
shifted = re.sub(
    r'( 10\n)([0-9.]+)', lambda m: f'{m.group(1)}{float(m.group(2))+1.4:.6f}',
    raw_block, flags=re.MULTILINE
)
# Repeat for group codes 11, 13, 14, etc. as needed for each entity type.
```

**Critical trap:** Inserting after `THUMBNAILIMAGE ENDSEC` instead of before `ENTITIES ENDSEC` orphans the entity. Always verify insertion point is inside the ENTITIES section by checking for preceding entity-type group codes (`ARC`, `LINE`, `INSERT`), not section names.

Before shipping any text-edited DXF:
- [ ] Convert to CRLF if recipient uses AutoCAD TrueView
- [ ] Run `contains_point` + `buffer(0.08)` for boundary-touching lines
- [ ] Screen short LINEs near instrument labels for ground/connector lines
- [ ] Test HATCH bounding boxes, not vertex points, for dot/rectangle fills
- [ ] If QCAD ODA export is used, verify entity counts match expected
- [ ] **Scan for empty string values after any clone operation (group codes 1, 2, 3, 4, 6, 7, 9)**
- [ ] **Ensure Z coordinate remains 0.0 for 2D entities after any coordinate transformation**
- [ ] **Verify handle range is BELOW the original DXF's maximum handle before QCAD ODA export**
- [ ] **When recovering accidentally deleted entities, extract from an earlier clean DXF and check for duplicate `0` separators after insertion**
- [ ] **If updating revision history in title blocks, confirm whether ATTDEF defaults live in BLOCKS section before doing QCAD ODA export**

---

## 7. Safe Handle Range for Cloned Entities (V3 Fix)

**Problem:** QCAD's ODA DWG export silently drops any entity whose handle exceeds the original DWG's maximum handle. In V2, 39 clones with handles `0x9C00`–`0x9C26` (above original max `0x9B3D`) were all discarded on DWG export.

**Evidence:**
- V2c round-trip max handle = `0x9B3D` (same as original)
- All 39 clones vanished; only LWPOLYLINE (`CA-1452`) survived reassignment
- V3 fixed by using handles `0x5458`–`0x547E` (21592–21630), well below `0x9B3D`

**Fix:**
1. Scan the original DXF for gaps in the handle sequence (where deletion targets have already freed slots)
2. Reuse freed handles, or invent new ones only below the original max
3. For a fresh DXF with no deletions: use `0x0100`–`0x0200` (safe under any realistic original max)
4. Always verify: `max_original_handle = max(all handles in ENTITIES + OBJECTS sections)`

```python
import re
handles = set()
for m in re.finditer(r'^  5\n([0-9A-Fa-f]+)$', content, re.MULTILINE):
    handles.add(int(m.group(1), 16))
max_handle = max(handles)
gap_start = 0x5458  # or any safe range below max_handle
```

## 8. Z Coordinate in DXF Cloning (V3 Fix)

**Problem:** When applying a vertical offset (`dy`) to cloned entities, naively adding `dy` to ALL Y-like coordinates corrupts the entity's Z elevation (group code 30). Entities with non-zero Z are dropped or clipped by QCAD ODA export because the source drawing is 2D with Z=0.

**Fix:** Apply `dy` ONLY to Y position codes (20, 42), NEVER to Z elevation (30). The clone must remain at Z=0.

```python
# Wrong (V2 bug)
data = data.replace(f"\n 30\n{z_old:.6f}\n", f"\n 30\n{z_old + dy:.6f}\n")

# Right (V3 fix)
# Group code 30 (Z elevation) must stay 0.0
# Group code 20 (Y position) gets the dy shift
# Group code 42 (arc bulge, if present) may or may not get dy depending on convention
```

**Also applies to:** Any 2D drawing where Z represents elevation. If the entity is a 3D entity (e.g. 3DFACE, S), treat group codes differently.

## 9. Consecutive Zero Lines — DXF Section Terminators

**Problem:** DXF section terminators are pairs of `  0\nSECTION` and `  0\nENDSEC`. The `ENTITIES` section itself ends with `  0\nENDSEC` preceded by the last entity's terminating `  0`. Removing or adding one extra `0  ` line breaks the section boundary, causing TrueView to hang.

**Evidence:** V2 had 160 consecutive ` 0` lines (broken); original had 159 (valid). TrueView hung on the 160-line version but loaded the 159-line version.

**Fix:** When inserting cloned entities, append them BEFORE the `ENDSEC` terminator, not AT it. Preserve the exact sequence: `last_entity_end` → `new_clones` → `  0\nENDSEC`.

```python
# Find the ENTITIES section end marker
endsec_marker = "  0\nENDSEC\n"
idx = content.find(endsec_marker)
assert content[idx-3:idx] == "  0\n", "Must have trailing entity terminator before ENDSEC"
# Insert clones right before ENDSEC
new_content = content[:idx] + clone_blocks + content[idx:]
# Verify no double-zero pattern
assert "  0\n  0\n" not in new_content, "Double zero detected — section boundary broken"
```
