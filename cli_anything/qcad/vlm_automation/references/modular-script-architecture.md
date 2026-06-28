# Modular Script Architecture for CAD/DWG Pipelines

The user explicitly prefers **separate single-purpose scripts per annotation type** rather than monolithic pipelines that mix operations. This isolates failure domains and preserves proven scripts from corruption by experimental additions.

## Script Classes

| Annotation Type | Script Class | Proven Tool | Example |
|----------------|-------------|-------------|---------|
| **Cloud deletion** | `cloud_deletion_pipeline.py` | ezdxf read + text-based delete + QCAD export | Pair 1, Pair 2 |
| **Text rename** | Raw byte search-replace | `sed`/Python string replace on DXF | Pair 2: TB-19→TB-21 |
| **Geometry edit** | Anchored coordinate replacement | Byte pattern match within entity block | Pair 2: RELAY 15 box height shrink |
| **Text addition** | QCAD ECMAScript `RAddObjectsOperation` | `new RTextEntity(doc, data)` | Pair 2: BLK label |
| **Entity duplication** | **(A) ezdxf discovery + `copy()`/`add_entity()`** | Dynamic geometry discovery with y-tolerance bands | Pair 3 V6: terminal wires 4/5/6 → 7/8/9 |
| **Entity duplication** | **(B) QCAD ECMAScript clone+offset** | `new RLineEntity(doc, newData)` + `RTextEntity` | Pair 3 V1–V5: wires 4/5/6 → 7/8/9 (deprecated — hardcoded handles) |
| **Revision table row** | Block editing (not yet proven) | Requires `INSERT` block attribute editing | Pair 3 pending |

## Key Rules

1. **Never add new functionality to a proven script** — e.g., never add entity creation to `cloud_deletion_pipeline.py`. Create a new script file instead.
2. **Chain scripts by saving intermediate files:** `original.dxf` → `deleted.dxf` → `deleted+rename.dxf` → `deleted+rename+height.dxf` → `+additions.dwg`.
3. **Each script handles ONE annotation type completely.** A script that tries to do deletion + rename + addition will corrupt when any single step fails, invalidating the entire pipeline.
4. **Proven scripts are sacred.** Once a script has been validated on a drawing pair, fork it for modifications rather than editing in-place. The original stays as a known-good reference.

## When to Choose Each Tool

- **Text-based DXF editing** (raw byte replacement): Use for renames, simple coordinate changes, and entity deletions by handle. Safe, fast, deterministic. Cannot add new entities.
- **ezdxf dynamic discovery clone**: Use for terminal wire duplication and any "same modification, different file" scenario. Discover source positions via landmark INSERTs/TEXT, collect entities with y-tolerance bands, clone with `copy()` + `add_entity()`, then apply text replacements. See `references/ezdxf-dynamic-clone-pattern.md` for full pattern.
- **QCAD ECMAScript**: Use for entity addition, and any operation that requires creating entirely NEW handles where ezdxf's auto-assignment is insufficient. Requires headless QCAD Pro.
- **ezdxf Python**: Use for analysis, entity counting, and verification. Read-only for LibreDWG DXFs — never use `doc.saveas()` on these (Pitfall #65).

## Pair-Specific Chaining

### Pair 2 (Delete + Rename + Resize + Add)
```
2.dwg → 2.dxf (LibreDWG)
2.dxf → cloud_deletion_pipeline.py → 2_deleted.dxf
2_deleted.dxf → text rename (TB-19→TB-21) → 2_deleted+rename.dxf
2_deleted+rename.dxf → height edit (RELAY 15 box) → 2_deleted+rename+d_height.dxf
2_deleted+rename+d_height.dxf → qcad_v9.js (add BLK) → 2_FINAL_V9.dwg
```

### Pair 3 (Duplicate + Text Rename)
```
3.dwg → 3.dxf (LibreDWG)
3.dxf → copy_entities.js (clone wires + rename labels) → 3_FINAL_V3.dwg
```

Note: Pair 3 has no cloud deletions, so the cloud pipeline is skipped entirely.
