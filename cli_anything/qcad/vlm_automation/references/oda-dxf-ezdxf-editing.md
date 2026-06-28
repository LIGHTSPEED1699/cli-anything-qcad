# ODA DXF + ezdxf Editing Workflow (2026-06-11)

**Validated workflow for editing DXFs produced by ODA File Converter using ezdxf `.saveas()` — clean, round-trip-safe for DXF, but DWG conversion requires ODA or manual CAD Save As.**

## Background

The Pair 4 pipeline (2026-06-11) explored three input sources for DXF editing:
1. **LibreDWG `dwg2dxf`** — produces R2004-compatible DXF with 300+ duplicate handles; ezdxf `saveas()` crashes on MATERIAL header; raw-text editing required.
2. **ODA File Converter GUI** — produces AC1032 (AutoCAD 2018) binary DXF; structurally clean with no duplicate handles.
3. **ODA File Converter GUI → manual Save As** — the ODA DXF is the intermediate.

This document covers workflow #2/#3: when you have an ODA-generated DXF and want to edit it with ezdxf.

## Why ODA DXFs Are Different

| Property | LibreDWG `dwg2dxf` | ODA File Converter |
|---|---|---|
| DXF version | R2004 (AC1015-ish) | AC1032 (AutoCAD 2018) |
| Duplicate handles | ~300–400 | 0 |
| OBJECTS section | Broken MATERIAL entries | Complete (MATERIAL, MLEADERSTYLE, etc.) |
| ezdxf `saveas()` | ❌ Crashes | ✅ Works cleanly |
| ATTRIB location | Nested in INSERT refs | Nested in INSERT refs (same) |
| `dxf2dwg` compatibility | ✅ LibreDWG accepts its own output | ❌ LibreDWG segfaults (AC1032 incompatible) |

## The Workflow

### Step 1: Obtain ODA DXF

```bash
# Method A: ODA File Converter GUI (human clicks Start)
cp original.dwg /tmp/oda_input/
# ... launch ODA GUI, user clicks Start ...
# Output appears in /home/hongbin/Downloads/
mv /home/hongbin/Downloads/original.dxf output/4_oda.dxf

# Method B: ODA File Converter CLI (if it works in your environment)
ODAFileConverter /tmp/oda_input/ /tmp/oda_output/ "ACAD2018" "DXF" "0" "1"
```

### Step 2: ezdxf Discovery (Read-Only)

```python
import ezdxf
doc = ezdxf.readfile("output/4_oda.dxf")
print(f"Version: {doc.dxfversion}")
print(f"Entities: {len(doc.entitydb)}")
print(f"Layers: {len(doc.layers)}")

# ATTRIB discovery — MUST use entitydb.values(), NOT query('ATTRIB')
for entity in doc.entitydb.values():
    if entity.dxftype() == 'ATTRIB':
        tag = entity.dxf.tag
        text = entity.dxf.text
        handle = hex(entity.dxf.handle)
        print(f"ATTRIB handle={handle} tag={tag} text='{text}'")
```

**Critical:** `doc.query('ATTRIB')` returns 0 entities on ODA DXFs because ATTRIBs are nested inside INSERT block references, not in top-level modelspace. Always iterate `doc.entitydb.values()` and filter by `dxftype()`.

### Step 3: ezdxf Edits

```python
# --- ATTRIB text edits ---
attrib_edits = {
    0x10A0: '4',           # REVNO
    0x10B6: '4',           # REV4
    0x10B7: 'P302D REMOVAL',
    0x10B8: 'HL',
    0x10B9: '2026-06-10',
    0x10BA: 'HL',
    0x10BB: 'HL',
}

for entity in doc.entitydb.values():
    if entity.dxftype() == 'ATTRIB':
        handle = entity.dxf.handle
        if handle in attrib_edits:
            entity.dxf.text = attrib_edits[handle]
            print(f"Updated ATTRIB {hex(handle)} → '{attrib_edits[handle]}'")

# --- Entity visibility (group code 60 = 1) ---
invisible_handles = {
    0xAEF5B, 0xAEF86, 0xAEFA2, 0xAEFAC, 0xAEFB2, 0xAEFB3,
    0xAEFE1, 0xAEFE2, 0xAEFF8, 0xAF43E,  # cloud HATCHes
    0xAF43F, 0xAF440,                      # edge LINEs
    0xAF8AC, 0xAF8C5, 0xAF8C8,            # "0v" TEXTs
    0xADEA6,                               # green circle HATCH
}

for entity in doc.entitydb.values():
    handle = entity.dxf.handle
    if handle in invisible_handles:
        entity.dxf.invisible = 1
        print(f"Marked {hex(handle)} ({entity.dxftype()}) invisible")
```

**Key insight:** ezdxf's `.dxf.invisible = 1` sets group code 60 = 1 cleanly. This works on ALL entity types (HATCH, LINE, TEXT, LWPOLYLINE, etc.) and survives ezdxf `saveas()` because ezdxf knows how to serialize the `invisible` property.

### Step 4: ezdxf Save

```python
doc.saveas("output/4_oda_edited.dxf")
```

**This works on ODA DXFs.** No crashes, no handle corruption, no MATERIAL header issues. The output DXF is structurally valid.

### Step 5: Verification (Render + Round-Trip)

```python
# matplotlib headless render for visual sanity check
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import ezdxf

doc = ezdxf.readfile("output/4_oda_edited.dxf")
msp = doc.modelspace()
fig, ax = plt.subplots(figsize=(14, 10), dpi=150)

for entity in msp:
    try:
        if entity.dxftype() == 'LINE':
            ax.plot([entity.dxf.start.x, entity.dxf.end.x],
                    [entity.dxf.start.y, entity.dxf.end.y],
                    color='black', lw=0.5)
        elif entity.dxftype() == 'LWPOLYLINE':
            pts = [(p[0], p[1]) for p in entity.get_points('xy')]
            if pts:
                ax.plot([p[0] for p in pts + [pts[0]]],
                        [p[1] for p in pts + [pts[0]]],
                        color='black', lw=0.5)
        elif entity.dxftype() == 'HATCH':
            for path in entity.paths:
                if path.path_type == ezdxf.const.BOUNDARY_PATH_EXTERNAL:
                    for edge in path.edges:
                        if edge.EDGE_TYPE == 'LineEdge':
                            ax.plot([edge.start[0], edge.end[0]],
                                    [edge.start[1], edge.end[1]],
                                    color='red', lw=0.8)
                        elif edge.EDGE_TYPE == 'ArcEdge':
                            # ... arc rendering ...
                            pass
        elif entity.dxftype() in ('TEXT', 'ATTRIB'):
            if not entity.dxf.invisible:
                ax.text(entity.dxf.insert.x, entity.dxf.insert.y,
                        entity.dxf.text, fontsize=6, ha='left', va='baseline')
    except:
        pass

ax.set_aspect('equal')
ax.invert_yaxis()
fig.savefig("output/4_oda_edited_render.png", dpi=150)
```

**Note:** matplotlib rendering of HATCH boundary edges shows where clouds used to be (red lines). In a real CAD viewer (AutoCAD, QCAD GUI, BricsCAD), invisible entities are completely hidden. The matplotlib render is a sanity check, not a pixel-perfect representation.

**Important:** When verifying ezdxf edits by re-reading the saved DXF, be aware that entity handles can appear in multiple DXF sections (ENTITIES, OBJECTS, tables). A simple `for entity in doc.entitydb.values()` scan is safe because entitydb only contains actual entities. But if you verify by scanning raw text lines, use entity-start boundary detection (`startswith('  0')` + entity type) to avoid matching handles in non-entity sections.

## Step 6: DWG Conversion (The Hard Part)

**LibreDWG `dxf2dwg` DOES NOT WORK on ODA AC1032 DXFs.** It segfaults with:
- `ERROR: Unknown DXF code 330 for HATCH`
- `ERROR: dwg_dynapi_entity_set_value: Invalid BLOCK_HEADER field`
- `Duplicate handle ...`
- `Segmentation fault (core dumped)` (exit 139)

**ODA File Converter CLI** may hang or timeout in non-interactive shells due to Qt platform initialization issues (even with `xvfb-run`).

**QCAD headless** (`-no-gui -exec`) times out — the ECMAScript APIs (`RApplication`, `RDocumentInterface`) are undefined in Qt6 headless mode.

**Working options for DWG conversion:**

| Method | Status | Notes |
|---|---|---|
| **Manual CAD Save As** | ✅ Always works | Open `4_oda_edited.dxf` in AutoCAD/QCAD/BricsCAD → File → Save As → DWG R2018 |
| **ODA File Converter GUI** | ⚠️ Semi-automated | Launch GUI, copy DXF to input folder, user clicks Start, agent polls output |
| **ODA File Converter CLI** | ⚠️ Environment-dependent | Works on some Linux setups; fails with Qt plugin errors on others |
| **LibreDWG `dxf2dwg`** | ❌ Never works | AC1032 is incompatible with LibreDWG's R2004 parser |

**Recommendation:** Deliver the edited DXF to the user with instructions to Save As DWG in their CAD software. The DXF is correct — the conversion gap is a tooling limitation, not a data problem.

## Comparison: LibreDWG vs ODA DXF Editing Paths

| Task | LibreDWG DXF Path | ODA DXF Path |
|---|---|---|
| Input source | `dwg2dxf original.dwg` | ODA File Converter GUI |
| ATTRIB edit method | Raw text (group code 1 replacement) | ezdxf `entity.dxf.text = ...` |
| Entity hide method | Raw text insert `60\n1\n` | ezdxf `entity.dxf.invisible = 1` |
| Save method | Raw text rewrite (NO ezdxf saveas) | ezdxf `doc.saveas()` ✅ |
| DWG conversion | `dxf2dwg edited.dxf` ✅ | Manual CAD Save As ⚠️ |
| Round-trip clean? | Yes (text-only edits) | Yes (ezdxf edits) |
| HATCH boundary safe? | Yes (group code 60, not coord move) | Yes (ezdxf invisible flag) |
| Handle corruption risk? | Low (no renumbering) | None (ezdxf handles AC1032 correctly) |
| Overall reliability | Good for DWG output | Good for DXF editing; DWG conversion manual |

## Key Rules

1. **For DWG→DWG pipelines (need DWG output):** Use LibreDWG path. Accept raw-text editing complexity. `dxf2dwg` produces valid DWG.
2. **For DWG→DXF pipelines (DXF output is sufficient):** Use ODA path. ezdxf editing is cleaner, code is simpler, verification is easier.
3. **Never mix paths:** Don't take an ODA DXF, edit with ezdxf, then try LibreDWG `dxf2dwg`. It will segfault.
4. **ATTRIB access:** On BOTH paths, ATTRIBs are nested in INSERTs. Use `doc.entitydb.values()` + `dxftype() == 'ATTRIB'`. `doc.query('ATTRIB')` fails silently (returns empty list).
5. **Invisibility is the correct hide method:** Group code 60 = 1 (raw text) or `entity.dxf.invisible = 1` (ezdxf). Never delete entities or relocate HATCH coordinates.

## Files (Pair 4 Session)

- `original/4.dwg` — source DWG
- `output/4_oda_edited.dxf` — ezdxf-edited ODA DXF (561 KB)
- `output/4_oda_edited_render.png` — matplotlib verification render
- `output/pair4_final_edited.dwg` — prior LibreDWG-path DWG (269 KB, from earlier session)

## See Also

- `references/pair4-raw-text-dxf-editing.md` — LibreDWG path (raw text editing, group code 60 insertion)
- `references/dxf2dwg-segfault-lessons.md` — Failure modes when `dxf2dwg` meets incompatible DXFs
- `references/oda-file-converter-gui-automation.md` — ODA GUI semi-automated workflow
- `references/pair4-oda-workflow-rule.md` — Earlier ODA-only rule (superseded by this dual-path analysis)
