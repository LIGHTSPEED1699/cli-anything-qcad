# QCAD Block Editor "Edits Don't Display" Misleading Behavior

## Session
2026-05-23. Spot (user) attempted manual revision history edit in QCAD Pro GUI on `3_FINAL_v24.dwg`.

## What Happened

1. Clicked title block once (single click) → right-click → chose "Edit block" / "Edit block in place"
2. QCAD entered block definition editor (BEDIT)
3. `REV_1`, `REV_DATE_1`, `REV_DESCR_1` fields were visible and editable
4. Changed values, saved block, returned to modelspace
5. **Changes were NOT visible** on the main drawing
6. Re-entered block editor → values *were* still there
7. Tried "Edit block in place" again → values were still there

## Why the Block Editor Misleadingly Appears to Work

When you enter the block editor, QCAD reads the block definition's **ATTDEF** records. These tags exist inside the block definition (`BLOCKS` section of DXF). QCAD renders them as editable fields.

**But the drawing's main view does NOT display ATTDEF records directly.** It displays **ATTRIB** instances — which belong to the INSERT entity in the modelspace (`ENTITIES` section).

Here's the critical gap: the exported DWG produced by QCAD ODA headless (`3_FINAL_v24.dwg` / its source DXF `3_cloned_v24.dxf`) contains:
- **ATTDEFs** inside the PLAINS-D-CAN block definition → QCAD reads these → shows them in block editor
- **Zero ATTRIB instances in modelspace** for REV_1, REV_DATE_1, etc. → nothing to render on the main drawing

This creates the illusion that editing is working because:
- You see the fields → they exist in the definition
- You change values → the definition defaults change
- You save → QCAD saves the definition
- You return to modelspace → nothing appears because there is no ATTRIB instance to carry the value

## The Attrib-vs-Attdef Recap

| DXF Record | Section | What it stores | Where it appears |
|---|---|---|---|
| `ATTDEF` | `BLOCKS` (inside block definition) | Default text for new block instances | QCAD Block Editor |
| `ATTRIB` | `ENTITIES` (children of INSERT) | Actual per-instance text value | Main drawing (modelspace / paper space) |

To display revision data:
- **ATTDEF** must exist in block definition (it does, in original 3.dxf) → QCAD will show it in editor
- **ATTRIB** must exist under the INSERT in entities (it does NOT, in exported v24) → QCAD shows nothing on screen

## Diagnostic Check

When a user reports "block editor edits don't show in main drawing", verify this:

```bash
# In the DXF that produces the DWG
grep -c "^ATTRIB$" 3_cloned_v24.dxf
grep -c "^ATTDEF$" 3_cloned_v24.dxf

# Then find which ATTRIB tags exist
grep -A 1 "^  2$" 3_cloned_v24.dxf | grep "REV_"
```

Result from v24 DXF:
- ATTDEFs: ~92 (inside PLAINS-D-CAN block definition) → QCAD Block Editor sees these
- ATTRIBs for REV_1: 0 → QCAD modelspace sees nothing for REV_1
- This is the confirmed structural gap

## Fix Options

### A. Use the original DWG / intact DXF
- Open `3.dwg` directly in QCAD GUI (not the exported one)
- The original has ATTRIB instances → block edits (or Property Editor edits) will actually show up

### B. Add ATTRIB instances programmatically
- In DXF, under the title block INSERT in `ENTITIES`, insert ATTRIB records for REV_1, REV_DATE_1, etc.
- Then QCAD will render them in modelspace without entering the block editor
- See `references/attdef-edit-invisible-revision-explained.md` for raw DXF example

### C. Add plain TEXT entities
- Insert standalone TEXT/MTEXT near the title block coordinates
- Pro: no block dependency; always survives export
- Con: not truly part of the block; won't move with block edits; not semantically a title block attribute

## Prevention in Pipeline

When the pipeline produces a DWG for spot-check by user:
- **Do NOT** tell the user "you should be able to edit the block to update revisions" — if the DWG is from QCAD ODA export, the ATTRIB instances are gone
- Instead: **guide user to original DWG** for any block-level attribute edits
- Or: **pre-populate revision fields** as plain TEXT in modelspace before export
- Or: **warn explicitly**: "This exported DWG has stripped block attributes. Revision editing requires the original 3.dwg."

## Related

- `references/attdef-edit-invisible-revision-explained.md` — technical DXF structure of ATTDEF vs ATTRIB
- `references/qcad-block-stripping.md` — QCAD ODA export strips ATTRIB/ATTDEF from blocks
- `references/pair3_clone_and_dwg_conversion.md` — why QCAD ODA export is used

