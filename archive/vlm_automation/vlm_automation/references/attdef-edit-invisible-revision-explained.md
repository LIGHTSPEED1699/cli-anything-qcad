# DXF/DWG BLOCK/AATTDEF Behavior Analysis (Pair 3 Revision Table)

## Session
2026-05-23. User attempted manual revision history edit in QCAD Pro GUI.

## User Observation
In QCAD Pro, the user:
1. Navigated to modelspace → title block area → BLOCK INSERT "PLAINS-D-CAN" 
2. Double-clicked → **Block Editor opened**
3. Edited the **default value** (ATTDEF group code 1) for **REV_1**, **REV_DATE_1**, **REV_DESCR_1**, and others
4. Used "Close Block Editor" / Escape to return to main drawing
5. **Modified values were NOT visible** in modelspace title block table

## Why the Edits Did Not Appear

The user edited **ATTDEF defaults** (what a NEW instance would get when first inserted), **NOT** the values of any EXISTING instance.

CAD file stores revision data at **two separate levels**:

| Level | DXF Record | How it stores default text | When it appears |
|-------|-----------|---------------------------|-----------------|
| Block Definition | `ATTDEF` in `BLOCKS` section | Group code `1` = default text | Only when a new INSERT is created with NO matching ATTRIB |
| Block Instance | `ATTRIB` under the `INSERT` in `ENTITIES` | Group code `1` = actual value | What you SEE on screen (if it exists) |

### The "Hidden" Attribute Problem

In the original DWG, the title block INSERT [47E7] had only **ONE** ATTRIB child: `tag='PIPELINE_SYSTEM'` with **empty** text. It did NOT have ATTRIB instances for REV_1, REV_DATE_1, etc.

**This is by design in the industry-standard title block pattern:**
- ATTDEFs with empty defaults → fields exist in block definition → if NO ATTRIB instances exist for those tags, CAD software (AutoCAD, BricsCAD) may display the ATTDEF default text as a placeholder
- In QCAD, if no ATTRIB exists, **nothing** displays — QCAD does NOT auto-render ATTDEF defaults
- To make a revision value visible, an actual ATTRIB record must be present in the INSERT entity group, with `tag='REV_1'` and `1='01'` (or whatever)

## QCAD ODA Export Destroys ATTRIB Instances

When our pipeline exported via QCAD ODA (`qcad-bin` ECMAScript), the ENTITIES-section ATTRIB [47E8] (PIPELINE_SYSTEM) was preserved, but all ATTDEF records in the BLOCKS section were reduced to minimal geometry-only definitions.

**Original 3.dwg:** 314,265 bytes, 92 ATTDEFs in PLAINS-D-CAN block
**V24 exported DWG:** 76,593 bytes, 0 ATTDEFs, 1 ATTRIB (empty PIPELINE_SYSTEM)

This is a **permanent downsampling** — the exported DWG has no framework to display revision data unless we add explicit ATTRIB instances to the INSERT.

## Two Ways to Actually Show Revision Data

### Method 1: Add ATTRIB instances to the modelspace INSERT (programmatic)

In `3_cloned_v24.dxf`, locate INSERT [47E7] in ENTITIES. After any existing SEQEND, insert ATTRIB records:

```
ATTRIB
  5
<new handle>
330
47E7                ← owner handle = the INSERT
100
AcDbEntity
  8
0
100
AcDbText
 10
<same x as title block>
 20
<same y as title block>
 30
0.0
 40
0.125              ← text height
  1
01                   ← actual value displayed!
  7
Standard
100
AcDbAttribute
  2
REV_1               ← must match ATTDEF tag name
 70
     0              ← invisible? no — keep 0 for visible
```

Repeat for each revision column (REV_DATE_1, REV_DESCR_1, REV_DRAW_1, REV_CHK_1, REV_APPD_1, REV_AFE_NO_1, etc.).

**QCAD will then display the ATTRIB text in modelspace** (no need to enter block editor again).

### Method 2: Add plain TEXT/MTEXT next to the block (standalone)

If modifying block attributes is too risky (could break the INSERT relationship), insert independent TEXT/MTEXT entities in modelspace near the title block coordinates:

```python
import ezdxf
doc = ezdxf.readfile('3_cloned_v24.dxf')
msp = doc.modelspace()
msp.add_text("Revision: 01 / 2026-05-20", dxfattribs={
    'insert': (6.0, 1.5, 0.0),
    'height': 0.125,
    'style': 'Standard'
})
```

**Pro:** No block dependency risk.
**Con:** Not actually part of the title block; won't move/scale if the block is edited later.

## Why Our Pipeline Can't Preserve ATTDEF Defaults

QCAD ODA exports by **reconstructing** block definitions from scratch. It inspects which ATTRIB instances are used by INSERTs, and only retains ATTDEF tags for those that have instances. Tags with NO instances (REV_1, etc.) are silently discarded.

LibreDWG `dxf2dwg` CAN preserve the original BLOCKS section, but its output format is pre-AutoCAD 2018 and may not open cleanly in modern AutoCAD/TrueView. Tradeoff table:

| Conversion Method | Preserves ATTDEF? | DWG Version | AutoCAD 2018+ Compatible? |
|---|---|---|---|
| QCAD ODA `qcad-bin` | **No** strips to 5 tags only | R32 (current) | ✓ Yes |
| LibreDWG `dxf2dwg` | **Yes** block section intact | R18 (pre-2018) | ⚠ May error/warn |

## User Guidance for Manual Revision Updates

When a user wants to update revision data on a DWG from our pipeline:

1. **Warn immediately:** "The exported DWG has stripped block definitions. Revision data exists in the source DXF but was not carried into this DWG."
2. **Two paths:**
   - **Original DWG path:** Open `3.dwg` directly in AutoCAD/QCAD GUI and edit the revision block. The original file has all ATTDEFs intact and will reflect block default changes.
   - **Add standalone TEXT:** Place plain modelspace TEXT overlapping the title block area. Not true block data, but visible in modelspace and survives QCAD ODA round-trip.
3. **Do NOT attempt** to edit block definitions in the exported QCAD DWG — the source ATTDEFs are gone, and any edits inside the block editor won't propagate to modelspace.

## Files

| File | Contains |
|---|---|
| `3.dwg` (original) | PLAINS-D-CAN block with 92 ATTDEFs + INSERT with one empty ATTRIB |
| `3_cloned_v24.dxf` | Full BLOCKS section preserved (56 REV_* tags intact) |
| `3_FINAL_v24.dwg` | Stripped BLOCKS section (0 REV_* tags, 0 ATTDEFs, single ATTRIB) |

## Related References

- `references/qcad-block-stripping.md` — broader notes on QCAD ODA block downsampling
- `references/pair3-v9-v13-hardening.md` — VLM verification that catches invisibility
- `references/vlm-post-edit-visual-verification.md` — why further edits may not appear
