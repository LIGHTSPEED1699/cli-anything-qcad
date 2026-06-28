# Production Test Data Reference — Pair 1/2/3

Location: `/home/hongbin/.hermes/kanban/workspaces/testfiles_2026.05.07/`

## Summary

Three real engineering drawing pairs (DWG + annotated PDF) used to validate the Phase B VLM-CAD pipeline.

| Pair | Drawing | PDF Markup | DXF | Entities | Layers | FreeText | Geometry Markers | Total |
|------|---------|-----------|-----|----------|--------|----------|-----------------|-------|
| 1 | 1.dwg | 1.pdf | 1.dxf | 218 | 14 | 7 | 23 | 30 |
| 2 | 2.dwg | 2.pdf | 2.dxf | 85 | 15 | 3 | 0 | 3 |
| 3 | 3.dwg | 3.pdf | 3.dxf | 227 | 15 | 2 | 0 | 2 |

**Only ~30% of annotations are actionable** FreeText annotations. The rest are decorative geometry (polygon clouds, leader lines).

## Layer Visibility Note (All Pairs)

All three DWGs produced by `dwg2dxf` → QCAD/TrueView exhibit the **layer visibility bug**: content is hidden on open until "Show All Layers" or thaw is applied. This is a **systemic artifact of the LibreDWG→DWG conversion workflow**, not file-specific corruption. See `references/layer-freeze-bug.md` for diagnosis and fixes.

**Critical update (2026-05-10):** `qcad_dxf2dwg_force_visible.js` is **not universally reliable** (Root Cause D). Pair 1 still opens with hidden layers even after the script. When this happens, the correct fix is **Option C: edit the original DWG directly** — import into QCAD, delete entities by handle, and export to a new DWG. This preserves the original's internal layer state. See `references/direct-dwg-deletion-pipeline.md`.

## Pair 1 — Hydrogen Peroxide Tank Level Transmitter

Junction box / instrument wiring drawing. 4 annotation clusters:
- **"delete clouded \nobjects"** (×2) — entity deletion inside cloud markers
- **"delete"** (×1) — single-word delete
- **"mark spare on both ends"** (×2) — wire end modification
- **24 polygon/line markers** — decorative revision clouds and leader lines

Key DXF text entities:
```
"Hydrogen Peroxide Tank Level Transmitter"
"SPAN 0-100 %"
"0-6.3 ft"
"4-20 ma", "+24V"
"Blk", "Wht", "Blu", "Red", "Yel", "Whi"
"CAB 27 PLC-PLT"
"R05-S03-P07-AI", "R05-S04-P00-AI"
```

**Deletion targets (from `1_deletion_log.json`):** 73 entities total — 69 TEXT, 3 ELLIPSE, 1 CIRCLE. Handles: `325B`, `325C`, `325D`, `34E8`, `34E9`, `4074`, `4151`, `4152`, `4672`, `4673`, `4674`, `4675`, `4676`, `4677`, `4678`, `4B84`, `4B87`, `3239`, `3240`, `3241`, `3242`, `3243`, `3244`, `3245`, `36DD`, `36DE`, `36DF`, `36E0`, `36E1`, `466D`, `466E`, `466F`, `4670`, `4671`, `4679`, `467A`, `467F`, `4680`, `4681`, `47C5`, `47C6`, `47C7`, `47C8`, `47C9`, `47CA`, `483A`, `48E0`, `48E2`, `4D70`, `4D71`, `4D72`, `4D74`, `4D75`, `4D76`, `36FC`, `3F84`, `4061`, `4062`, `41D3`, `4837`, `4B6B`, `36D2`, `36D3`, `36D4`, `36D5`, `36D6`, `36DB`, `36FA`, `36FB`, `3980`, `446E`, `4836`, `4B4D`.

Correct pipeline for Pair 1 (2026-05-10):
1. Original `1.dwg` (213 KB, all layers visible) is the source of truth
2. Run `test_handle_stability.py` on `1.dwg` to confirm handles survive DWG→DWG
3. Run `qcad_delete_entities_by_handle.js` with the 73 target handles → `1_MODIFIED_DIRECT.dwg`
4. Validate: open in QCAD/TrueView without "Show All Layers" — content must be visible
5. If handle stability test fails, use spatial+text matching fallback

**Do NOT use:** `1.dxf` → `fix_layer_visibility.py` → `qcad_dxf2dwg_force_visible.js` — this produces `1_MODIFIED_FIXED_VISIBLE.dwg` which still opens with hidden layers.

## Pair 2 — CABINET 16/25 Relay Panel

Panel layout drawing. 4 annotations:
- **[0]** "add BLK" — ambiguous (no target entity specified)
- **[1]** "Change to TB-21" ✅ **EXECUTED** — TB-19 → TB-21 @ (8.4, 7.8)
- **[2]** Polygon cloud around RELAY 15 area
- **[3]** "remove circled objects; then make the RELAY 15 box smaller" — complex multi-step

Key DXF text entities (verified editable):
```
"CAB 26 PLC-PPL" @ (13.6, 10.9)
"CABINET 16 FRONT" @ (2.3, 10.9)
"RELAY 15" @ (2.3, 8.0)
"TB-19" @ (8.4, 7.8) → changed to **"TB-21"**
"PAA-PPL-PERM" @ (13.7, 5.1)
"R02-S09-P28-DI" @ (13.6, 5.3)
```

## Pair 3 — Wire List / Table

Tabular wire list drawing. 4 annotations:
- **[0]** "copy wires to 4,5,6 to 7,8,9 and change related texts as PLC22, CA-1452, DWG B-SAR-280-02733" — complex multi-step copy
- **[1]** "02" — column marker, insufficient context
- **[2]** Line marker — no text
- **[3]** "Add new row: 01A, 2026/05/04, IFR" — table row insertion

## Generated Artifacts (per session)

Created by `test_real_data_pipeline.py` and `execute_and_review.py`:

| File | Size | Description |
|------|------|-------------|
| `2.dxf` | 498 KB | DXF with TB-21 edit (modified in place from 2_ORIGINAL.dxf) |
| `2_MODIFIED.dwg` | 236 KB | DWG re-converted from edited DXF via dxf2dwg |
| `2_ORIGINAL.dxf` | 541 KB | Backup of pre-edit DXF |
| `REVIEW_REPORT.pdf` | 7 KB | Consolidated summary PDF |
| `pipeline_report.json` | ~20 KB | Per-pair JSON results |
| `review_queue.db` | ~8 KB | SQLite human review queue |
| `audit_log.db` + `.jsonl` | ~16 KB | Tamper-evident audit logs |

## Running the Pipeline

```bash
cd ~/.openclaw/workspace/vlm-gui-automation

# Mock mode (fast, deterministic)
~/.hermes/venv/bin/python3 test_real_data_pipeline.py --pair all

# Live VLM (real Ollama calls, ~5 min for all FreeText annotations)
~/.hermes/venv/bin/python3 test_real_data_pipeline.py --pair all --live-vlm

# Execute edits (text replacement only)
~/.hermes/venv/bin/python3 execute_and_review.py --pair all --execute
```

## Reproduction Notes

- DWG→DXF uses LibreDWG: `/media/sdddata1/libredwg/bin/dwg2dxf`
- DXF→DWG uses LibreDWG: `/media/sdddata1/libredwg/bin/dxf2dwg -y`
- PDF annotation extraction uses PyMuPDF (`fitz`)
- DXF read/write uses `~/.hermes/venv/bin/python3` with `ezdxf`
- Live VLM calls use `http://localhost:11434` with `qwen2.5vl:latest`
- For simple text edits, prefer direct ASCII replacement in DXF to avoid `ezdxf` material table corruption on LibreDWG-generated files
