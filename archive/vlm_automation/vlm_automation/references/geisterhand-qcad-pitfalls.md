# Geisterhand + QCAD Visual Verification Pitfalls

Session-hardened knowledge from live VLM verification attempts on Pair 1 V10 DWG (2026-05-16).

## What Was Attempted

**Goal:** Use Geisterhand (Linux GUI automation HTTP API on 127.0.0.1:7680) to launch QCAD, zoom to the F174 area, take a screenshot, and have a VLM verify the restored ground reference line.

**Result:** QCAD autostart ECMAScript zoom commands do NOT execute after GUI window initialization. Screenshots via Geisterhand captured the desktop/file manager, not the QCAD canvas. Manual `type`/`key` commands to QCAD did not zoom because the command line was not properly focused.

## What Actually Works

| Approach | Outcome |
|----------|---------|
| QCAD + autostart script with `setZoomToWindow()` in ECMAScript | **FAIL** — Script runs before GUI window exists; no zoom effect |
| QCAD + `-no-gui` flag + Geisterhand screenshot | **FAIL** — No window surfaces for X11 capture |
| QCAD with GUI (no `-no-gui`) + Geisterhand screenshot | **PARTIAL** — Window opens but is not auto-focused/bringed forward; screenshot may capture file manager or Discord instead |
| Focus QCAD center canvas → type `zw` → click two corners programmatically | **UNRELIABLE** — Command line state uncertain; screenshots showed full-sheet view, not zoomed |
| **ezdxf matplotlib rendering → VLM `vision_analyze` with `file://` URLs** | **✅ RELIABLE** — Programmatic full-sheet or zoomed-region renders directly from DXF; clean, repeatable, no GUI timing issues |

## Recommended Fallback Pipeline

When Geisterhand+QCAD visual verification fails, use this deterministic alternative:

### 1. Programmatic DXF Render to PNG

```python
import ezdxf, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def render_dxf_region(dxf_path, png_path, xlim, ylim, figsize=(14, 10)):
    doc = ezdxf.readfile(dxf_path)
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_facecolor('black')
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    ax.set_aspect('equal')
    ax.axis('off')
    
    for e in doc.modelspace():
        try:
            t = e.dxftype()
            if t == 'LINE':
                xs = [e.dxf.start.x, e.dxf.end.x]
                ys = [e.dxf.start.y, e.dxf.end.y]
                ax.plot(xs, ys, color='cyan', linewidth=0.8)
            elif t == 'LWPOLYLINE':
                pts = list(e.get_points('xy'))
                xs = [p[0] for p in pts] + [pts[0][0]]
                ys = [p[1] for p in pts] + [pts[0][1]]
                c = 'red' if e.dxf.color == 1 else 'white'
                ax.plot(xs, ys, color=c, linewidth=1.0)
            elif t == 'POLYLINE':
                verts = list(e.vertices)
                xs = [v.dxf.location.x for v in verts] + [verts[0].dxf.location.x]
                ys = [v.dxf.location.y for v in verts] + [verts[0].dxf.location.y]
                c = 'red' if e.dxf.color == 1 else 'white'
                ax.plot(xs, ys, color=c, linewidth=1.0)
            elif t == 'TEXT':
                c = 'red' if e.dxf.color == 1 else 'white'
                ax.text(e.dxf.insert.x, e.dxf.insert.y, e.dxf.text,
                       color=c, fontsize=8, ha='center', va='center')
            elif t == 'ARC':
                c = e.dxf.center
                ax.add_patch(plt.Circle((c.x, c.y), e.dxf.radius,
                           fill=False, color='cyan', linewidth=0.8))
        except:
            pass
    
    plt.tight_layout(pad=0)
    fig.savefig(png_path, dpi=150, facecolor='black')
    plt.close()
```

### 2. Side-by-Side Comparison (V_before vs V_after)

Use `matplotlib.pyplot.subplots(1, 2)` with the same xlim/ylim for both DXFs. Highlight restored or deleted entities in contrasting colors (e.g., restored=lime, deleted=orange).

### 3. VLM Analysis via `file://` URLs

The `vision_analyze` tool accepts local file URLs in the `image_url` field:

```json
{"image_url": "file:///tmp/comparison.png", "question": "Compare left (V10) and right (V11). V10 should have empty space to the right of F174. V11 should show a restored ground reference line (lime green)."}
```

**Important:** Use the three-slash format `file:///absolute/path/to/file.png`. Two-slash `file://` is rejected.

## Key Pitfalls

1. **Autostart vs. GUI timing** — QCAD loads ECMAScript autostart files during initialization, before the main window X11 surface is mapped. Zoom commands in autostart scripts execute against a document interface with no active viewport, producing no visible effect.

2. **`-no-gui` kills the window** — Launching with `-no-gui` suppresses the X11 window entirely. Geisterhand (X11 screenshot + XTest) cannot interact with or capture what does not exist.

3. **QCAD command-line focus state** — After launching, the QCAD command line may not accept keyboard input until the canvas has been clicked. Programmatic `type` and `key` commands via Geisterhand may be ignored unless preceded by a focused click on the canvas area.

4. **X11 focus race conditions** — When multiple windows are open (file manager, Discord, browser), Geisterhand screenshots capture the Z-stack top window, not necessarily QCAD. `xdotool` or `wmctrl` can raise QCAD, but they may not be installed in the environment.

## When to Use Which Approach

| Situation | Approach |
|-----------|----------|
| Visual verification of drawing edits (deletions, clones) | **ezdxf matplotlib render + VLM** — deterministic, fast, no GUI timing issues |
| Interactive CAD manipulation (drawing new lines, clicking specific UI tools) | **Geisterhand + QCAD GUI** — coordinate-based automation is viable when live interaction is needed |
| Large-scale batch DWG verification across multiple files | **QCAD headless ECMAScript** — `-autostart` scripts with file I/O for status reports, no screenshots needed |

## Session Artifacts

- `geisterhand.service`: systemd user service at `~/.config/systemd/user/geisterhand.service`
- QCAD Pro path: `~/opt/qcad-3.32.7-pro-linux-qt6-x86_64/`
- Test script: `~/openclaw-shared/test_qcad_automation.sh`
- Python example: `~/openclaw-shared/qcad_automation_example.py`
- Documentation: `~/openclaw-shared/geisterhand-qcad-automation.md`
