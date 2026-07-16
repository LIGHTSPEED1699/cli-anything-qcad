# Recovering Accidentally Deleted Entities from an Earlier DXF

## When This Applies

An automated deletion (cloud polygon sweep, handle list bulk removal, etc.) removes entities that should have been kept. The user reports the missing items. The current working DXF has already progressed past the bad version, but an earlier clean DXF still contains the missing entities.

## The Fix: Surgical Re-Insertion

This is NOT cloning — it uses **original coordinates**, not offsets. Entities are copied verbatim from the earlier clean DXF and inserted into the current working DXF at the correct location.

## Step-by-Step

### 1. Identify Missing Entity Handles

From the user's description or by comparing deletion lists between versions, determine which entity handles need to be recovered. In V19 Pair 3: four entities (`9A84`, `9A85`, `9A89`, `9A8A`) comprising the T7 wire were missing in V18.

### 2. Extract Raw Entity Blocks from Source DXF

Use line-by-line scanning or regex to find the entity block in the earlier clean DXF:

```python
def extract_entity_block(lines, handle):
    """Extract entity block from DXF lines by handle."""
    for i in range(len(lines)):
        line = lines[i].strip()
        if line == '5' and i+1 < len(lines):
            next_line = lines[i+1].strip()
            if next_line == handle:
                # Found handle line at i+1; walk back to find entity type
                start = i - 2
                while start >= 0:
                    if lines[start].strip() == '0':
                        break
                    start -= 1
                # Walk forward to find end of entity block
                end = i + 2
                while end < len(lines):
                    if lines[end].strip() == '0' and end + 1 < len(lines):
                        break
                    end += 1
                return lines[start:end]
    return []
```

### 3. Find Insertion Landmark in Target DXF

Identify an existing entity handle in the target DXF that is spatially or logically adjacent. In V19, ARC `9A82` was the last valid entity before the missing T7 wire block.

### 4. Insert Extracted Block

Find the entity terminator line (`0` followed by `ENDSEC` or next entity type) after the landmark entity. Insert the extracted block(s) right before that terminator.

```python
# Find where entity 9A82 ends
insertion_idx = find_entity_end(target_lines, '9A82')
# Insert extracted blocks in correct order
for block in blocks:
    target_lines = target_lines[:insertion_idx] + block + target_lines[insertion_idx:]
    insertion_idx += len(block)
```

### 5. Guard Against Structural Corruption

After insertion, **always** check for these two dangerous patterns:

- **Duplicate `0` lines:** `"  0\n  0\n"` in the resulting text means two entity terminators are adjacent, which will cause `Invalid group code "ARC"` (or similar) parse errors at load time. Fix by removing one of the duplicate `0` lines.
- **Missing `0` separator:** The inserted block must end with a `0` line so the next entity has a proper boundary.

### 6. Verify

```python
doc = ezdxf.readfile("rebuilt.dxf")
msp = doc.modelspace()
for e in msp:
    if e.dxf.handle in expected_handles:
        print(f"Found {e.dxf.handle}: {e.dxftype()}")
```

## Symmetric Risk: Deleting Entity Blocks Also Corrupts Structure

Surgical *removal* of specific entity blocks from a DXF carries the same structural corruption risk as insertion. When you delete an entity by stripping its full raw-text block — from its opening `0`/`ENTTYPE` lines through its terminating `0` / next-entity boundary — the gap between the preceding and following entities can produce:

- **Duplicate `0` terminators**: If the deleted block ended with a `0` line and the next entity also starts with `0`, you get `"  0\n  0\n"`, which ezdxf interprets as `"Invalid group code 'ARC'"` (or whatever entity type follows).
- **Missing `0` separator**: If your line-range deletion accidentally swallows the next entity's opening `0` line, that next entity loses its ASCII boundary and causes parse errors further down.

### Pair 3 V20 Example

Deleted handles `9A82`, `9A84`, `9A8E`, `9A8F`, `9A90` from `3_cloned_v19.dxf` by removing five consecutive raw text blocks. After removal, an extra `0` line remained between the preceding ARC and the next INSERT, producing `Invalid group code "ARC"`. Removing one of the duplicate `0` lines restored valid parsing.

**Procedure after every raw DXF deletion:**
1. Run `ezdxf.readfile()` on the result immediately.
2. If it raises `DXFStructureError("Invalid group code …")`, search for consecutive `0` lines in the affected section.
3. Fix by removing one duplicate `0` line, or adding back a missing `0` line that was accidentally consumed.

## Pitfall: ezdxf Save Is Unreliable

The `rebuilt.dxf` can be read by ezdxf for verification, but `doc.saveas()` may fail with:
```
AttributeError: 'str' object has no attribute 'dxf'
```
This is the materials table error. Do NOT use ezdxf to write the file — keep the text-edited DXF as-is and export via QCAD headless or deliver as DXF directly.

## Pitfall: Handle Collision

If the recovered handle already exists in the target DXF (e.g. from contamination or earlier clones), remap the recovered entity to a new unused handle before insertion. Verify with regex scan for duplicate group-code-5 values.

## V21 Extension: Coordinate-Shifting Recovery for Pattern Matching

When the missing entity must be recovered but placed at a **different position** than its original location (e.g., a cable callout cloned from T5 to T7, or a symbol shifted to align with a new wire end), extract the raw entity block, apply coordinate shifts via string replacement, assign a new free handle, and insert.

### When to Use

- A duplicate callout was deleted and now needs to be **recreated at a new position** (not the original position in the source DXF).
- A wiring-end symbol (WFEND, WECOIL) needs to be **shifted along a wire** to its correct end position.
- The source entity exists in an earlier DXF but at the wrong coordinates for the current target.

### Procedure

**1. Determine the required offset by pattern matching.**
Find a reference entity at a known-good position (e.g., T5 cable-end callout at x≈19.47) and compare with the desired target position (T7 wire end at x≈20.87). Compute `Δx = target_x - source_x` (e.g., +1.4 DXF units).

**2. Extract raw entity blocks from the source DXF.**
Same as standard recovery — locate by handle, walk back to opening `0`/`ENTTYPE`, walk forward to closing boundary.

**3. Shift coordinates BEFORE inserting.**
Apply string replacements to the raw block text to shift all x-coordinates by `Δx`:

```python
# Example: shift all group-code-10 x coordinates in raw block by +1.4
import re
shifted_block = re.sub(
    r'( 10\n)([0-9.]+)',
    lambda m: f'{m.group(1)}{float(m.group(2)) + dx:.6f}',
    raw_block,
    flags=re.MULTILINE
)
# Repeat for group code 11 (endpoints of LINE), 13, 14, etc.
```

**4. Assign free handles.**
Scan the target DXF for all existing handles in ENTITIES and OBJECTS sections. Pick unused handles well above the current max entity handle but safely below the original DWG max (or use any gap). Document them explicitly:

```python
all_handles = set(re.findall(r'^  5\n([0-9A-Fa-f]+)', target_dxf_text, re.M))
free_handles = [h for h in ['9A93', '9A94'] if h.upper() not in all_handles]
# Replace handle in raw block before insertion
shifted_block = shifted_block.replace(f'\n  5\n{old_handle}\n', f'\n  5\n{new_handle}\n')
```

**5. Insert at ENTITIES section boundary — NOT after THUMBNAILIMAGE.**
A common trap: inserting after `THUMBNAILIMAGE` section's `ENDSEC` places the new entity in the HEADER space instead of ENTITIES, causing ezdxf to silently ignore it. Always insert **before** the `ENTITIES` section's `ENDSEC` terminator.

```python
# WRONG — after THUMBNAILIMAGE
insertion_point = content.find("THUMBNAILIMAGE\n  2\nTHUMBNAILIMAGE", content.find("ENTITIES"))
# RIGHT — before ENTITIES ENDSEC
entities_endsec = content.find("  0\nENDSEC\n", content.find("  0\nENTITIES\n"))
# Verify: the character preceding insertion_point should be part of a valid entity terminator
assert content[entities_endsec-3:entities_endsec] == "  0\n", "Insertion boundary mismatch"
```

**6. Verify with ezdxf AND visual overlay.**
After insertion, validate:
- `ezdxf.readfile()` loads without structural errors.
- Entity count increased by expected number.
- New coordinates match target position (±0.01 tolerance).
- No duplicate `0` lines at insertion boundary.

### Example — Pair 3 V21 (CA-1452 callout + WFEND/WECOIL shift)

**Context:** V20 deleted CA-1452 callout entities (TEXT 9A8E, LINE 9A8F, LWPOLYLINE 9A8A) from T7 because they were duplicate clones. After deletion, the empty bracket 9A8A remained at x=20.53 but the tagged callout was gone. The user needed the tagged callout restored at T7 wire end.

**Recovery:**
1. Extracted raw blocks for TEXT 9A8E and LINE 9A8F from V19 DXF (which had the correct content).
2. Computed shift: T5 callout at x≈19.47 → T7 wire end at x≈20.87, `Δx = +1.4`.
3. Shifted all group-code 10 x-coordinates by +1.4 in both blocks.
4. Assigned free handles 9A93 (TEXT) and 9A94 (LINE leader).
5. Inserted before ENTITIES ENDSEC at char 300119.
6. Additionally shifted existing WFEND 9A91 and WECOIL 9A92 by +1.4 via in-place raw text coordinate replacement (preserving their INSERT block references and layers).

**Result:** V21 DXF — 243 entities (+2 from V20). CA-1452 text and leader present at T7 wire end, WFEND/WECOIL positioned at wire end (x=20.75/20.09), empty bracket 9A8A intact.

### Pitfall: ENDSEC Insertion Trap

Inserting new entities after `THUMBNAILIMAGE ENDSEC` instead of before `ENTITIES ENDSEC` orphans them. ezdxf will load the file without errors but the new entities won't appear in modelspace query results. Always verify insertion point is inside the ENTITIES section by checking the preceding context for entity-type group codes (e.g., `ARC`, `LINE`, `INSERT`) rather than section names.

---

## Files (V19 Example)

- `3_cloned_v17.dxf` — clean source containing T7 wire entities
- `3_cloned_v18_fixed.dxf` — working target missing T7 wire
- `3_cloned_v19.dxf` — result after extracting and inserting T7 wire + deleting stray ARC
