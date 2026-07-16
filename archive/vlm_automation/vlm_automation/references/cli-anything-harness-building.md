# Building a CLI-Anything Harness for the VLM-CAD Pipeline

This reference describes how to package the existing QCAD-VLM-automation pipeline as a reusable, agent-discoverable CLI using the CLI-Anything framework.

## Why do this

The ad-hoc scripts in `QCAD-VLM-automation` work for Hermes sessions but are hard for other agents (or future you) to reuse. CLI-Anything formalizes them into:

- A `click` CLI with `--json` output on every command.
- Persistent sessions and undo/redo.
- A `SKILL.md` that agents can read to discover commands.
- A PyPI-installable package (`pip install cli-anything-qcad`).

## End-to-end pipeline

The harness implements the same universal pipeline as the raw scripts:

1. **Ingest** PDF markup annotations.
2. **Convert** input DWG → working DXF (QCAD Pro or ODA File Converter).
3. **Classify** each annotation into a modification category.
4. **Route** to backend tier:
   - **T1** `ezdxf` — text changes, color/layer changes, deletions.
   - **T2** QCAD ECMAScript — moves, clones, reorders, block swaps, adds.
   - **T3** ODA round-trip — when DWG fidelity is required.
   - **T4** VLM + X11 — ambiguous / interactive instructions.
5. **Execute** edits on the working DXF/DWG.
6. **Verify** by rendering original + modified → pixel diff + VLM semantic check.
7. **Export** verified working DXF → output DWG.

## Modification categories

| Category | Default tier | Typical markup |
|----------|--------------|----------------|
| text_change | T1 | "Change X to Y" |
| delete | T1/T4 | "Remove clouded items" |
| move | T2 | "Move this to ..." |
| clone | T2 | "Copy row 3 to row 5" |
| reorder | T2 | "Move this row to second" |
| block_swap | T2 | "Replace BlockA with BlockB" |
| add | T2 | "Add a new label" |
| property_change | T1 | "Change color to red" |
| ambiguous | T4 | "Fix this" / "Adjust" |

## Package layout

```
cli-anything-qcad/
├── cli_anything/
│   └── qcad/
│       ├── qcad_cli.py              # click command tree
│       ├── core/
│       │   ├── categories.py        # category definitions + classifier
│       │   └── session.py           # per-job state
│       ├── pipelines/
│       │   └── markup_pipeline.py   # 7-stage orchestrator
│       ├── backends/
│       │   ├── dwg_converter.py     # DWG ↔ DXF
│       │   ├── ezdxf_backend.py     # T1
│       │   ├── qcad_ecma_backend.py # T2/T3
│       │   └── vlm_x11_backend.py   # T4
│       └── utils/
│           ├── pdf_parser.py        # PDF annotation ingest
│           └── visual_verify.py     # render + diff + VLM check
├── skills/cli-anything-qcad/SKILL.md  # agent discovery
├── setup.py
└── README.md
```

## CLI commands

```bash
cli-anything-qcad apply drawing.dwg markup.pdf -o drawing_modified.dwg --json
cli-anything-qcad dwg2dxf drawing.dwg working.dxf
cli-anything-qcad dxf2dwg working.dxf drawing.dwg
cli-anything-qcad parse markup.pdf --json
cli-anything-qcad render drawing.dwg --out preview.png
```

## Porting from QCAD-VLM-automation

Most scripts in `QCAD-VLM-automation` map directly into the new package:

- `pdf_annotation_parser.py` → `utils/pdf_parser.py`
- `tier_router.py` → `core/categories.py`
- `dxf_editor.py` → `backends/ezdxf_backend.py`
- `qcad_action_executor.py` + ECMAScript files → `backends/qcad_ecma_backend.py`
- `qcad_vlm_match.py` + `x11_controller.py` → `backends/vlm_x11_backend.py`
- `visual_verifier.py` + `vlm_verifier.py` → `utils/visual_verify.py`
- `audit_logger.py` → add to pipeline

**Important:** the mapping is a plan, not a guarantee that the scaffold already implements the logic. When results through the harness are worse than results from the legacy scripts, the harness backends are likely still stubs. Verify by comparing the harness backend code against the legacy script before debugging the drawing itself.

Recommended porting order (lowest risk, highest value):
1. `dxf_entity_lookup.py` + `dxf_editor.py` → `backends/ezdxf_backend.py` (T1 text/replace/delete/revision).
2. `qcad_action_executor.py` → `backends/qcad_ecma_backend.py` (T2 move/add/block-swap/reorder).
3. `visual_verifier.py` → `utils/visual_verify.py` (headless render, pixel diff, VLM compare).
4. `clone_pair3_v9.py` or later → `engines/terminal_clone.py` (Pair 3 row clone).
5. `qcad_vlm_verifier.py` → `backends/vlm_x11_backend.py` (T4 verification).

After each port, re-run the failing pair to confirm parity with the legacy script, then commit before moving to the next backend.

## Repository and install

Recommended repo name: `LIGHTSPEED1699/cli-anything-qcad`.

```bash
git clone https://github.com/LIGHTSPEED1699/cli-anything-qcad.git
cd cli-anything-qcad
pip install -e .
```

## Validation

After scaffolding, run the smoke test:

```bash
python3 -m pytest tests/test_imports.py
```

Then test commands with `--json` and validate output is parseable JSON.
