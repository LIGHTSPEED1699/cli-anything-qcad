# CLI-Anything QCAD

CLI-Anything harness for QCAD. Takes a DWG file and a PDF markup print, interprets the requested modifications, applies them through the most reliable backend, verifies the result visually, and returns a clean DWG.

## Pipeline

1. **Ingest** — parse PDF annotations into structured tasks.
2. **Convert** — input DWG → working DXF via QCAD Pro or ODA File Converter.
3. **Classify** — map each annotation to a modification category.
4. **Route** — pick backend tier:
   - **T1** `ezdxf`: text changes, deletions, property changes
   - **T2** QCAD ECMAScript: moves, clones, reorders, block swaps, adds
   - **T3** ODA round-trip: when DWG fidelity is required
   - **T4** VLM + X11: ambiguous / interactive instructions
5. **Execute** — edit the working DXF/DWG.
6. **Verify** — render original and modified files, run pixel diff + VLM semantic check.
7. **Export** — working DXF → output DWG.

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
- `click`, `ezdxf`, `pymupdf`, `Pillow`
- QCAD Professional (Linux) or ODA File Converter for DWG round-trip

## Status

This is a scaffold. The next step is porting the existing backend logic from `QCAD-VLM-automation` into `cli_anything/qcad/backends/` and `utils/`.
