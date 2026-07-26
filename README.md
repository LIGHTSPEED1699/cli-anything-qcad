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
   - `mark_spare_wires` — find horizontal wires crossing the cloud/callout region, trace each wire to both terminal blocks, add SPARE text adjacent to the terminal label at both ends
6. **Execute** — edit the working DXF, checkpointing after each task.
7. **Verify** — render original and modified files, run pixel diff + optional VLM semantic check.
8. **Export** — working DXF → output DWG.

## Task-type engines

| Engine | File | Purpose |
|---|---|---|
| `delete_clouded_entities` | `engines/delete_clouded_entities.py` | Delete geometry and text inside PDF cloud polygons while preserving terminals, ground, title block, and drawing borders. |
| `change_text_value` | `engines/text_value.py` | Replace TEXT/MTEXT/ATTRIB values (e.g. `TB-20` → `TB-21`). |
| `add_text_label` | `engines/text_value.py` | Insert a new TEXT/MTEXT label near a region or anchor. Supports **batch mode** (add multiple labels beside existing text, e.g. Y521-Y536 beside 5-5-01 to 5-5-16) with **collision detection** that tries 6 candidate positions and skips labels that would overlap existing text. Also handles **revision row filling** by auto-discovering the title block's REV_N ATTRIB slots. |
| `clone_terminal_wires` | `engines/clone_terminal_wires.py` | Clone wire geometry and labels between row bands without duplicating terminal INSERT blocks or creating duplicate arcs. |
| `resize_bounding_box` | `engines/extra_ops.py` | Shrink a closed LWPOLYLINE box around a component label. |
| `mark_spare_wires` | `engines/extra_ops.py` | Find horizontal wires crossing the clouded (or callout-pointed) region, trace each wire to both terminal blocks, and add SPARE text adjacent to the wire's terminal label at both ends. Handles both cloud polygon annotations (wide region) and callout arrows (narrow region → arrow tip point probe). |

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

### Agent-Assisted Retry + Parameter Overrides (`a193857`)

The pipeline now supports **agent-assisted parameter tuning** for failed VLM verification and user redo feedback:

- `MarkupPipeline.run(overrides=...)` accepts a dict mapping task_id to parameter deltas (e.g. `{"t002": {"tolerance": 1.5}}`). Overrides are applied to `task.parameters` before execution.
- The DWG Portal backend (`dwg-portal-backend/server.py`) has two trigger paths:
  1. **Redo with feedback** — `_redo_pipeline()` calls an LLM agent (glm-5.2:cloud via Ollama) to translate the user's natural-language feedback into per-task parameter overrides, then re-runs the pipeline with those overrides.
  2. **VLM verification failure** — `_execute_pipeline()` calls the agent with VLM failures + task list, re-runs with overrides, re-verifies. Loops up to `AGENT_MAX_ITERATIONS` (default 3).
- The agent never touches engine code. It only adjusts existing parameters (tolerance, label_offset, threshold, etc.) via a structured prompt with per-task-type parameter hints.
- Config via env vars: `DWG_PORTAL_AGENT_MODEL`, `DWG_PORTAL_AGENT_MAX_ITERATIONS`, `DWG_PORTAL_AGENT_TIMEOUT`.

### MarkSpareWiresEngine Implementation (`a193857`)

The `mark_spare_wires` engine was previously a pass-through stub. Now implemented:

1. Extract the cloud polygon from the task's DXF region.
2. Find horizontal wires (2-vertex POLYLINEs/LINEs) whose Y falls within the cloud's Y-bbox and whose X-span crosses the cloud.
3. For each wire, find the terminal box (5-vertex POLYLINE rectangle) at each endpoint.
4. Inside each terminal box, find the TEXT entity closest to the wire's Y.
5. Add "SPARE" text adjacent to each terminal label, matching its height, style, and layer.

**Callout arrow fallback:** when the region is narrow (< 2.0 DXF units, e.g. a callout arrow without a nearby cloud), the arrow tip (last vertex) is used as a point probe to find the nearest horizontal wire, then traced to both terminal blocks.

Verified against real instrument loop drawings: correctly identifies F176/A233 and F172/B239 terminal label pairs at both wire ends.

### Collision Detection for Batch Labels (`1cc102d`)

The `add_text_label` engine's batch mode previously placed labels at `target.x - 1.5 * text_height` without accounting for the label's own width, causing new labels to overlap the very text they were placed beside. This was caught on a real drawing where Y521-Y536 labels collided with existing 5-5-XX wire labels.

**Fix:**
- Added `_text_bbox()` and `_check_text_collision()` helpers that compute TEXT/MTEXT bounding boxes and test rectangle intersection with a margin.
- `_add_batch_labels()` now tries 6 candidate positions (left, right, above, below, far_left, far_right) and picks the first that doesn't collide with any existing text entity.
- Labels that can't be placed without collision are reported as `blocked` in the pipeline report instead of silently overlapped.

### Closed-Loop VLM Verification (`vlm_verify_loop.py`)

A new verification module (`utils/vlm_verify_loop.py`) wraps the pipeline in a retry loop:

1. After each pipeline run, generate **zoomed crops** (4x upscale) of each task's target region — never full-screen screenshots (VLMs hallucinate on full pages).
2. Query the VLM (`gemma4:31b-cloud`) with **neutral questions** ("list all text you see" not "is X deleted?").
3. Parse pass/fail for each modification request.
4. If any check fails, diagnose the root cause via DXF entity inspection and apply targeted fixes.
5. Re-run the pipeline with fixes applied. Repeat until all checks pass or max iterations (5) reached.
6. Falls back to **DXF-level verification** if VLM is unreachable or QCAD screenshots fail.

Also adds `overlap_check` task type to `_dxf_verify()` — scans all TEXT/MTEXT entities for bounding-box intersections to detect overlapping labels structurally.

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

Reusable task-type pipeline is implemented and validated on Pairs 1, 2, and 3. All 11 engines are implemented (delete, text change, add label, batch labels, clone terminal wires, cloud clone, text-based clone, resize bounding box, mark spare wires, add dimension, add leader, move entity). The old pair-named executors have been removed; all logic now lives in the engines above.

Agent-assisted retry is implemented in the DWG Portal backend for VLM verification failures and user redo feedback.
