# CLI-Anything QCAD

CLI-Anything harness for QCAD. Takes a DWG file and a PDF markup print, interprets the requested modifications, applies them through the most reliable backend, verifies the result visually, and returns a clean DWG.

## Pipeline

The pipeline in `cli_anything/qcad/pipelines/markup_pipeline.py` is now task-type driven rather than pair-specific.

1. **Ingest** — parse PDF annotations (FreeText + cloud polygons) into structured tasks.
2. **Convert** — input DWG → working DXF via QCAD Pro (`dwg2bmp`/`dxf2dwg`).
3. **Calibrate** — align PDF coordinates to DXF using text/geometry anchors.
4. **Classify** — hybrid rule-based (`core/categories.py`) + VLM (`gemma4:31b-cloud`) classifier maps each annotation to a reusable task type.
5. **Route** — task-type engine:
   - `delete_clouded_entities` — geometry-aware deletion inside clouded regions with terminal/title/ground protection
   - `change_text_value` / `add_text_label` — text/attribute operations
   - `clone_terminal_wires` — copy only wire geometry and labels between row bands; skips terminal INSERTs
   - `resize_bounding_box` — shrink a component box around a label
   - `mark_spare_wires` — add dashed HIDDEN rectangles around clouded spare areas
6. **Execute** — edit the working DXF, checkpointing after each task.
7. **Verify** — render original and modified files, run pixel diff + optional VLM semantic check.
8. **Export** — working DXF → output DWG.

## Task-type engines

| Engine | File | Purpose |
|---|---|---|
| `delete_clouded_entities` | `engines/delete_clouded_entities.py` | Delete geometry and text inside PDF cloud polygons while preserving terminals, ground, title block, and drawing borders. |
| `change_text_value` | `engines/text_value.py` | Replace TEXT/MTEXT/ATTRIB values (e.g. `TB-20` → `TB-21`). |
| `add_text_label` | `engines/text_value.py` | Insert a new TEXT/MTEXT label near a region or anchor. |
| `clone_terminal_wires` | `engines/clone_terminal_wires.py` | Clone wire geometry and labels between row bands without duplicating terminal INSERT blocks or creating duplicate arcs. |
| `resize_bounding_box` | `engines/extra_ops.py` | Shrink a closed LWPOLYLINE box around a component label. |
| `mark_spare_wires` | `engines/extra_ops.py` | Draw dashed `HIDDEN` rectangles around clouded spare regions. |

## Install

```bash
git clone https://github.com/LIGHTSPEED1699/cli-anything-qcad.git
cd cli-anything-qcad
pip install -e .
```

## CLI

```bash
# Apply PDF markups to a DWG
cli-anything-qcad apply drawing.dwg markup.pdf -o drawing_modified.dwg --json

# Convert DWG ↔ DXF
cli-anything-qcad dwg2dxf drawing.dwg working.dxf
cli-anything-qcad dxf2dwg working.dxf drawing.dwg

# Parse PDF annotations only
cli-anything-qcad parse markup.pdf --json

# Render a DWG to PNG
cli-anything-qcad render drawing.dwg --out preview.png
```

## Dependencies

- Python 3.10+
- `click`, `ezdxf`, `pymupdf`, `Pillow`, `matplotlib`
- QCAD Professional (Linux) for DWG round-trip

## Status

Reusable task-type pipeline is implemented and validated on Pairs 1, 2, and 3. The old pair-named executors have been removed; all logic now lives in the engines above.
