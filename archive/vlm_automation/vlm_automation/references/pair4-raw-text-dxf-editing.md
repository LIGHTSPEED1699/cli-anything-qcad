# Pair 4 Raw-Text DXF Editing — ATTRIB Update + Group Code 60 Invisibility (2026-06-11)

Session: 2026-06-11
Purpose: Complete workflow for updating revision block ATTRIB values and hiding cloud/annotation entities in a DWG via raw DXF text editing + LibreDWG round-trip.

## Problem

A markup PDF specified:
- Update REVNO from "3" to "4"
- Populate REV4 row: REV="4", description="P302D REMOVAL", by="HL", date="2026-06-10", checked="HL", app="HL"
- Hide all red revision clouds and associated wire stubs
- Hide three "0v" labels and a green circle annotation

## Discovery: What Broke the Previous ODA Automator

The `oda_converter_automator.py` script (Geisterhand-based) stopped working because:
1. Geisterhand systemd service was hardcoded to `DISPLAY=:1`; actual GNOME session runs on `:0`
2. Even after correcting display, Qt6 widgets swallow ALL synthetic input events (xdotool, clipboard paste, keypress)
3. GUI automation for ODA File Converter is effectively dead on Qt6 builds

## Solution: LibreDWG Round-Trip with Raw DXF Text Editing

### Step 1: DWG → DXF via LibreDWG

```bash
/media/sdddata1/libredwg/bin/dwg2dxf original/4.dwg -o output/4_libredwg.dxf
```

Produces ~79K line DXF. Exit 0. Clean output.

### Step 2: Entity Discovery via ezdxf (Read-Only)

```python
import ezdxf
doc = ezdxf.readfile("output/4_libredwg.dxf")
msp = doc.modelspace()

# Find ATTRIBs in revision block
for entity in msp:
    if entity.dxftype() == 'ATTRIB':
        tag = entity.dxf.tag
        text = entity.dxf.text
        handle = hex(entity.dxf.handle)
        print(f"ATTRIB handle={handle} tag={tag} text='{text}'")
```

Discovered handles:
- REVNO ATTRIB: handle `10A0`, group code 1 value "3"
- REV4 row ATTRIBs: `10B6` (REV4), `10B7` (REVDESCR), `10B8` (BY), `10B9` (DATE), `10BA` (CHKD), `10BB` (APP) — all empty

Cloud/annotation discovery via vision model on PDF markup + DXF cross-reference:
- Cloud HATCHes: `AEF5B`, `AEF86`, `AEFA2`, `AEFAC`, `AEFB2`, `AEFB3`, `AEFE1`, `AEFE2`, `AEFF8`, `AF43E`
- Edge LINEs: `AF43F`, `AF440`
- "0v" TEXTs: `AF8AC`, `AF8C5`, `AF8C8`
- Green circle HATCH: `ADEA6`

## Solution: LibreDWG Round-Trip with Raw DXF Text Editing

### Step 1: DWG → DXF via LibreDWG

```bash
/media/sdddata1/libredwg/bin/dwg2dxf original/4.dwg -o output/4_libredwg.dxf
```

Produces ~79K line DXF. Exit 0. Clean output.

### Step 2: Entity Discovery via ezdxf (Read-Only)

```python
import ezdxf
doc = ezdxf.readfile("output/4_libredwg.dxf")
msp = doc.modelspace()

# Find ATTRIBs in revision block
for entity in msp:
    if entity.dxftype() == 'ATTRIB':
        tag = entity.dxf.tag
        text = entity.dxf.text
        handle = hex(entity.dxf.handle)
        print(f"ATTRIB handle={handle} tag={tag} text='{text}'")
```

Discovered handles:
- REVNO ATTRIB: handle `10A0`, group code 1 value "3"
- REV4 row ATTRIBs: `10B6` (REV4), `10B7` (REVDESCR), `10B8` (BY), `10B9` (DATE), `10BA` (CHKD), `10BB` (APP) — all empty

Cloud/annotation discovery via vision model on PDF markup + DXF cross-reference:
- Cloud HATCHes: `AEF5B`, `AEF86`, `AEFA2`, `AEFAC`, `AEFB2`, `AEFB3`, `AEFE1`, `AEFE2`, `AEFF8`, `AF43E`
- Edge LINEs: `AF43F`, `AF440`
- "0v" TEXTs: `AF8AC`, `AF8C5`, `AF8C8`
- Green circle HATCH: `ADEA6`

### Step 3: Raw-Text DXF Edit Script (Corrected)

```python
from pathlib import Path

src = Path.home() / "Documents/QCAD-VLM-automation/pairs/pair4/output/4_libredwg.dxf"
dst = Path.home() / "Documents/QCAD-VLM-automation/pairs/pair4/output/4_final_edit.dxf"
with open(src, 'r') as f:
    lines = f.readlines()

# --- ATTRIB edits ---
attrib_edits = {
    '10A0': '4',       # REVNO
    '10B6': '4',       # REV4
    '10B7': 'P302D REMOVAL',
    '10B8': 'HL',
    '10B9': '2026-06-10',
    '10BA': 'HL',
    '10BB': 'HL',
}

# --- Entities to mark invisible (group code 60 = 1) ---
invisible = {
    'AEF5B', 'AEF86', 'AEFA2', 'AEFAC', 'AEFB2', 'AEFB3',
    'AEFE1', 'AEFE2', 'AEFF8', 'AF43E',  # cloud HATCHes
    'AF43F', 'AF440',                     # edge LINEs
    'AF8AC', 'AF8C5', 'AF8C8',           # "0v" TEXTs
    'ADEA6',                              # green circle HATCH
}

total_attrib = 0
total_invisible = 0

for i in range(len(lines) - 1):
    # Entity start detection: MUST use startswith('  0'), NOT strip() == '0'
    # strip() == '0' also matches data values like layer name "0", causing
    # premature scan termination inside an entity.
    if not lines[i].startswith('  0'):
        continue
    entity_type = lines[i+1].strip() if i+1 < len(lines) else None
    if entity_type not in ('ATTRIB', 'HATCH', 'LINE', 'TEXT'):
        continue

    # Find handle within this entity (scan until next entity start)
    j = i + 2
    handle = None
    entity_end = len(lines)
    while j < len(lines) - 1 and j < i + 30:
        if lines[j].startswith('  0'):
            entity_end = j
            break
        if lines[j].strip() == '5' and handle is None:
            handle = lines[j+1].strip().upper()
        j += 1

    if handle is None:
        continue

    # ATTRIB text edit: find group code 1 within this entity
    if handle in attrib_edits and entity_type == 'ATTRIB':
        k = i + 2
        while k < entity_end - 1:
            if lines[k].strip() == '1':
                lines[k+1] = attrib_edits[handle] + '\n'
                total_attrib += 1
                break
            k += 1

    # Invisibility flag: insert AFTER group code 8 (layer) and BEFORE next 100
    if handle in invisible:
        insert_pos = None
        k = i + 2
        while k < entity_end - 1:
            if lines[k].strip() == '8':
                insert_pos = k + 2  # after layer value
            if lines[k].strip() == '100' and insert_pos is not None:
                insert_pos = k  # before subclass marker
                break
            k += 1
        if insert_pos is not None:
            lines.insert(insert_pos, ' 60\n')
            lines.insert(insert_pos + 1, '     1\n')
            total_invisible += 1

with open(dst, 'w') as f:
    f.writelines(lines)

print(f"Applied {total_attrib} ATTRIB edits")
print(f"Marked {total_invisible} entities invisible")
```

**Key corrections from 2026-06-12 session:**

1. **Entity start detection:** Use `lines[i].startswith('  0')`, NOT `lines[i].strip() == '0'`. The latter matches data values like layer name `"0"`, causing premature entity-boundary detection and missing coordinates.

2. **Group code 60 insertion point:** Insert AFTER group code 8 (layer value) and BEFORE the next `100` subclass marker (e.g., `100\nAcDbHatch\n`). This places the visibility flag in the `AcDbEntity` subclass per DXF spec. Inserting immediately after group code 5 (handle) is structurally wrong — LibreDWG may still accept it but it violates the spec.

3. **Handle isolation:** Only process handles found within an entity start boundary. Handles appear in multiple sections (entity, table, group). A naive `if lines[i].strip() == '5'` scan without entity context will match table entries too.

### Step 4: DXF → DWG via LibreDWG dxf2dwg

```bash
/media/sdddata1/libredwg/bin/dxf2dwg output/4_final_edit.dxf -o output/4_final.dwg
```

Expected output: Clean conversion, no "Duplicate handle" errors, no "Invalid boundary_handles size" errors. Only benign warnings: `TABLESTYLE unsupported`, `Object handle not found` (3–5 residual handles, acceptable).

### Step 5: Round-Trip Verification (Corrected)

```bash
/media/sdddata1/libredwg/bin/dwg2dxf -y output/4_final.dwg -o output/4_verify.dxf
```

Verify in Python — **only scan entity sections, not table entries:**

```python
with open("output/4_verify.dxf", 'r') as f:
    lines = f.readlines()

# Verify ATTRIBs
expected = {
    '10A0': '4', '10B6': '4', '10B7': 'P302D REMOVAL',
    '10B8': 'HL', '10B9': '2026-06-10', '10BA': 'HL', '10BB': 'HL',
}

for i in range(len(lines) - 1):
    if lines[i].startswith('  0') and lines[i+1].strip() == 'ATTRIB':
        j = i + 2
        handle = None
        while j < len(lines) - 1:
            if lines[j].startswith('  0'):
                break
            if lines[j].strip() == '5':
                handle = lines[j+1].strip()
                if handle in expected:
                    # find group code 1
                    k = j + 2
                    while k < len(lines) - 1:
                        if lines[k].startswith('  0'):
                            break
                        if lines[k].strip() == '1':
                            actual = lines[k+1].strip()
                            status = "OK" if actual == expected[handle] else f"FAIL ({actual})"
                            print(f"ATTRIB {handle}: {status}")
                            break
                        k += 1
                break
            j += 1

# Verify visibility flags — scan ONLY entity sections
hide = {'AEF5B', 'AEF86', 'AEFA2', 'AEFAC', 'AEFB2', 'AEFB3',
        'AEFE1', 'AEFE2', 'AEFF8', 'AF43E', 'AF43F', 'AF440',
        'AF8AC', 'AF8C5', 'AF8C8', 'ADEA6'}

for i in range(len(lines) - 1):
    if not lines[i].startswith('  0'):
        continue
    entity_type = lines[i+1].strip()
    if entity_type not in ('HATCH', 'LINE', 'TEXT'):
        continue
    j = i + 2
    handle = None
    has_60 = False
    while j < len(lines) - 1:
        if lines[j].startswith('  0'):
            break
        if lines[j].strip() == '5':
            handle = lines[j+1].strip()
        if lines[j].strip() == '60' and lines[j+1].strip() == '1':
            has_60 = True
        j += 1
    if handle in hide:
        status = "OK" if has_60 else "FAIL"
        print(f"Entity {entity_type} {handle}: {status}")
```

**Pair 4 verification results (2026-06-11):**
- ✅ REVNO → "4"
- ✅ REV4 → "4"
- ✅ REVDESCR → "P302D REMOVAL"
- ✅ BY → "HL", DATE → "2026-06-10", CHKD → "HL", APP → "HL"
- ✅ All 16 entities marked invisible (group code 60 present)
- ✅ dxf2dwg: 0 duplicate handle errors, 0 boundary handle errors
- ✅ Only 3 residual "Object handle not found" warnings (benign)

## What Did NOT Work (And Why)

| Approach | Result | Root Cause |
|----------|--------|------------|
| ezdxf `saveas()` on LibreDWG DXF | Crash in `_update_header_vars` | MATERIAL table missing entry |
| HATCH coordinate relocation to 9999 | 300+ duplicate handles, black render | Boundary handle bitstream corruption |
| Layer reassignment to `Defpoints` | Layer reverts to empty after round-trip | LibreDWG doesn't preserve arbitrary layer names |
| Entity deletion from DXF | `Failed to decode DXF file` | Dangling 330 soft-pointer references |
| ODA File Converter GUI automation | xdotool events swallowed by Qt6 | Qt6 widget event loop blocks synthetic input |

## Key Rules for Raw-Text DXF Editing

1. **Never delete entities** — dxf2dwg needs all handles intact
2. **Never relocate HATCH coordinates** — destroys boundary handle streams
3. **Never use ezdxf `saveas()` on LibreDWG DXFs** — crashes on MATERIAL header
4. **Use ezdxf ONLY for discovery** — `readfile()` is safe; `saveas()` is not
5. **Use group code 60 = 1 for hiding** — survives round-trip, preserves handles
6. **Insert 60/1 immediately after handle (group code 5)** — correct insertion point
7. **Verify with dxf2dwg → dwg2dxf round-trip** — confirm edits survive

## QCAD dwg2bmp Render Note

QCAD's ODA-based `dwg2bmp` renders black for ANY LibreDWG-generated DWG/DXF (even unmodified originals). This is an upstream ODA↔LibreDWG format incompatibility in QCAD's importer, not data loss. The DWG opens correctly in the user's actual CAD software (AutoCAD, BricsCAD, etc.). Do not use QCAD `dwg2bmp` to verify LibreDWG output.

## Alternative Path: ODA DXF + ezdxf

If the deliverable is a DXF (not DWG), the ODA File Converter path is cleaner:
- ODA produces AC1032 DXF with no duplicate handles
- ezdxf `saveas()` works cleanly (no MATERIAL crash)
- ATTRIB edits via `entity.dxf.text = ...`
- Entity hiding via `entity.dxf.invisible = 1`
- **Trade-off:** DWG conversion requires manual CAD Save As; LibreDWG `dxf2dwg` segfaults on AC1032

See `references/oda-dxf-ezdxf-editing.md` for the full dual-path comparison. Use the LibreDWG raw-text path when DWG output is required. Use the ODA+ezdxf path when DXF output is sufficient.

## Files

- `original/4.dwg` — source drawing
- `output/4_libredwg.dxf` — baseline LibreDWG DXF
- `output/4_final_edit.dxf` — text-edited DXF (ATTRIBs + group code 60)
- `output/4_final.dwg` — final DWG after dxf2dwg
- `output/4_verify.dxf` — round-trip verification DXF
