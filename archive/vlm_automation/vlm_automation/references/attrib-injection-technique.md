# ATTRIB Injection Technique for Modelspace INSERTs

## Context

When a DXF file contains an `INSERT` in modelspace that references a `BLOCK` with `ATTDEF` templates, but no actual `ATTRIB` instances exist in the `ENTITIES` section, the block's attribute data is effectively "hollow." This commonly occurs after QCAD ODA export, which strips `ATTRIB` instances during DWG reconstruction.

This reference documents the exact DXF-level manipulation needed to re-inject `ATTRIB` instances into the `ENTITIES` section, attached to an existing `INSERT`.

## When This Technique Is Useful

- You have a DXF with a hollow `INSERT` (ATTDEFs in BLOCKS, no ATTRIBs in ENTITIES)
- You need to restore editable attribute fields before opening in a CAD GUI
- **Limitation:** The injected ATTRIBs survive in QCAD GUI display but are destroyed by QCAD ODA headless export (see `references/revision-block-attdef-lesson.md`). Use this for GUI inspection / manual editing, not for pipeline DWG generation.

## Prerequisites

1. The target `INSERT` exists in `ENTITIES` with a known handle (e.g., `47E7`)
2. The `BLOCK` definition in `BLOCKS` contains `ATTDEF` entries with known tag names, positions, and default values
3. You have the DXF in text format (not binary)
4. You know the `INSERT` insertion point and scale factor (for coordinate mapping)

## Step-by-Step Injection

### Step 1: Extract ATTDEF Geometry from BLOCKS

For each `ATTDEF` in the `BLOCKS` section, extract:

| Field | Group Code | Description |
|-------|-----------|-------------|
| Tag name | 2 | e.g., `REV_1` |
| Default text | 1 | e.g., `""` or `"A"` |
| Height | 40 | Text height in block units |
| Text style | 7 | e.g., `Plains Slant` |
| Position (block-local) | 10, 20, 30 | x, y, z in block coordinates |
| Alignment point | 11, 21, 31 | Second alignment point |

### Step 2: Map Block-Local to Modelspace Coordinates

Given `INSERT` with:
- Insertion point `(ix, iy)`
- Scale factor `s` (uniform, or `sx, sy, sz`)

```
tx = ix + (bx * s)
ty = iy + (by * s)
```

For non-uniform scale, apply per-axis.

### Step 3: Allocate New Handles

Find the maximum handle in the DXF:

```python
import re
with open('input.dxf', 'r') as f:
    content = f.read()

handles = set()
for m in re.finditer(r'\n5\n([0-9A-F]+)\n', content):
    handles.add(int(m.group(1), 16))

max_handle = max(handles)
next_handle = max_handle + 1
```

Reserve sequential handles: `next_handle`, `next_handle + 1`, ..., `next_handle + N - 1`.

⚠️ **Verify none of the reserved handles exist** — some DXFs have sparse handle tables. Spot-check with grep.

### Step 4: Build ATTRIB Entity Blocks

Each ATTRIB entity in `ENTITIES` follows this structure:

```dxf
  0
ATTRIB
  5
<NEW_HANDLE>          ; group code 5: entity handle
330
<INSERT_HANDLE>       ; group code 330: owner (must be the INSERT)
100
AcDbEntity
  8
0                     ; layer name
100
AcDbText
 10
<MODELSPACE_X>        ; insertion x
 20
<MODELSPACE_Y>        ; insertion y
 30
0.0                   ; z
 40
<TEXT_HEIGHT>         ; text height
  1
<TEXT_VALUE>           ; text content
  7
<TEXT_STYLE>           ; text style
 11
<ALIGN_X>              ; second alignment x
 21
<ALIGN_Y>              ; second alignment y
 31
0.0
100
AcDbAttribute
  2
<TAG_NAME>             ; e.g., REV_1
 70
     0                 ; attribute flags
 74
     2                 ; text generation flags
1001
AcDbAttr               ; XDATA marker (optional, from original)
1070
     0
1070
     1
```

### Step 5: Insert in Correct Location

The ATTRIB entities must appear **after** the `INSERT` entity and its existing `ATTRIB` children, but **before** the `SEQEND` that closes the `INSERT`.

```
ENTITIES section:
  ...
  INSERT (handle 47E7)
    ...
  ATTRIB (existing child, e.g., handle 47E8)
    ...
  ATTRIB (NEW handle 9B40)    ← inject here
    ...
  ATTRIB (NEW handle 9B41)
    ...
  ...
  ATTRIB (NEW handle 9B54)
    ...
  SEQEND (handle 4845, owner 47E7)
  ...
```

Locate the insertion point by finding the last existing `ATTRIB` (or the `INSERT` itself) and inserting before the `SEQEND`.

### Step 6: Verify with ezdxf (or grep)

```bash
# Count ATTRIBs in ENTITIES section
ENT_START=$(grep -n "^ENTITIES$" file.dxf | head -1 | cut -d: -f1)
ATTRIB_IN_ENTITIES=$(sed -n "${ENT_START},$p" file.dxf | grep -c "^ATTRIB$")
echo "ATTRIB in ENTITIES: $ATTRIB_IN_ENTITIES"

# Check specific handle
grep -n "^9B40$" file.dxf | awk -F: '$1 > 32066 {print}'
```

## Example: 21-Handle Injection (2026-05-23)

**File:** `3_cloned_v24_attribs.dxf`
**INSERT:** handle `47E7`, position `(2.17544, 5.79127)`, scale `0.051722`
**New handles:** `9B40`–`9B54` (21 handles, 1 per REV_* field)
**Injected entities:** All owned by `47E7`, tags `REV_1` through `REV_8`, `REV_DATE_1` through `REV_DATE_8`, `REV_DESCR_1` through `REV_DESCR_8`, etc.

## Common Pitfalls

| Pitfall | Cause | Prevention |
|---------|-------|------------|
| Handle collision | New handle already exists in sparse table | Verify with regex scan before allocation |
| Wrong owner | 330 points to non-INSERT handle | Only set 330 to the INSERT handle |
| Missing SEQEND | Inserted after SEQEND instead of before | Search for `330` + `INSERT_HANDLE` in reverse from EOF |
| Coordinate mismatch | Used block-local coords instead of modelspace | Apply INSERT transform: `tx = ix + (bx * s)` |
| Layer mismatch | ATTRIB on wrong layer | Use layer "0" (same as INSERT) unless explicit layer needed |
| ezdxf crash | Empty text values in ATTRIBs | Use `" "` (space) instead of `""` if ezdxf validation needed |

## What This Does NOT Solve

❌ QCAD ODA headless export strips **ALL** ATTRIB instances (confirmed 2026-05-23)

**Injection verification:** 21 ATTRIBs (handles 9B40–9B54) were successfully created in the DXF, all owned by INSERT 47E7. After QCAD ODA export: exported DWG was only +1,377 bytes larger (77,908 vs 76,531), `strings` found zero `REV_`/`REV_DATE_`/`REV_DESCR_` tags. The ODA writer discards ATTRIBs regardless of whether they exist in BLOCKS or ENTITIES.

The injection works for:
- ✅ Opening in QCAD GUI and seeing editable attributes
- ✅ Exporting to DXF (retains ATTRIBs)
- ✅ Using LibreDWG `dxf2dwg` (might retain them, untested)

But fails for:
- ❌ QCAD ODA DWG export (`qcad-bin -autostart export.js`)

**Root cause:** QCAD ODA rebuilds the DWG BLOCK structure from scratch using only primitive geometry visible in modelspace. ATTRIB entities (in both BLOCKS and ENTITIES) are not primitive geometry and are discarded during reconstruction.

For DWG export that preserves attributes, you must use:
- **Original AutoCAD DWG** + DWG-native editor (AutoCAD, QCAD Pro GUI with save-to-DWG), OR
- **ODA File Converter** (standalone tool that does DWG↔DXF without authoring-level optimizations), OR
- **Fix 1: Insert + ATTRIB cloning** (see `references/fix-1-insert-attrib-clone.md`)

## References

- `references/revision-block-attdef-lesson.md` — Full analysis of why this technique was attempted and why it ultimately fails for the revision-table use case
- `references/dwg-block-attdef-structure-and-tag-extraction.md` — Technical details of BLOCK/ATTDEF/ATTRIB DXF structure
