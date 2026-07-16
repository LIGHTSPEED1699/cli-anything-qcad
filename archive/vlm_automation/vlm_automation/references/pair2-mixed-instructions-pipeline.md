# Pair 2 — Mixed Instructions Pipeline (2026-05-12)

Pair 2 has fundamentally different characteristics from Pair 1: a single cloud deletion
plus three non-deletion FreeText instructions. The pipeline needed extension beyond
cloud matching.

## Source Files
- **DWG:** `2.dwg` (AutoCAD R32 2018, ~213 KB)
- **PDF:** `2.pdf` (1224×792 portrait, 4 annotations)

## Annotations Extracted

| Type | Content | swap_xy DXF Position |
|------|---------|---------------------|
| Cloud (7 vertices) | — | x=[2.14, 4.67], y=[3.51, 5.03] |
| FreeText | "add 'BLK'" | PDF(544,244)-(600,378) → DXF(3.39-5.25, 7.56-8.33) |
| FreeText | "Change to TB-21" | PDF(556,614)-(588,756) → DXF(8.53-10.50, 7.72-8.17) |
| FreeText | "remove circled objects; then, make the RELAY 15 box smaller" | — |

## Pipeline Execution

### Phase 1: DWG→DXF Conversion
```bash
/media/sdddata1/libredwg/bin/dwg2dxf 2.dwg -o 2.dxf
```
Expected warnings (normal for AutoCAD-sourced DWGs):
- Unstable Class objects (TABLESTYLE, MATERIAL, MLEADERSTYLE)
- Skip HATCH common handles (short handle stream)
- Object handle not found (~10 handles in 800-object pool)

### Phase 2: Cloud Deletion (cloud_deletion_pipeline.py)
- **1 cloud detected** — LEFT-BOTTOM, x=[2.14, 4.67], y=[3.51, 5.03], height=1.51
- **85 entities indexed** — 26 TEXT, 32 LINE, 24 INSERT, 3 LWPOLYLINE
- **17 entities matched** — all Tier 1 (strict PIP), no boundary-touching
  - RELAY cluster: BLOCK inserts (21, 22, 24), TEXT labels (21, 22, 24, 24V)
  - 7 connecting LINE wires
  - 0 false positives, 0 false negatives
- **Deletion**: `delete_entities_text.py` — 17 deleted, 171 kept
- **Layer fix**: 15 negative colors → positive

### Phase 3: Non-Deletion Instructions

**Instruction A: TB-19 → TB-21**
Raw byte search-replace on the DXF: `b'TB-19'` → `b'TB-21'`. Single occurrence. Safe.

**Instruction B: Shrink RELAY 15 Box**
Target LWPOLYLINE handle 4396. Original vertices: (1.78125,7.90625), (3.53125,7.90625), (3.53125,3.40625), (1.78125,3.40625).
**User confirmed: height reduction from BOTTOM** (ymin 3.406→4.406), not width reduction.
Replace BOTH bottom y-coordinates: `\n 20\n3.40625\n` → `\n 20\n4.40625\n` (once per vertex).
Global replace safe — only one occurrence of 3.40625 in the entire file.
Post-fix: vertices all rectangular at ymin=4.40625, ymax=7.90625.

**Instruction C: Add "BLK" text**
**MUST use QCAD ECMAScript** — raw DXF byte insertion always corrupts (pitfall #100).
Proven pattern (from V8/V9):
- RTextData with setters: setPosition, setAlignmentPoint, setTextHeight(0.1), setText("BLK")
- HAlign can be HAlignLeft or HAlignCenter
- Layer: E-SYMB
- RAddObjectsOperation + di.applyOperation()
- Position baseline ABOVE the A1-13 horizontal line (y=7.625) — use y=7.65 to 7.75
- QCAD ODA converts to MTEXT; LibreDWG roundtrip drops MTEXT — trust QCAD console output only

### Phase 4: DWG Export
- Layer fix: 15 negative → positive (fix_layer_visibility.py)
- QCAD ODA: `qcad-bin -platform offscreen -no-gui -autostart script.js input.dxf output.dwg`
- The proven `/tmp/qcad_convert_v9_simple.js` pattern works for clean conversion
- QCAD text-addition scripts (V8/V9) may OOM with full layer-operations — use minimal scripts

## Version History

| Version | Changes | DWG Size |
|---------|---------|----------|
| V3 | 17 cloud deletions + TB rename + box width shrink (WRONG axis) | — |
| V5 | Fixed: box height shrink from bottom, BLK via raw DXF (corrupted) | — |
| V6 | BLK via QCAD ECMAScript (RTextData full ctor — silent fail) | — |
| V8 | BLK via proven setter pattern at (4.50, 7.575), box partially fixed | 40.0 KB |
| V9 | Box: both bottom y→4.40625 (rectangular). BLK at (5.725, 7.55→7.75) | 40.1 KB |

## Key Learnings (2026-05-12, updated from V9 session)

1. **Raw DXF byte insertion ALWAYS corrupts** on LibreDWG DXFs — text additions MUST use QCAD ECMAScript
2. **QCAD ODA exports RTextEntity as MTEXT** — LibreDWG roundtrip drops it, trust QCAD console only
3. **Coordinate text replacement MUST use anchored DXF group-code patterns** (`\n GC\nVALUE\n`)
4. **Confirm axis BEFORE geometry edits** — width vs height, which edge to move
5. **Box bottom fix needs BOTH vertices** — replace all y-coordinates with the old value, not just one
6. **Match font sizes exactly** — use `setTextHeight()` matching existing labels (0.1 for Pair 2)
7. **QCAD combined scripts may OOM** — keep add-BLK and convert scripts separate from layer operations

## Comparison: Pair 1 vs Pair 2

| Aspect | Pair 1 | Pair 2 |
|--------|--------|--------|
| Clouds | 4 (LEFT/RIGHT pairs) | 1 |
| Deletion targets | 102 handles (12 iterations) | 17 handles (1 pass, 100% auto) |
| Non-deletion | None | 3 instructions (add, rename, resize) |
| DXF format | ezdxf can parse | ezdxf fails (needs text-based) |
| Pipeline coverage | 100% automated | 50% automated, 50% text-based editing |
| DWG output | 45.8 KB | 40.4 KB |
