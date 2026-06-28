# Pair 3: DXF Cloning & QCAD ODA Conversion Lessons (2026-05-14)

This reference captures the **original V1–V3** cloning attempts using custom handle allocation (text-based) and QCAD ECMAScript (hardcoded handles). Both approaches were superseded by the **ezdxf dynamic discovery pattern** (`references/ezdxf-dynamic-clone-pattern.md`) validated in Pair 3 V6. This file is retained for historical context only — the ezdxf pattern should be used for all new terminal wire duplication tasks.

## Goal
Clone terminals T4/T5/T6 wire elements into T7/T8/T9 on drawing `3.dxf`, then convert to DWG while preserving all cloned entities and title-block revision history.

## Outcome
- `3_cloned_v2.dxf` — 39 entities cloned successfully into T7/T8/T9 positions (valid DXF)
- `3_FINAL_v2c.dwg` — QCAD ODA export **dropped all 39 clones** + **stripped BLOCK section**
- Cable-tag LWPOLYLINE (`CA-1452`) was the **only clone to survive**

## Root Causes

### 1. Handle Range Collision (even within 0xFFFF)
QCAD ODA DWG importer reassigns any handle not present in the original DWG. Cloned handles (`0x9C00`–`0x9C26`) were remapped to unused slots like `0x962F` and `0x9B46`, overwriting existing entities. On export, overwritten entities are silently dropped.

**Evidence:**
- V2c round-trip max handle = `0x9B3D` (original max)
- All clone handles (`0xB000`–`0xB026` after reassignment by QCAD) vanished from output
- Only LWPOLYLINE with simple vertices survived reassignment

### 2. BLOCK Section Stripping
QCAD ODA writer discards the entire BLOCKS/ATTDEF/ATTRIB section. Original `3.dwg` went from 314 KB to ~75 KB.

**Lost data:**
- BLOCK definitions: `PLAINS-D-CAN`, `Langtree`
- Title-block revision rows: REV 00, REV 01
- All ATTRIB entities inside block references

### 3. Tool Limitations Summary
| Tool | Can Clone | Preserves BLOCKs | Headless | Notes |
|------|-----------|------------------|----------|-------|
| **Text-based DXF editing** | ✅ Yes | N/A (DXF only) | ✅ Yes | Safe, fast, proven for deletion & clone |
| **QCAD ODA headless** | ❌ No | ❌ No | ✅ Yes | Drops clones + strips blocks |
| **QCAD GUI Save As** | ⚠️ Maybe | ⚠️ Maybe | ❌ No | Interactive only; unverified for clones |
| **LibreDWG dxf2dwg** | ⚠️ Unknown | ⚠️ Unknown | ✅ Yes | Corrupts AutoCAD 2018+ files; not tested |
| **ODA File Converter** | ✅ Yes (batch) | ✅ Yes (batch) | ⚠️ Needs xvfb | Needs `--export` flag + xvfb-run for headless |

## Text-Based DXF Cloning Script Pattern

```python
# clone_entities.py
# Reads DXF text, clones entities by handle, reassigns handles, applies dy offset

import re, sys

SAFE_BASE = 0x9C00  # Or reuse deleted handles (best option)
dy = -1.25
replacements = {
    "(4)": "(7)", "(5)": "(8)", "(6)": "(9)",
    "EPAC G1 14 H": "EPAC G1 16 H",
    "15 H": "17 H", "14 N": "16 N", "15 N": "17 N",
    "PLC21": "PLC22", "CA-1451": "CA-1452",
    "02732": "02733",
}

def clone_entity(block, new_handle, dy, replacements):
    block = block.replace(f"  5\n{old_handle}\n", f"  5\n{new_handle:04X}\n")
    # Apply dy to all Y coordinates (group code 20/30/42/50 where applicable)
    # ... (see full script in repo: /tmp/clone_v2_final.py)
    return block
```

**Key design decisions:**
- Reuse **deleted handles** from Pair 1/2 gap list rather than inventing new ones at `0x9C00+`
- Clone ATTRIB sub-entities along with parent INSERT blocks
- Apply text replacements BEFORE coordinate transformations to avoid number confusion
- Verify clone positions: T7 y=19.375, T8 y=19.125, T9 y=18.875 (dy = -1.25 from T6→T9)

## Coordinate Mapping (Confirmed for 1224×792 Landscape PDFs)
```
x_dxf = y_pdf / 72
y_dxf = (1224 - x_pdf) / 72
```
This is `swap_xy` with vertical flip. Confirmed across all 3 pairs.

## Recommendations for Future Clone Operations
1. **Before cloning:** Run existing deletion pipeline first to free up handle gaps
2. **Clone into freed handles:** Scan `1_v10_deleted.dxf` or similar for missing handle ranges; reuse them
3. **Never headless-convert cloned DXF through QCAD ODA:** GUI Save As only, or use ODA File Converter
4. **Verify interactively:** Open `*_cloned.dxf` in QCAD GUI before any DWG operation
5. **For revision history:** Work directly on the original `.dwg` with QCAD GUI; do not round-trip through DXF+ODA

## V3: Successful Clone with Safe Handle Range (2026-05-15)

After V2c dropped all clones, the root cause was identified as **QCAD ODA DWG reassigns handles it doesn't recognize in the original DWG**. The fix: use handles **below the original maximum handle** so QCAD preserves them.

### Four Critical Fixes

| Issue | V2 Failure | V3 Fix |
|---|---|---|
| **Handle range** | `0x9C00+` (above original max `0x9B3D`) → QCAD reassigns & drops | `0x5458–0x547E` (gap below max) → QCAD preserves |
| **Line endings** | LF-only (`\n`) → TrueView 2020 hangs | CRLF (`\r\n`) → TrueView loads correctly |
| **Consecutive `0` lines** | 160 consecutive `0` lines → structural corruption | 159 (same as original DXF) → structurally valid |
| **Z coordinate** | `-1.25` on entity Z → dropped/clipped by QCAD | `0.0` on entity Z → clones render correctly |

### V3 Script Design
```python
SAFE_BASE = 0x5458  # 21592, well below original max 0x9B3D (39741)
# 39 clones use 0x5458–0x547E (21592–21630), all inside QCAD's safe zone

dy = -1.25  # T4→T7, T5→T8, T6→T9 vertical offset

# Coordinate transformation: ONLY Y group codes (20, 42 for arc bulge Y, etc.)
# Z group code 30 must remain 0.0 — never apply dy to Z
```

### Verified Output
- `3_cloned_v3_fixed.dxf` — 738,086 bytes, 39 clones present, CRLF endings
- `3_FINAL_v3.dwg` — 77,585 bytes (vs 314,265 original), clones visible in TrueView
- All terminal wires T7/T8/T9 present, text replacements working

### Remaining Limitation
QCAD ODA export still **strips BLOCK definitions** (314 KB → 77 KB). Title-block revision history and ATTRIB entities are lost. This is an **inherent ODA writer limitation**, not fixable via DXF editing. For drawings requiring intact BLOCK/ATTDEF data, use QCAD GUI Save As or ODA File Converter, not headless ECMAScript export.

## Related Files in Workspace
- `/tmp/clone_v2_final.py` — V2 clone script (SAFE_BASE=0x9C00, dy=-1.25) — **DO NOT USE**, demonstrates handle collision
- `/tmp/clone_v3.py` — V3 clone script (SAFE_BASE=0x5458, dy=-1.25, CRLF, 0x00 Z) — **Proven working**
- `/tmp/qcad_convert_v9_simple.js` — QCAD ECMAScript conversion script (proven for non-clone DXFs)
- `~/.hermes/kanban/workspaces/testfiles_2026.05.07/3_cloned_v2.dxf` — intact clone output (V2)
- `~/.hermes/kanban/workspaces/testfiles_2026.05.07/3_cloned_v3_fixed.dxf` — V3 corrected DXF
- `~/.hermes/kanban/workspaces/testfiles_2026.05.07/3_FINAL_v3.dwg` — V3 working DWG
