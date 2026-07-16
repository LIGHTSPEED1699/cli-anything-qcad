# LibreDWG Handle Duplication and Coherence (2026-06-10)

LibreDWG `dwg2dxf` produces DXFs with duplicate handles. This is a known behavior, not a corruption. However, it creates pitfalls for DXF editors that assume handle uniqueness.

## Handle Duplication in LibreDWG Output

**Observation:** A DWG converted to DXF by `dwg2dxf` and back to DWG by `dxf2dwg` retains structural integrity. The duplicate handles do NOT cause data loss because:
- Duplicate handles occur in different sections (e.g., BLOCKS vs. ENTITIES vs. OBJECTS)
- LibreDWG resolves them by section context during re-import
- AutoCAD also tolerates them (tested: AutoCAD 2018 opens such DWGs)

**Count:** Original `dwg2dxf` output for Pair 4 had 344 duplicate handles (out of 2,000+ total).

## Why ezdxf saveas() Breaks This

When ezdxf reads a LibreDWG DXF and calls `saveas()`:
1. ezdxf reassigns ALL handles to ensure uniqueness within its internal model
2. It updates cross-references (group code 330, 360, etc.) to match
3. But it may NOT update all OBJECTS-section references correctly
4. The resulting DXF has "fixed" handles in ENTITIES but stale references in OBJECTS
5. dxf2dwg encounters handles that don't exist and segfaults

## Text-Level Editing Preserves Handle Coherence

The safe approach is text-level editing that:
- Does NOT renumber handles
- Does NOT delete entities (which leaves dangling references)
- Only modifies values within existing entities (text content, layer names, coordinates)

This is why the layer-reassignment strategy works: the entity stays in the file, all its handles remain valid, and all cross-references remain intact.

## Practical Summary

| Strategy | Handle Impact | dxf2dwg Safe? | Recommended? |
|---|---|---|---|
| Layer reassignment (Defpoints) | None | ✅ Yes | ✅ Yes |
| Visibility flag toggle | None | ✅ Yes | ✅ Yes |
| Text content edit | None | ✅ Yes | ✅ Yes |
| Entity deletion without repair | Dangling refs | ❌ No | ❌ No |
| ezdxf saveas on LibreDWG DXF | Corrupted refs | ❌ No | ❌ No |
