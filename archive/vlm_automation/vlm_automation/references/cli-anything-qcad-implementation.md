# CLI-Anything QCAD — Implemented Repository

This reference describes the actual scaffold and engine implementation in `LIGHTSPEED1699/cli-anything-qcad` as built in June 2026.

## Repository

- GitHub: `LIGHTSPEED1699/cli-anything-qcad`
- Package: `cli-anything-qcad` (`cli_anything.qcad`)
- Entry point: `cli-anything-qcad`
- Install: `git clone ... && cd cli-anything-qcad && pip install -e .`

## Commands

```bash
cli-anything-qcad apply drawing.dwg markup.pdf -o drawing_modified.dwg --json
cli-anything-qcad apply drawing.dwg markup.pdf -o drawing_modified.dwg --overrides overrides.json
cli-anything-qcad apply drawing.dwg markup.pdf --dry-run --json
cli-anything-qcad dwg2dxf drawing.dwg working.dxf
cli-anything-qcad dxf2dwg working.dxf drawing.dwg
cli-anything-qcad parse markup.pdf --json
cli-anything-qcad render drawing.dwg --out preview.png
cli-anything-qcad verify drawing.dwg --question "Are the F174 ground lines present?"
```

## Engine details

### CloudDeletionEngine

Ported from `cloud_deletion_pipeline.py`.

1. `extract_clouds(pdf_path)` — reads `Cloud` Polygon annotations via PyMuPDF with swap_xy mapping.
2. `_build_entity_index(dxf_path)` — indexes TEXT, MTEXT, LINE, CIRCLE, ARC, ELLIPSE, POLYLINE, LWPOLYLINE, INSERT, HATCH.
3. `_match_entities()` — 4-tier matching: strict PIP, bbox overlap, thin-cloud-bbox, boundary PIP.
4. Filtering: content sweep, label boxes, ground refs, arrow triangles.
5. `delete_handles()` raw DXF deletion by handle.
6. `fix_layer_visibility()` fixes negative layer color 62 values.

### TerminalCloneEngine

Ported from `clone_pair3_v13.py` and `dxf_clone_template.py`.

1. Find terminal rows by `Wlltermn` INSERT y-coordinates.
2. Discover source-row TEXT/MTEXT entities by y-tolerance.
3. Remove placeholder labels at target rows.
4. Clone source entities by raw DXF block with vertical offset.
5. Apply text replacements.

### QcadVlmVerifier

Ported from `qcad_vlm_verifier.py`.

1. Launch QCAD with DWG/DXF.
2. Find window via `xdotool` (sets DISPLAY=:0).
3. Screenshot via ImageMagick `import -window`.
4. Send PNG to Ollama vision model.
5. Parse YES/NO in first 50 chars.

### QcadRenderer

Fallback chain:
1. QCAD headless ECMAScript `RGraphicsViewQt::renderToImage` (fragile).
2. QCAD Pro `dwg2bmp` + ImageMagick convert — preferred headless rasterizer.
3. QCAD Pro `dwg2pdf` + ImageMagick convert.

### EzdxfBackend

- `replace_text`: substring replacement in TEXT/MTEXT.
- `delete_by_text`: delete TEXT/MTEXT matching keywords.

### QcadEcmaBackend

Generates ECMAScript on the fly for move, add LINE, swap block.

### VlmX11Backend

Uses `QcadVlmVerifier` for visual yes/no check on ambiguous instructions.

## File mapping from QCAD-VLM-automation

| Original file | New location |
|---------------|--------------|
| `pdf_annotation_parser.py` | `cli_anything/qcad/utils/pdf_parser.py` |
| `cloud_deletion_pipeline.py` | `cli_anything/qcad/engines/cloud_deletion.py` |
## Known limitations

- Prefer `dwg2bmp` over QCAD headless ECMAScript for image rendering.
- `QcadEcmaBackend.run_script()` currently passes only one argument; scripts expecting `getArgument(1)` need caller to supply output path or edit in-place.
- BLOCK/ATTDEF revision editing remains a hard boundary; see `vlm-cad-automation` skill for the forensic analysis.

## Porting vs. running ad-hoc scripts

**Critical lesson (2026-06-27):** The `cli-anything-qcad` scaffold and the legacy `QCAD-VLM-automation` scripts are NOT yet feature-equivalent. The scaffold's `QcadEcmaBackend`, `VlmX11Backend`, and the `markup_pipeline` routing logic were stubs as of June 2026. Pair 1, 2, and 3 produced better results when run directly from `QCAD-VLM-automation` than through the scaffold because the scaffold had not yet absorbed the real scripts.

When a user asks to "port the scripts" or asks why scaffold results are worse than the original scripts, the correct path is one of:
1. **Run the legacy scripts first** (fastest way to reproduce the good result).
2. **Incrementally replace scaffold stubs** with the real implementations from `QCAD-VLM-automation` (slower, but builds a reusable package).

Do not assume the scaffold already contains the logic just because the reference file mapping exists.

## Real source locations (June 2026)

- Legacy repo: `LIGHTSPEED1699/QCAD-VLM-automation`
- Key scripts:
  - `scripts/pair1_fixed_executor.py` — Polygon annotation text clearing for Pair 1.
  - `scripts/dxf_editor.py` — `DXFEditor` (text replace, revision block, keyword deletion).
  - `scripts/clone_pair3_v9.py` — row-based terminal wire cloning + text replacement.
  - `scripts/qcad_action_executor.py` — ECMAScript generation for move/add/block swap.
  - `scripts/qcad_vlm_verifier.py` — launch QCAD, screenshot, ask VLM yes/no.
  - `scripts/visual_verifier.py` — render + pixel diff + VLM semantic compare.
- The scaffold source lives in `LIGHTSPEED1699/cli-anything-qcad`.

## Tests

- `tests/test_unit.py`
- `tests/test_integration.py`

Run: `python3 tests/test_unit.py && python3 tests/test_integration.py`

## Related

- `vlm-cad-automation` skill — deep CAD/DWG/DXF pitfalls, VLM model selection, pair-specific lessons.
- `geisterhand-qcad` skill — X11/QCAD GUI automation for T4 fallback.


## Related

- `vlm-cad-automation` skill — deep CAD/DWG/DXF pitfalls, VLM model selection, pair-specific lessons.
- `geisterhand-qcad` skill — X11/QCAD GUI automation for T4 fallback.
