# dwg2bmp — The only working headless DWG rasterizer

## Why this exists

The `vlm-cad-automation` skill has a "Headless Rendering Reality" pitfall that
says all headless renderers are broken. **As of 2026-06-04, this is no longer
true.** QCAD Pro ships a `dwg2bmp` CLI that produces a valid PNG of
any DWG/DXF in ~3 seconds, with no X11, no Qt platform plugin, no ODA File
Converter needed. This is the validated pattern for VLM verification images.

## The other renderers (and why they don't work)

| Renderer | Failure mode |
|---|---|
| `qcad-bin -platform offscreen` | Exits 0 but produces no PNG; ECMAScript `exportImage()` silently fails |
| `qcad` wrapper script | Hardcodes `-platform xcb`, can't be overridden with `-platform offscreen` |
| `LibreCAD dxf2pdf` | Outputs valid PDFs for some files but writes them to the **input directory** (ignores `-o`); produces ~1.6 KB blank PDFs for others |
| `ODAFileConverter` | Crashes with Qt platform plugin missing (`offscreen` unavailable); `xvfb-run` times out after 180s |
| `ezdxf + matplotlib Agg` (after LibreDWG `dwg2dxf`) | **Blank PNG (1 unique color)** because materials/MATERIAL objects strip line widths/colors |
| `ezdxf + matplotlib Agg` (clean DXF, no round-trip) | ✅ Works, but only for DXFs that haven't been round-tripped through LibreDWG |

`dwg2bmp` is the only one that works for **DWG inputs without dependencies**.

## Usage

```bash
# Setup
export QCAD_DIR=$HOME/opt/qcad
export LD_LIBRARY_PATH=$QCAD_DIR:$LD_LIBRARY_PATH
# Optional: kill any lingering QCAD instances
pkill -9 -f qcad-bin 2>/dev/null
sleep 1

# Render DWG/DXF → PNG
timeout 60 $QCAD_DIR/dwg2bmp \
    -f \                  # force overwrite
    -a \                  # enable antialiasing
    -o /tmp/output.png \  # output file
    /path/to/input.dwg    # input (DWG or DXF both work)

# Check result
ls -la /tmp/output.png
# Typical: 30-60 KB PNG, 637x455 or 1920x1080 depending on aspect ratio
```

### CLI flags (from `dwg2bmp -h`)

```
-a, -antialiasing       Enable antialiasing
-b, -background=C       Set background color (e.g. 'white' or '#ccdd00')
    -block=BLOCK_NAME   Block/layout to output (default: *Model_Space)
-c, -color-correction   Prevent white-on-white or black-on-black painting
-d, -recompute-dim      Recompute empty dimension blocks
-f, -force              Overwrite existing output file
    -flat               Flatten to 2D (Z==0)
-fs, -font-substitution FONT1 FONT2
```

### Known warnings (benign)

```
Warning: TODO TABLESTYLE r2010+ missing fields
Warning: Unstable Class object 505 MLEADERSTYLE (0xfff)
Warning: Unstable Class object 502 MATERIAL (0x481)
Warning: Unhandled Object TABLESTYLE in out_dxf
```

These are all about OBJECTS section fields that don't have a PNG rendering
analog. Ignore them.

## Verified use case: VLM verification

This is the canonical use case: take a `1_FINAL_vN.dwg`, render it, send the
PNG to a vision model, ask "are the F174 ground-reference lines present?".

```python
import subprocess, os
from pathlib import Path

def render_dwg_to_png(dwg_path: Path, png_path: Path, timeout: int = 60) -> Path:
    """Headless DWG → PNG using QCAD's dwg2bmp."""
    qcad_dir = os.path.expanduser("~/opt/qcad")
    env = {**os.environ, "LD_LIBRARY_PATH": f"{qcad_dir}:{os.environ.get('LD_LIBRARY_PATH','')}"}
    subprocess.run(
        ["timeout", str(timeout), f"{qcad_dir}/dwg2bmp",
         "-f", "-a", "-o", str(png_path), str(dwg_path)],
        env=env, check=True, capture_output=True
    )
    if not png_path.exists() or png_path.stat().st_size < 1000:
        raise RuntimeError(f"dwg2bmp produced empty/blank PNG: {png_path}")
    return png_path
```

## Why this matters for VLM-CAD pipeline

The VLM-CAD pipeline runs visual verification gates after every editing step
("did the deletion miss anything?", "are the F174 ground lines still there?").
Before `dwg2bmp`, these gates were either:
- Skipped (relying on programmatic verification only — caught the F174
  hallucination too late)
- Done by opening QCAD in X11 + screenshot via xdotool (requires interactive
  display, slow, fragile)

With `dwg2bmp`:
- Headless: no X11 needed
- Fast: 3s per render
- Reliable: same engine that QCAD GUI uses, full fidelity
- Pipeline-compatible: can be called from Python without subprocess shell

## Size and resolution

Default resolution: matches the DWG's drawing area. For a typical schematic
(17×11 DXF units), output is ~637×455 pixels, ~30 KB. For larger drawings,
output scales up proportionally.

For most VLM verification tasks, default resolution is fine. The 30 KB PNG
fits well within Discord's 8 MB attachment limit and tokens to ~300 input
tokens in the vision model.

## Failure modes

1. **"Cannot load library"** — missing `LD_LIBRARY_PATH`. Always set it.
2. **"No input file"** — `dwg2bmp` was called without an input file argument.
3. **Empty/blank PNG** — should never happen for a valid DWG. If it does,
   check that the DWG is not corrupted (try opening in QCAD GUI first).
4. **"Application already running"** — previous QCAD instance lingering. Run
   `pkill -9 -f qcad` before retry.

## Provenance

Validated 2026-06-04 on:
- `1_FINAL_v11.dwg` (46.8 KB, 5 pairs of L-shaped ground lines, 1136 entities)
- Output: 30.4 KB PNG, 637×455, 3s render time
- All entities (TEXT, LWPOLYLINE, LINE, INSERT, ELLIPSE) rendered with
  correct colors and line weights

Used as the test image source for the gemma4 vision benchmark
(`references/vlm-model-comparison-2026-06.md`).
