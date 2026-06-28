# Pair 4 ODA Workflow Rule — Revision Block Preservation

**Date:** 2026-06-10
**Context:** Pair 4 DWG revision-block editing (REVNO "3" → "4", REV4 row population, cloud entity deletion)

## The Failure

Used LibreDWG `dwg2dxf` → text-level DXF edit → LibreDWG `dxf2dwg` for a revision-block pipeline. Result:
- 300+ duplicate handles
- HATCH layer changes (Defpoints) lost on round-trip
- MATERIAL entries broken
- Revision block partially corrupted (REVNO updated, REV4 row fields not populated, ATTRIB handle references unresolved)
- Final DWG triggered AutoCAD TrueView Recover dialog

## The Fix

For ANY pipeline touching BLOCK, ATTDEF, ATTRIB, or title-block / revision-table data, there are two valid paths:

**Path A (DWG output required):**
```
Original DWG → LibreDWG dwg2dxf → raw-text ATTRIB edit + group code 60 → LibreDWG dxf2dwg → Valid DWG
```

**Path B (DXF output sufficient, cleaner editing):**
```
Original DWG → ODA File Converter → DXF → ezdxf ATTRIB edit + entity.dxf.invisible = 1 → ezdxf saveas → Clean DXF
```

LibreDWG is acceptable ONLY for:
1. Initial DWG analysis / inspection (`dwg2dxf` to dump contents)
2. Non-block geometry-only edits where TrueView validation is not required
3. Environments where ODA is genuinely unavailable and user accepts corrupt output

**Never mix the paths:** Do not take an ODA AC1032 DXF and try LibreDWG `dxf2dwg` — it will segfault. Do not use ezdxf `saveas()` on a LibreDWG DXF — it will crash. See `references/oda-dxf-ezdxf-editing.md` for the full dual-path comparison.

## Practical Semi-Automated Workflow

Because ODA File Converter uses Qt6 widgets that **swallow all xdotool synthetic events**, the Start button cannot be clicked programmatically. The agent handles setup; the user clicks Start.

### Step 1: Agent prepares environment
```bash
# Ensure screen won't lock during work
gsettings set org.gnome.desktop.screensaver lock-enabled false
gsettings set org.gnome.desktop.session idle-delay 3600

# Launch ODA with pre-configured folders
export DISPLAY=:0
export XAUTHORITY=/run/user/1000/gdm/Xauthority
~/.local/bin/ODAFileConverter
```
ODA remembers last-used input/output folders. Pre-configure once via the GUI, then subsequent runs reuse those paths.

### Step 2: Agent copies source file to input folder
```bash
cp pairs/pair4/original/4.dwg /tmp/oda_input/4.dwg
```

### Step 3: User clicks the Start button in the ODA GUI
This is the **manual step**. The ODA window is already open and sees the file in the input folder.

### Step 4: Agent polls for output and continues
```python
# Poll /home/hongbin/Downloads/ for 4.dxf
# Move to working directory when detected
```

### Step 5: Text-level DXF edit (fully automated)
```python
python3 edit_pair4_text.py output/4_oda.dxf > output/4_oda_edited.dxf
```

### Step 6: DXF → DWG via ODA (repeat steps 1-4)
Copy edited DXF to input folder, user clicks Start, agent polls for output DWG.

### Why this is acceptable
- The "manual" step is literally **one mouse click** (~2 seconds)
- ODA File Converter preserves BLOCK/ATTDEF/ATTRIB data perfectly
- The alternative (LibreDWG round-trip) corrupts revision blocks beyond repair
- For a production pipeline, wrap the human step in a simple "Click Start and press Enter" prompt

## Why Text-Level Editing Is Still Correct for Step 2

ezdxf `readfile()` + text-string manipulation (without `saveas()`) is the safest edit method for DXFs produced by ODA because:
- ODA DXFs do not have the 300+ duplicate handle problem
- No ezdxf round-trip means no handle renumbering risk
- Raw byte-level ATTRIB tag/value replacement preserves all cross-references

However, `saveas()` should still be avoided on LibreDWG-produced DXFs even though ODA DXFs are cleaner — the habit of text-level editing on all externally-produced DXFs is defensive.
