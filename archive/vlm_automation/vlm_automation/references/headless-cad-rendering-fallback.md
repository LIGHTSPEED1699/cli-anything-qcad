# Headless CAD Rendering Failure & ezdxf+matplotlib Fallback

## 2026-06-04 Update: There's a better headless renderer

The original claim "the only reliable headless rendering path in this environment is matplotlib's Agg backend" is still true for **pixel-diff gates** (which need deterministic monochrome line art), but for **VLM verification** there's a much better option: QCAD's bundled `dwg2bmp` CLI.

`dwg2bmp` produces true-color, true-width, block-aware renders that match what the user sees in QCAD's window — and it runs headless in 3 seconds. **This is the renderer you should use for VLM verification.**

```bash
QCAD=<qcad-install-dir>
LD_LIBRARY_PATH=$QCAD:$LD_LIBRARY_PATH $QCAD/dwg2bmp -f -a -o /tmp/out.png /path/in.dwg
```

**Use `dwg2bmp` for VLM verification.** Use matplotlib (below) only when you need pixel-diff between two DXFs.

Full technique: see `references/dwg2bmp-headless-renderer.md`.

## Problem (still valid for matplotlib fallback)

The `visual_verifier.py` script in the remote repo (`QCAD-VLM-automation`) assumes multiple renderers are available:

1. **QCAD headless** (`qcad-bin -platform offscreen`) — fails with `Application already running` even after `pkill`; ECMAScript image export silently produces no PNG. (But `dwg2bmp` works, see above.)
2. **LibreCAD** (`librecad dxf2pdf`) — works for DXF but outputs to a FIXED path (the PDF is saved *next to* the input file, not to `-o`). Also produces tiny blank PDFs (~1.6 KB) for some inputs.
3. **ODA File Converter** (`ODAFileConverter`) — headless fails with Qt platform plugin errors (`offscreen` not available); `xvfb-run` hangs indefinitely (>180s timeout).
4. **dwg2pdf** — not installed in the environment.

**The original text-deletion pattern leaves the DXF in a state that ezdxf can't re-read** — see Pitfall at end of this file about ENDSEC corruption. If you need to re-read a `delete_entities_text.py`-modified DXF, patch the missing `0` group code first, or use `dwg2bmp` which doesn't care.

## Standalone Script

A packaged version of this fallback is available as `scripts/visual_verify.py`:

```bash
python3 scripts/visual_verify.py original.dxf modified.dxf "Expected change description"
```

Exit codes: `0` if pixel diff < 1% (PASS), `1` if 1–15% (review needed), `2` if > 15% (likely corruption).

Requirements: `ezdxf`, `matplotlib`, `numpy`, `Pillow`.

## Working Fallback: ezdxf + matplotlib

Extract entities from DXF/DWG (via `dwg2dxf` if necessary), then render with matplotlib in headless `Agg` mode:

```python
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import ezdxf
import numpy as np

def render_dxf_to_png(filepath, outpath, bbox=None):
    doc = ezdxf.readfile(filepath)  # or dwg2dxf first
    fig, ax = plt.subplots(figsize=(14, 10), dpi=150)

    for e in doc.modelspace():
        etype = e.dxftype()
        try:
            if etype == "LINE":
                ax.plot([e.dxf.start.x, e.dxf.end.x],
                        [e.dxf.start.y, e.dxf.end.y],
                        color='black', lw=0.8)
            elif etype == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
                ax.plot([p[0] for p in pts + [pts[0]]],
                        [p[1] for p in pts + [pts[0]]],
                        color='black', lw=0.8)
            elif etype == "ARC":
                theta = np.linspace(np.radians(e.dxf.start_angle),
                                   np.radians(e.dxf.end_angle), 100)
                ax.plot(e.dxf.center.x + e.dxf.radius * np.cos(theta),
                        e.dxf.center.y + e.dxf.radius * np.sin(theta),
                        color='black', lw=0.8)
            elif etype == "CIRCLE":
                theta = np.linspace(0, 2*np.pi, 100)
                ax.plot(e.dxf.center.x + e.dxf.radius * np.cos(theta),
                        e.dxf.center.y + e.dxf.radius * np.sin(theta),
                        color='black', lw=0.8)
            elif etype == "TEXT":
                ax.text(e.dxf.insert.x, e.dxf.insert.y, e.dxf.text,
                        fontsize=6, ha='center', va='center')
            elif etype == "MTEXT":
                ax.text(e.dxf.insert.x, e.dxf.insert.y, e.text,
                        fontsize=7, ha='left', va='center')
        except:
            pass  # Skip problematic entities

    if bbox:
        ax.set_xlim(bbox[0], bbox[1])
        ax.set_ylim(bbox[2], bbox[3])
    ax.set_aspect('equal')
    ax.set_facecolor('white')
    fig.savefig(outpath, dpi=150, bbox_inches='tight')
    plt.close(fig)
```

**Entity count comparison** is the first signal — original vs modified should differ by the expected number. **Pixel diff** between the rendered PNGs is the second gate. If pixel change < 1%, programmatic checks passed. If 1–15%, investigate with VLM vision. If > 15%, check for corruption.

## When to Use Each Path

| Scenario | Use |
|---|---|
| VLM verification (CAD images, text labels, line detection) | **`dwg2bmp`** |
| Pixel-diff between original and modified DWG | matplotlib (monochrome = stable diff) |
| Human-review screenshot for the user | **`dwg2bmp`** (matches QCAD GUI) |
| DWG without an X11 display available | **`dwg2bmp`** (no X11 required) |
| Need to render a DXF that ezdxf can't re-read (after `delete_entities_text.py`) | **`dwg2bmp`** (handles malformed DXF) |

## Key Environment Requirements

- `DISPLAY=:1` and `XAUTHORITY` must be set for xdotool/ImageMagick, but **`dwg2bmp` ignores these** — it runs with Qt's default platform plugin
- `LD_LIBRARY_PATH` must include QCAD's own directory for `dwg2bmp` to start at all
- `QT_QPA_PLATFORM=offscreen` is required by ODA and LibreCAD (but ODA lacks the offscreen plugin); **`dwg2bmp` does not need this** — it uses QCAD's bundled Qt

## Lessons

- The `visual_verifier.py` `self.tools` dict is misleading — `shutil.which()` and `Path.exists()` check binary presence, NOT runtime viability
- For VLM verification, use `dwg2bmp` (not matplotlib) — VLMs need true-color CAD imagery
- Do not waste time debugging Qt/X11 in non-interactive shells — `dwg2bmp` is the working alternative
- For DWG inputs in `dwg2dxf` chain, use `dwg2bmp` instead — it accepts DWG directly, no DXF intermediate

## Pitfall: `delete_entities_text.py` produces DXFs that ezdxf can't re-read

When using text-based entity deletion (the `delete_entities_text.py` script), the surgery can leave stray `ENDSEC` markers without the leading `0` group code, breaking ezdxf's strict DXF parser:

```
DXFStructureError: Invalid group code "ENDSEC
" at line 55137.
```

**Root cause**: When a SECTION is removed or inserted in the wrong place, the line-rewrite can lose the `0` group code prefix. Pattern in the broken file: `0\n  0\nENDSEC\n  0\nSECTION\n  2\n` — a stray `0` group code with no entity type.

**Workarounds**:
1. Use `dwg2bmp` to render — it accepts malformed DXF and produces correct output
2. Patch the missing `0` group code with a one-liner: `sed -i 'N; s/^\nENDSEC$/  0\nENDSEC/' file.dxf` (or use Python text-rewrite)
3. Convert DWG → DXF via LibreDWG `dwg2dxf` (more lenient parser), then read with ezdxf

**Prevention** (long-term fix for `delete_entities_text.py`): track SECTION boundaries and explicitly write `0\nENDSEC\n` pairs. See `references/text-based-dxf-editing.md` for the implementation pattern.

## Session References

- Date 1: 2026-05-25 — Initial fallback doc
  - File: `3_final_v6.dxf` (Pair 3 pipeline output)
  - Result: QCAD headless failed, ODA headless failed, LibreCAD produced wrong output path; matplotlib rendered both original and modified at 150 DPI in 5 seconds
  - Pixel diff: 0.40% → PASSED

- Date 2: 2026-06-04 — `dwg2bmp` discovery
  - File: `1_FINAL_v11.dwg` (F174 verification)
  - Result: `dwg2bmp` produced 31KB true-color render in 3s; matplotlib fallback produced 2.2KB 1-color blank render (DXF was unreadable due to ENDSEC corruption)
  - Result: VLM verification benchmark with three models succeeded because we had a working headless renderer
