# DWG BLOCK-ATTDEF Structure: Why Blocks Differ from Ordinary Objects

## Problem Statement

CAD novices (and automators) assume blocks are just grouped entities you can move together. In reality, blocks are a **three-part referencing system** that splits data across three separate records in the DWG/DXF file. This makes programmatic editing fundamentally different from editing a simple TEXT or LINE entity.

---

## Three Records, Three Sections

| Record | DXF Section | What It Stores |
|---|---|---|
| **BLOCK definition** | `BLOCKS` | The template: geometry (lines, arcs, text), plus ATTDEF placeholders with field names like `REV_1`, `REV_DATE_1`, and an optional default text value |
| **INSERT instance** | `ENTITIES` | Placement command: "Put block *PLAINS-D-CAN* at position (0, 0), scale 1.0, rotation 0°" — one INSERT per appearance on the drawing |
| **ATTRIB values** | `ENTITIES` (child of INSERT) | Actual displayed text values that override the ATTDEF defaults — one ATTRIB per ATTDEF field that the user filled in |

**Key distinction:** A block can have 92 ATTDEF fields but only 3 ATTRIB instances. The remaining 89 fields are blank on the drawing. But the **tag names** (REV_1, etc.) exist in the BLOCK definition as metadata.

---

## Ordinary Entity vs Block: Editing Comparison

| Operation | Ordinary TEXT entity | Block attribute |
|---|---|---|
| **What you see in DXF** | `0 ENTITIES → 0 TEXT → 1 "A"` | `0 ENTITIES → 0 INSERT → 0 ATTRIB → 1 "A" → 2 "REV_1"` |
| **Displayed text** | Group code 1 directly | ATTRIB group code 1 (overrides ATTDEF default) |
| **Field identifier** | None | ATTRIB/ATTDEF group code 2: tag name (e.g. `REV_1`) |
| **Default value** | None | ATTDEF group code 1: shown when no ATTRIB exists |
| **Move** | Change 10/20 (insertion point) | Change INSERT 10/20 (whole block shifts; relative offsets preserved) |
| **Delete** | Remove entity lines from DXF | Remove INSERT + children; block definition may still exist orphaned in BLOCKS section |
| **Find by text content** | Search group code 1 for "A" | Search ATTRIB group code 1 for "A", or search INSERT for handle, or search BLOCKS for tag name via ATTDEF |

---

## Where REV_* Tags Live

The tag names discovered in the V24 session (`REV_1`, `REV_DATE_1`, `REV_DESCR_1`, `REV_DRAW_1`, `REV_CHK_1`, `REV_APPD_1`, `REV_AFE_NO_1`, and lines 1–8) are **ATTDEF tag names** inside the PLAINS-D-CAN block definition.

**ATTDEF structure in DXF (simplified):**
```
  0
ATTDEF                           ← Entity type
  5
443D                             ← Handle
330
43DA                             ← Owner (PLAINS-D-CAN block)
  8
0                                ← Layer
100
AcDbText
 10
633.47                           ← X position (relative to block origin)
 20
41.99                            ← Y position
 40
0.2                              ← Text height
  1
                                 ← Group code 1: DEFAULT DISPLAY TEXT (empty here)
  2
REV_1                            ← Group code 2: TAG NAME (field identifier)
  3
                                   ← Tag prompt (optional)
100
AcDbAttributeDefinition
```

**What you see on the drawing:** If no ATTRIB exists in modelspace, the field is blank (because group code 1 is empty). If an ATTRIB is attached to the INSERT, its group code 1 value is displayed. The tag name `REV_1` is NEVER shown to the user — it's metadata for the CAD software.

---

## Why QCAD ODA Export Destroys This Data

QCAD ODA `qcad-bin` + ECMAScript export follows this logic:

1. Read all INSERT instances in modelspace
2. For each INSERT, identify which block it references
3. Rebuild a **minimal** BLOCK definition containing only the geometry needed for those INSERTs
4. Discard ATTDEF records that are not instantiated as ATTRIBs in modelspace
5. Discard extended data, XDATA, reactors, complex dictionaries

**Result:** Original DWG 314 KB with full PLAINS-D-CAN BLOCKS (92 ATTDEFs) → QCAD-exported DWG 76 KB with stripped blocks (PLAINS logo simplified, PLAINS-D-CAN missing entirely, all REV_* tag names gone).

**Stripping verification command:**
```bash
grep -c "REV_1" 3_cloned_v24.dxf   # Returns 56
grep -c "REV_1" 3_FINAL_v24.dwg    # Returns 0
grep -c "ATTDEF" 3_cloned_v24.dxf # Present
grep -c "ATTDEF" 3_FINAL_v24.dwg   # 0
```

---

## Practical Implications for Automated Pipelines

### What our pipeline CAN edit:
- Modelspace TEXT, LINE, ARC, LWPOLYLINE, INSERT position/scale/rotation
- Layer colors (ENTITIES section group code 8, 62)
- Entity deletion by handle (ENTITIES section)

### What our pipeline CANNOT edit (without special handling):
- ATTDEF defaults inside BLOCK definitions
- ATTRIB text values attached to INSERTs (requires block traversal in ezdxf, not simple msp.query)
- Block origin points or internal geometry
- XDATA, reactors, dictionaries in BLOCKS section

### What gets lost on round-trip (LibreDWG dwg2dxf → edit → QCAD dxf2dwg):
- All ATTDEF tag names
- Extended entity data (XDATA)
- Complex block structures (nested INSERTs, dynamic block features)
- Layer states and layer filters
- Drawing properties (custom properties, summary info)

---

## Recovery Strategy 

If a user's drawing has revision data in BLOCK ATTDEFs and needs automated editing of modelspace geometry:

**Approach A — Parallel track (recommended):**
1. Keep the **original DWG** as the authoritative version for block data
2. Run `dwg2dxf` → edit ENTITIES (clones, deletions) → fix layers → rebuild into **new DWG via LibreDWG `dxf2dwg`**
3. User manually merges the new modelspace geometry into the original DWG in AutoCAD/QCAD GUI
4. Block data (revision history) preserved in original; new geometry delivered separately

**Approach B — Standalone replacement (acceptable for one-off):**
1. Accept that QCAD-exported DWG will lose block metadata
2. After export, add standalone TEXT/MTEXT in modelspace at the revision table coordinates
3. Pro: visible in exported DWG. Con: not tied to block; won't sync if user edits block later.

**Approach C — Block editing before export:**
1. Use text-based DXF editing to modify ATTDEF group code 1 defaults in BLOCKS section
2. Convert with LibreDWG `dxf2dwg` (not QCAD)
3. Pro: actual block data updated. Con: LibreDWG output may be incompatible with AutoCAD 2018+.

---

## Key Lesson

**Never assume block data survives our pipeline.** Always verify:
1. Does the task require editing block-level data (revision, title block, sheet number)?
2. If yes, warn the user immediately: "QCAD ODA export will strip block metadata."
3. Offer the parallel-track strategy (original DWG + new geometry DWG) or standalone replacement.
4. Do not attempt to edit revision data inside our standard pipeline.

**See also:** `references/revision-block-attdef-lesson.md` for the specific Pair 3 V19 investigation.
