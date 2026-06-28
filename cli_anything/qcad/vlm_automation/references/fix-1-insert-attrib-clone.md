# Fix 1: Clone INSERT + Child ATTRIBs Together During Pipeline Execution

## Problem

The VLM-CAD pipeline copies entities from original DXF → modified DXF one at a time. When it encounters an `INSERT`, it copies the INSERT entity but **does not copy the child ATTRIB entities** that immediately follow it in the DXF structure. In DXF, entities belonging to an INSERT have `330 = INSERT_handle` (owner pointer), forming an implicit linked list terminated by `SEQEND`.

**Current broken structure in pipeline output:**
```
INSERT (handle 47E7)    ← copied
  ...                   
ATTRIB (handle 47E8)    ← NOT copied (orphaned in original)
  ...
SEQEND (handle 4845)    ← NOT copied (orphaned in original)
```

**Result:** The new INSERT is hollow — no attributes in modelspace. When QCAD ODA exports this DXF to DWG, it sees ATTDEFs in the BLOCK definition but no ATTRIB instances in modelspace, and **discards the ATTDEFs** as "unused."

## Solution

When cloning an INSERT, also clone all child entities (ATTRIBs + SEQEND) that have `330 = INSERT.handle`.

**Correct structure after Fix 1:**
```
INSERT (new handle ABCD)    ← cloned
  ...                       
ATTRIB (new handle EFGH)   ← cloned from original 47E8
  ...
ATTRIB (new handle IJKL)   ← cloned from original 9B40
  ...
SEQEND (new handle MNOP)    ← cloned from original 4845
```

## Implementation

In the entity cloning loop (the Python script that iterates original DXF and writes to modified DXF):

```python
def clone_insert_with_children(original_dxf, modified_dxf, insert_handle):
    """
    Clone an INSERT and all its child ATTRIBs + SEQEND.
    Returns dict mapping old_handles → new_handles.
    """
    # 1. Clone the INSERT itself (already done by existing pipeline)
    new_insert_handle = clone_entity(original_dxf, modified_dxf, insert_handle)
    
    # 2. Scan original DXF for all entities with 330 == insert_handle
    #    These appear immediately after the INSERT in the DXF file
    children = []
    for entity in scan_entities_after(original_dxf, insert_handle):
        if entity.get_group(330) == insert_handle:
            children.append(entity)
        else:
            break  # End of INSERT's children
    
    # 3. Clone each child, updating owner to new_insert_handle
    handle_map = {insert_handle: new_insert_handle}
    for child in children:
        new_child = clone_entity(
            original_dxf, modified_dxf, child.handle,
            owner_override=new_insert_handle
        )
        handle_map[child.handle] = new_child.handle
    
    return handle_map
```

## Key Rules

1. **Children appear contiguously after INSERT in DXF.** The DXF specification does not guarantee ordering, but all major CAD tools (AutoCAD, QCAD, LibreCAD) write INSERT children contiguously.
2. **Owner field (group code 330) is the discriminator.** Every entity whose `330` equals the INSERT's handle belongs to that INSERT.
3. **SEQEND terminates the list.** When you encounter `SEQEND` with `330 == INSERT.handle`, that's the end of the child list.
4. **Update child owner handles.** When cloning, change every child's `330` from the old INSERT handle to the new INSERT handle.
5. **Allocate new handles for cloned children.** Use the same handle-allocation strategy as for the INSERT itself.

## Why This Fixes the QCAD ODA Export Problem

When QCAD ODA exports the modified DXF to DWG:
- It sees an INSERT with child ATTRIB instances → recognizes the BLOCK is "used"
- It preserves ATTDEFs in the BLOCK definition because ATTRIB instances reference them
- The exported DWG retains full block attribute framework

## Verification Steps After Implementation

1. Compare `strings output.dwg | grep -i "REV_"` — should show tags
2. Check file size: output DWG should be ~300KB (not ~76KB)
3. Open in QCAD → single-click INSERT → Property Editor should show Attributes section with tags visible
4. Attempt to edit `REV_DATE_1` — should accept changes (not revert to blank)

## When to Apply Fix 1

Apply this fix if any drawing in the pipeline:
- Contains blocks with attributes (title blocks, revision tables, part data)
- Needs to survive QCAD ODA export back to DWG
- Has user-facing fields that must remain editable in the final deliverable

## References

- `references/attrib-injection-technique.md` — Prior attempt (injecting synthetic ATTRIBs) failed because QCAD ODA also strips standalone ATTRIBs
- `references/revision-block-attdef-lesson.md` — Full analysis of why QCAD ODA destroys block data (76% size loss)
- `references/dwg-block-attdef-structure-and-tag-extraction.md` — DXF technical details of BLOCK/ATTDEF/ATTRIB relationships
