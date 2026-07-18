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

## Visual Verification (QCAD GUI + cua-driver)

The pipeline supports two visual verification modes:

**1. Headless pixel diff** (`cli_anything/qcad/utils/visual_verify.py`) — renders original and modified DWG via `dwg2bmp`, computes pixel difference map. Fast, no GUI required.

**2. VLM semantic verification** (`cli_anything/qcad/utils/visual_verifier.py`) — opens the DWG in **QCAD GUI with the AT-SPI bridge activated** (`QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`), captures a screenshot via **cua-driver** (background, no focus steal), and sends it to an Ollama vision model for semantic yes/no verification.

Key features of the cua-driver verifier:
- **AT-SPI bridge** — QCAD's bundled Qt 6.11.0 has the AT-SPI bridge compiled into `libQt6Gui.so.6`. The verifier launches QCAD with `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1` to activate it, making the full widget tree (menus, toolbars, scroll bars) visible via AT-SPI.
- **Background window detection** — uses `cua-driver call list_windows` instead of `xdotool search`, reliable even with QCAD's bash-wrapper → `qcad-bin` PID split.
- **No focus stealing** — screenshots are captured in the background via cua-driver's `get_window_state` with `screenshot_out_file`, no `windowactivate`/`windowraise`.
- **Cua-driver daemon** — requires `cua-driver serve` to be running (see [cua-driver docs](https://github.com/trycua/cua)).
- **VLM endpoint** — defaults to `http://localhost:11434` with `qwen2.5vl:latest`; override via `OLLAMA_URL` and `VISION_MODEL` env vars.

```bash
# Direct VLM verification via CLI
cli-anything-qcad verify output.dwg --question "Are the cloned terminal labels correct?"

# Pipeline uses this automatically when --per-task-vlm or default final verification is enabled
cli-anything-qcad apply drawing.dwg markup.pdf -o drawing_modified.dwg --per-task-vlm
```

## Recent Changes (July 2026)

### Drawing Profile Auto-Discovery (`c08411c`)

The pipeline is no longer tied to specific drawing conventions. A new `DrawingProfile` introspection module (`utils/drawing_profile.py`) automatically discovers a DXF's structure before any engine runs:

| Discovery | What it finds | Example (Pair 5) |
|-----------|--------------|-------------------|
| **Drawing extents** | Bounding box from all geometry + ATTRIB positions | `(-0.12, 33.67, 0, 17.69)` |
| **Revision table** | INSERT block with REV-tagged ATTRIBs, naming convention, subfield tags | `PLAINS-D-CAN`, `REV_{n}`, `REV_DATE_{n}` |
| **Terminal blocks** | Blocks with sequential integer ATTRIBs at uniform Y-spacing | `Wlltermn`/`TERMNUM`, 36 terminals, 0.25 spacing |
| **Protected blocks** | Terminal blocks + `GROUND`/`GND` | Automatically preserved during deletion |

**Engines updated to use the profile** (falling back to hardcoded defaults when discovery fails):

- `text_value.py` — revision row filler discovers block name, tag pattern, and subfield tags from profile
- `terminal_positions.py` — discovers terminal block names and ATTRIB tag instead of hardcoded `Wlltermn`/`TERMNUM`
- `delete_clouded_entities.py` — receives protected block names from profile via `MarkupPipeline`
- `vlm_verify_loop.py` — crop coordinate mapping uses actual drawing extents, not hardcoded `(0, 34, 0, 22)`
- `markup_pipeline.py` — generates profile after DWG→DXF and passes to every engine

**Verified on 3 drawings with different conventions:**
- Pair 1: `ATBASEB` block, `REV{n}` pattern (no date/descr subfields), no terminals
- Pair 3: No revision table found, `Wlltermn` terminals (36 count, 0.25 spacing)
- Pair 5: `PLAINS-D-CAN` block, `REV_{n}` pattern (full subfields), no terminals

### Screenshot Capture Fix (`b24a99e`)

The VLM verification loop's screenshot path had three bugs that caused it to silently fall back to DXF-only verification:

1. **Wrong window title search** — `xdotool` searched for `"modified"` in the window title; QCAD's title is `<filename>.dwg - QCAD Professional` which never contains "modified". Fixed: search for `"QCAD"`.
2. **Non-existent cua-driver tool** — `visual_verifier.py` called `cua-driver call screenshot`, which doesn't exist in cua-driver 0.7.0. Removed.
3. **cua-driver `get_window_state` hangs on QCAD AT-SPI tree** — QCAD publishes a massive accessibility tree with thousands of broken paths, causing cua-driver to timeout. Reordered to try `xdotool` + ImageMagick `import` first (fast, reliable on Qt6), with cua-driver as a last resort with a 10s timeout.

### VLM Evaluation Fixes (`eedfcca`)

- Fixed `CheckResult` constructor missing required `passed` argument
- Revision table crop now centers on the actual annotation region (from DXF coordinates) instead of a hardcoded position
- VLM evaluation splits compound expected values like `"B, 2026/07/10"` on comma and checks each part independently, since VLMs report revision fields in separate columns
- Crop fallback guard for inverted rectangles when crop center falls outside drawing extents

## Dependencies

- Python 3.10+
- `click`, `ezdxf`, `pymupdf`, `Pillow`, `matplotlib`
- QCAD Professional (Linux) for DWG round-trip

## Status

Reusable task-type pipeline is implemented and validated on Pairs 1, 2, and 3. The old pair-named executors have been removed; all logic now lives in the engines above.
