# Pair 3 V19 Lesson: Revision Data is BLOCK-Level ATTDEF, Not Modelspace ATTRIB

## Session
2026-05-18. Pair 3 V19 DWG revision update pipeline.

## User Request
> "the revision history and number were shown in the original 3.dwg file. the changes to revision are shown in pdf markup 3.pdf. please proceed updating this dwg file"

## Investigation

1. **ezdxf modelspace query** on the PLAINS-D-CAN INSERT [47E7] found only **one** attached ATTRIB instance: tag=`PIPELINE_SYSTEM`, text empty.
2. **Block definition scan** in the BLOCKS section of `3.dxf` revealed ATTDEF entries:
   - tag="Revision", default text="A"
   - tag="Drawn Date", default text="`200x/xx/xx``
   - tag="Line 1-8 Revision", default text empty
   - tag="Line 1-8 Revision Date", default text empty
   - tag="Line 1-8 Drawn", default text empty
3. **Binary string extraction** from `3.dwg` found "01", "00" in the binary, confirming revision data exists in the original DWG but is encoded differently than modelspace TEXT.
4. **Tag name discovery (V24 session):** The DXF BLOCKS section contains **56 `REV_*` tag names** (e.g. `REV_1`, `REV_DATE_1`, `REV_DESCR_1`, `REV_DRAW_1`, `REV_CHK_1`, `REV_APPD_1`, `REV_AFE_NO_1`, and lines 1–8). These strings exist in the DXF (plain text) but **not** in the original binary DWG (encoded in binary tag structures), and **not** in the final QCAD-exported DWG (stripped).
5. **Tag name vs displayed text:** `REV_1` is the **tag name** (group code 2 inside ATTDEF), not the displayed text. The actual displayed text is the group code 1 value of the same ATTDEF, which is typically empty (the ATTRIB instance in modelspace overrides it, or the user fills it in CAD software).

## What REV_* Actually Is

| Group Code | Meaning | Example |
|---|---|---|
| 2 | Tag name (field identifier) | `REV_1` |
| 1 | Default value (displayed text) | `""` or `"A"` |

`REV_1` tells CAD software "this field is for Revision 1." The value shown on the drawing comes from either the ATTDEF default (group code 1) or an ATTRIB instance attached to the INSERT in modelspace. When neither exists, the field is blank on the drawing but the tag name preserves the template.

## Root Cause

The revision history table in the PLAINS-D-CAN title block stores its data as **ATTDEF default text values within the BLOCK definition**, not as modelspace ATTRIB instances. When the drawing is exported via QCAD ODA headless (`qcad-bin` ECMAScript), the BLOCKS section is stripped down (~76% size loss: 314 KB → 76 KB), and ATTDEF defaults are lost because QCAD ODA rebuilds blocks from scratch based only on what geometry is instantiated in modelspace. Tag names like `REV_1`, `REV_DATE_1`, `REV_DESCR_1` are discarded as unused block metadata.

**Stripping verification:**
| File | REV_* tags | ATTDEF count |
|---|---|---|
| Original `3.dwg` | Not visible in `strings` (binary encoded) | Present |
| `3.dxf` (LibreDWG dwg2dxf) | **56 present** | 92 in PLAINS-D-CAN block |
| `3_cloned_v24.dxf` (after edits) | **56 present** ✓ | 92 present ✓ |
| `3_FINAL_v24.dwg` (QCAD ODA export) | **0 present** ✗ | **0 present** ✗ |

## Implication for VLM-CAD Pipeline

The VLM-CAD pipeline (which edits ENTITIES-level DXF and exports DWG via QCAD ODA) **cannot preserve or edit BLOCK-level ATTDEF data** because:
- QCAD ODA export strips BLOCK definitions and re-creates them without ATTDEF defaults
- Text-based editing of the ENTITIES section cannot reach into BLOCK definitions
- Any programmatic revision update must happen at the BLOCK definition level before QCAD ODA conversion (after which it is permanently lost)

## Injection Attempt (2026-05-23): Modelspace ATTRIB Instances

After discovering that BLOCK ATTDEFs are stripped, we attempted a workaround: **inject standalone ATTRIB instances directly into the ENTITIES section**, owned by the modelspace INSERT (handle `47E7`).

**What was done:**
- Allocated new handles `9B40`–`9B54` (21 sequential handles above DXF max `9B3D`)
- Inserted 21 ATTRIB entities in ENTITIES between the existing `PIPELINE_SYSTEM` ATTRIB and the SEQEND
- Each ATTRIB had correct owner handle `330=47E7`, tag names (`REV_1`, `REV_DATE_1`, `REV_DESCR_1`, etc.), and placeholder text values
- Verified: 21 ATTRIBs present in the DXF, all owned by INSERT 47E7

**Result after QCAD ODA export → DWG:**
- Exported DWG size: 77,908 bytes (vs. 76,531 for the non-injected version) — only **+1,377 bytes** increase
- `strings` extraction: **zero** `REV_` or `REV_DATE_` or `REV_DESCR_` strings found
- Binary inspection: no ATTRIB tags survive

**Conclusion:** QCAD ODA export strips **both** BLOCK ATTDEFs **and** standalone modelspace ATTRIBs. The ODA writer rebuilds the DWG BLOCK structure from scratch using only the geometry present in modelspace. Any entity that is not a primitive geometric object (LINE, CIRCLE, TEXT, LWPOLYLINE, etc.) is discarded during reconstruction.

**This eliminates Options B and C above for the revision-table use case.** The only viable path is Option A: edit the original DWG with a DWG-native editor.

## Mitigation Options

| Option | Approach | Pros | Cons |
|--------|----------|------|——|
| A | Use the **original DWG**, open in AutoCAD/QCAD GUI, edit block definition directly | Preserves all block data, user expects | Not programmatic; requires manual CAD work |
| B | ~~Add standalone ATTRIB instances in modelspace~~ | ~~Theory: ATTRIBs survive as INSERT children~~ | **FAILS:** QCAD ODA strips all ATTRIBs (confirmed 2026-05-23) |
| C | Edit DXF **BLOCKS section** text directly, then convert with LibreDWG `dxf2dwg` | Fully programmatic | dxf2dwg incompatible with AutoCAD 2018+; may lose other ODA-specific features |
| D | Add **standalone TEXT/MTEXT** in modelspace mimicking the revision table | Programmatic via ezdxf/ezdxf add-on; survives QCAD export | Not tied to block; won't update block definition; may overlap if block is later edited |

## User Decision

User chose **Option A** — they will handle revision manually in QCAD/AutoCAD after confirming mechanical wire fixes are correct.

## QCAD Property Editor Behavior with Blank ATTRIB Fields

When editing an INSERT in QCAD that has ATTRIB instances attached:

- **Property Editor panel (single-click INSERT):** Fields that already have **non-empty text values** show correctly and can be edited. Fields that are **blank** (empty string `""`) show as blank and **do NOT accept new text** — typing and pressing Enter reverts to blank.
- **Double-click attribute text directly:** If the attribute text is **already visible on the drawing**, double-clicking it allows inline editing. However, if the field is blank (no visible text on the drawing), QCAD cannot enter edit mode for it.
- **Block Editor (BEDIT):** Changing ATTDEF default values inside the block definition affects **future INSERTs only**, not the existing modelspace instance.

This means an exported DWG with hollow ATTRIB instances (tag exists but text is empty) **cannot** be rehabilitated through the QCAD GUI. Only the original DWG with populated ATTRIB values is editable.

## Lesson for Future Tasks

When a user asks to update "revision history", "title block", or "sheet data":
1. First determine WHERE the data lives: modelspace TEXT, modelspace ATTRIB (with text), or BLOCK ATTDEF defaults.
2. If BLOCK ATTDEF: warn the user immediately that QCAD ODA export will destroy this data. Offer to:
   - Provide a clean DXF with BLOCKS section intact (for manual edit)
   - Or add standalone modelspace TEXT as a non-block placeholder
3. If the user has already exported through the pipeline (ATTRIB text empty/hollowed): **state explicitly** that the exported DWG cannot be fixed in QCAD — blank attributes are dead. The user must either:
   - Edit the **original pre-pipeline DWG** (recommended)
   - Re-apply pipeline geometry changes to the original DWG
4. Never assume title-block data is editable via the standard ENTITIES-level DXF pipeline.

## Files
- `3.dwg` — Original DWG retaining PLAINS-D-CAN BLOCK ATTDEF data ( revision "01" strings in binary)
- `3_FINAL_v19.dwg` — QCAD ODA exported; BLOCK data lost; revision not updated
- `3_cloned_v19.dxf` — Intermediate DXF with BLOCKS section preserved (but not yet edited for revision)
