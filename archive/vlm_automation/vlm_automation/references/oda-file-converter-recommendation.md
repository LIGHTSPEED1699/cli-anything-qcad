# ODA File Converter Recommendation for DWG↔DXF Pipeline Step

## Problem

QCAD's built-in ODA export (`qcad-bin -autostart export.js`) rebuilds DWG block structures from scratch during DXF→DWG conversion. As an authoring application, QCAD optimizes by stripping "unused" block metadata:
- ATTDEFs with no corresponding modelspace ATTRIB instances → discarded
- ATTRIB instances themselves → discarded (not primitive geometry)
- Title-block data, revision tables, sheet properties → lost

**Measured impact:** Original `3.dwg` (314 KB) → QCAD ODA export → `3_FINAL_v24.dwg` (76 KB). **76% data loss.**

## Solution

Replace the pipeline's DXF→DWG export step with the **ODA File Converter** (free standalone tool from Open Design Alliance).

## What ODA File Converter Does Differently

| Feature | QCAD ODA Export | ODA File Converter |
|---------|----------------|-------------------|
| Purpose | Authoring save | Format conversion |
| Block optimization | Rebuilds from scratch | Preserves as-is |
| ATTDEF/ATTRIB | Strips if "unused" | Preserves all |
| File size | Often smaller (data loss) | Same or larger (full fidelity) |
| Batch CLI | Limited | Full |

## Installation

Download from [opendesign.com](https://www.opendesign.com/guestfiles/oda_file_converter) (free registration required).

Linux version available as `.AppImage` or `.run` installer. No root required for AppImage.

## CLI Usage

```bash
# Syntax: OdaFC input output version "recurse" "audit"
OdaFC "input.dxf" "output.dwg" "ACAD2018" "0" "1"
```

Parameters:
- `ACAD2018` — target DWG version (ACAD2010, ACAD2013, ACAD2018, ACAD2024, etc.)
- `0` — recurse (0 = single file, 1 = directory)
- `1` — audit and fix errors (1 = yes, 0 = no)

## Pipeline Integration

Replace this step in the pipeline:
```bash
# OLD (QCAD ODA — strips block data)
qcad-bin -platform offscreen -autostart export.js input.dxf output.dwg
```

With this:
```bash
# NEW (ODA File Converter — preserves block data)
OdaFC "input.dxf" "output.dwg" "ACAD2018" "0" "1"
```

## Verification

After ODA File Converter:
1. `strings output.dwg | grep -i "REV_"` — should show tags
2. `ls -la output.dwg` — should be ~300KB, not ~76KB
3. Open in QCAD → single-click title block INSERT → Property Editor should show Attributes section
4. Edit `REV_DATE_1` → should accept changes (not revert to blank)

## When to Use ODA File Converter vs QCAD

| Scenario | Tool |
|----------|------|
| Need to preserve block attributes (title blocks, revision data) | **ODA File Converter** |
| Need to force layers ON after DXF edit (hidden layer bug) | QCAD + layer-fix script |
| Need to fix layer colors in DXF before export | QCAD or text-based script |
| Final export after all edits complete | **ODA File Converter** |
| Quick test / preview during pipeline iteration | QCAD (acceptable) |

## Hybrid Approach

Some pipelines may need **both** tools at different stages:

```
1. Original DWG → LibreDWG dwg2dxf → DXF
2. Edit DXF (ezdxf / text-based scripts)
3. Fix layer colors if needed (text-based script)
4. ODA File Converter DXF → DWG (preserves block data)
```

Note: LibreDWG `dwg2dxf` can produce a DXF from the original DWG. The edited DXF then goes through ODA File Converter for final DWG export.

## Limitations

- ODA File Converter is **free but requires registration** on Open Design Alliance website
- Command-line interface is **Windows-first**; Linux support exists but has fewer options
- Cannot run headless on a server without X11 unless using `xvfb-run`

## References

- `references/fix-1-insert-attrib-clone.md` — Alternative Fix 1: clone INSERT + ATTRIB children in pipeline
- `references/revision-block-attdef-lesson.md` — Full analysis of QCAD ODA data loss
- `references/attrib-injection-technique.md` — Failed attempt: injecting synthetic ATTRIBs
