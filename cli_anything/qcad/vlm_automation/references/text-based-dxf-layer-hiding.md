# Text-Based DXF Layer Hiding for Cloud Deletions (2026-06-10, DEPRECATED 2026-06-11)

> **DEPRECATED:** This reference documents the `Defpoints` layer reassignment approach, which does NOT survive LibreDWG round-trip (layer names revert to empty/defaults). The correct approach is group code 60 invisibility. See `references/pair4-raw-text-dxf-editing.md` for the validated replacement workflow.

When a markup PDF shows cloud regions (revision clouds) around entities that must be deleted, the DXF entity-deletion workflow is fragile. Deleting entities destroys handle coherence and produces DWGs with dangling OBJECTS-section references. Layer reassignment to `Defpoints` was previously thought to be the safer text-level equivalent.

## Why Delete Fails

- ezdxf `delete_entity()` + `saveas()` produces DXFs with broken MATERIAL entries that crash dxf2dwg
- Text-level entity removal breaks `330` soft-pointer references between HATCH boundary paths and their parent loops
- dxf2dwg segfaults (exit 139) or fails with "Unknown DXF code 330 for HATCH"

## Why Defpoints Reassignment FAILS

- `Defpoints` layer name does NOT survive LibreDWG round-trip
- After `dxf2dwg` → `dwg2dxf`, the layer name reverts to empty/defaults
- The entity reappears in the drawing (visible again)
- Group code 60 invisibility survives round-trip cleanly

## Correct Replacement: Group Code 60 Invisibility

See `references/pair4-raw-text-dxf-editing.md` for the complete, validated workflow.

Key pattern:
```python
# After identifying entity handles via ezdxf (for discovery only):
handles_to_hide = {'AEF5B', 'AEF86', ...}

# In raw DXF, insert "60\n1\n" immediately after the handle (group code 5)
for handle in handles_to_hide:
    # Find line containing handle after group code 5
    for i, line in enumerate(lines):
        if line.strip() == '5' and i+1 < len(lines) and lines[i+1].strip().upper() == handle:
            lines.insert(i+2, '60\n')
            lines.insert(i+3, '1\n')
            break
```

## Critical Rules

1. **Never delete the entity** — dxf2dwg needs all handles intact
2. **Never use ezdxf `saveas()` on LibreDWG-roundtripped DXFs** — produces broken MATERIAL entries
3. **Use ezdxf ONLY for discovery** — `readfile()` is safe; `saveas()` is not
4. **Never use Defpoints layer reassignment** — does not survive round-trip
5. **Use group code 60 = 1 instead** — survives round-trip, preserves handles, accepted by all CAD tools
6. **Verify with dxf2dwg immediately** — `dxf2dwg edited.dxf -o test.dwg`

## Verification

After group code 60 insertion + dxf2dwg + dwg2dxf round-trip:
```python
# Confirm group code 60 is still present in the re-exported DXF
# Confirm entity group code 1 values (text) are preserved
# Confirm no "Invalid boundary_handles size" errors in dxf2dwg
```
