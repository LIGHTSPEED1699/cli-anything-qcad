# VLM Visual Verification (Screenshot-Based)

A working VLM visual verification pipeline was prototyped using `qwen2.5vl` via the local Ollama API. This is **not** the full 4-tier pipeline described in the main SKILL.md — it is a standalone screenshot-analysis approach for validating DWG/DXF output.

## When to Use

- You have a DWG/DXF file and need a second pair of "eyes" to check it
- The user has TrueView and sees issues the programmatic checker missed
- You want to compare original vs. modified drawings side-by-side
- Terminal labels, wire routing, or text placement need visual validation

## Working Prototype (Session: 2026-05-16)

### 1. Render DXF to PNG
```python
import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, matplotlib
import matplotlib.pyplot as plt

def render_dxf_to_png(dxf_path, png_path, figsize=(24, 16), dpi=200):
    doc = ezdxf.readfile(dxf_path)
    fig = plt.figure(figsize=figsize)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    ax.axis("off")
    ctx = RenderContext(doc)
    out = matplotlib.MatplotlibBackend(ax)
    frontend = Frontend(ctx, out)
    frontend.draw_layout(doc.modelspace(), finalize=True)
    fig.savefig(png_path, format="png", bbox_inches="tight", pad_inches=0.02, dpi=dpi)
    plt.close(fig)
```

### 2. Send to VLM via Ollama
```python
import requests, base64

def vlm_analyze_drawing(png_path, prompt, model="qwen2.5vl:latest",
                         url="http://192.168.2.15:11434/api/generate", timeout=120):
    with open(png_path, "rb") as f:
        img_data = base64.b64encode(f.read()).decode("utf-8")
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [img_data],
        "stream": False,
        "options": {"num_ctx": 8192, "temperature": 0.3}
    }
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json().get("response", "")
```

### 3. Prompt Engineering for CAD
- Provide **context** about intended modifications
- Ask **specific questions** about terminal rows, wire counts, label duplicates
- Request **structured output** (GOOD / NEEDS_WORK / ERROR with explanation)
- For side-by-side comparisons, explicitly label LEFT=original, RIGHT=modified

### Example Prompt
```
You are reviewing an updated electrical CAD drawing screenshot.
The original had terminals 3,4,5,6 with wires.
The update clones wire geometry from terminals 4-6 to new terminals 7-9.

PAY SPECIAL ATTENTION to terminals 4 through 9:
1. Do you see terminal labels (4), (5), (6), (7), (8), (9)? Any duplicates?
2. Do the wires for 7-9 look like clones of 4-6 wires?
3. Any anomalies, extra labels, or missing elements?
```

## Key Pitfall: Renderer vs. TrueView Gap

The `ezdxf` matplotlib backend does NOT render all entities with the same fidelity as AutoCAD TrueView or QCAD. Line weights, hatch patterns, and some block attributes may be missing or visually different.

**Consequence:** A VLM analyzing the rendered PNG may declare "GOOD" while TrueView shows errors.

**Mitigation:**
1. Use VLM verification as a **screening step**, not final approval
2. Generate **zoomed crops** (not just full-page renders) for detail inspection
3. Ask the VLM specifically about **text labels** and **wire segment counts** — these are usually rendered correctly
4. Always request user TrueView confirmation for the final DWG

## Key Pitfall: VLM Can Miss Subtle Structural Errors

The VLM spotted terminal labels correctly (7, 8, 9, 10) but did NOT identify that the underlying DXF had been corrupted by wrong-handle cloning. Visual analysis validates appearance, not data structure.

**Mitigation:** Combine VLM visual checks with programmatic entity-level verification (handle integrity, y-position mapping, entity counts per zone).

## QCAD Screenshot + VLM Verification (Geisterhand Pipeline)

An alternative to matplotlib rendering is opening the DWG directly in QCAD, screenshotting the live CAD view, and querying a VLM. This has different tradeoffs than the matplotlib approach.

### Pipeline
```
DWG → QCAD GUI (launched via Geisterhand) → Ctrl+E zoom extents
  → ImageMagick `import -window` screenshot → Ollama vision model (qwen2.5vl)
```

**Script:** `scripts/qcad_vlm_verifier.py` in the `geisterhand-qcad` skill (also synced to `scripts/qcad_vlm_verifier.py` in this repo).

### Usage
```bash
# Verify specific features
python3 scripts/qcad_vlm_verifier.py drawing.dwg \
    --question "Are the two short ground-reference lines on the right side of F174 present?"

# Calibration mode (screenshot only, no VLM)
python3 scripts/qcad_vlm_verifier.py drawing.dwg --calibrate

# Keep QCAD open for manual inspection
python3 scripts/qcad_vlm_verifier.py drawing.dwg --question "..." --keep-qcad
```

### Environment
- `QCAD_BIN` — path to `qcad` launcher (not `qcad-bin`; wrapper handles Qt platform)
- `OLLAMA_URL` — default `http://192.168.2.15:11434`
- `VISION_MODEL` — default `qwen2.5vl:latest`
- `GEISTERHAND_URL` — default `http://127.0.0.1:7680`
- `DISPLAY`, `XAUTHORITY`, `DBUS_SESSION_BUS_ADDRESS` — must be set for non-interactive shells

### QCAD vs. Matplotlib Rendering Comparison

| Aspect | QCAD Screenshot | Matplotlib (ezdxf) |
|---|---|---|
| Fidelity | True CAD rendering (line weights, fonts, hatches) | Simplified (line weights missing, some entities skipped) |
| Speed | ~8s (launch + zoom + screenshot) | ~2s (pure Python) |
| Headless | Requires X11 session + Geisterhand | Works anywhere with `Agg` backend |
| Zoom control | Live zoom/pan via key commands | Static bounding-box based |
| Scalability | One file at a time | Batch-render many files |
| Text readability | Native CAD fonts | Matplotlib fonts, may differ |

### VLM Accuracy Finding (2026-05-27)

Tested against V10 DWG (1_FINAL_v10.dwg) querying F174 ground-reference lines:

- **Core question accuracy:** VLM correctly answered **YES** — lines are present.
- **Detail hallucination:** VLM claimed labels are "24v" and "F73" — these are nearby TEXT entities, not line labels. Vision models cannot reliably read small CAD text at full-zoom resolution.
- **Programmatic cross-check:** ezdxf search within 2 units of F174 found:
  - LINE h=4B6D (horizontal, 0.236 units) — **kept in V10**
  - LINE h=4B6C (vertical, 0.244 units) — **kept in V10**
  - TEXT "F73" at (9.488, 9.025) — kept
  - TEXT "24v" at (9.496, 9.232) — kept
  - **Zero entities within 2 units of F174 were deleted in V10**

**Rule:** VLM visual verification is good for "are these lines present?" but bad for "what are their labels?" Always cross-check with programmatic entity search for precision.

### Calibration Mode

Before trusting VLM results, run `--calibrate` once per drawing type:

```bash
python3 scripts/qcad_vlm_verifier.py drawing.dwg --calibrate
```

This launches QCAD, zooms extents, and saves a screenshot. Review the PNG to verify:
1. Text labels are readable (not pixelated)
2. The drawing fills the viewport (not zoomed too far out)
3. No dialog boxes or tooltips are covering the drawing

If text is unreadable, increase `--wait` seconds (default 3) to let QCAD finish loading fonts.

## Session-Specific Discoveries

### Pair 3 V7 Failure (2026-05-16)
The VLM declared V7 "GOOD" — no duplicate labels, wires cloned correctly — but TrueView showed five errors:
1. Cloning applied to 8-10 instead of 7-9
2. Wrong source terminal used (T4 instead of T5 as first wire)
3. Wire labels (B), (W) not copied
4. GND label overlapping with other text
5. Partial deletion of T7 wires

**Root cause:** The V7 script cloned **BLOCK DEFINITION** handles (y≈10.0 in BLOCKS section) instead of **instantiated wire geometry** handles in the ENTITIES section. The VLM saw a visually coherent screenshot because the wrong clones were still wires, just in wrong positions.

**Lesson:** VLM visual verification cannot catch handle-level data corruption. Always pair with programmatic handle verification.
