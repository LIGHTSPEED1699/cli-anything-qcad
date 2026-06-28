# Geisterhand QCAD GUI Automation Pitfalls

## Session
2026-05-17. Attempted to use Geisterhand + QCAD GUI for screenshot-based VLM verification of V10/V11 DWG.

## Outcome
GUI automation path ABANDONED in favor of matplotlib DXF rendering. The programmatic renderer is faster, deterministic, and doesn't depend on window manager state.

## Why GUI Automation Failed

### 1. Autostart Scripts Run Pre-GUI
QCAD `-autostart script.js` launches the script BEFORE the GUI window fully initializes. The script's `QTimer.singleShot()` or immediate commands execute on a document that has no visible canvas. Screenshots taken via Geisterhand `/screenshot` capture the desktop/file manager, not QCAD.

```
# This script runs but zoom happens on invisible canvas
var di = EAction.getDocumentInterface();  // may return null
QTimer.singleShot(2000, function() { ... });  // window still not rendered
```

**Symptom:** Screenshot shows desktop/file manager, not QCAD drawing.

### 2. QCAD Window Detection Fragility
`xdotool search --name "QCAD"` returns MULTIPLE window IDs:
- Main QCAD window (620×1080, actual content)
- Tiny helper windows (3×3, selection owner, etc.)
- Must filter by minimum geometry (>100×100)

```python
# From qcad_automation_example.py
for wid in ids:
    geom = subprocess.run(["xdotool", "getwindowgeometry", wid], ...)
    w, h = parse(geom.stdout)
    if w > 100 and h > 100:
        return wid  # Real QCAD window
```

### 3. DISPLAY Environment in Non-Interactive Shells
Terminal and cron sessions don't inherit the user's DISPLAY/XAUTHORITY:
```bash
# Fails in terminal/cron:
import -window $WID /tmp/out.png
# Error: Can't open display: (null)
```

**Fix:** Export explicitly before every xdotool or ImageMagick call:
```bash
export DISPLAY=:1
export XAUTHORITY=/run/user/1000/gdm/Xauthority
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
```

Same fix applied to Python scripts:
```python
import os
os.environ.setdefault("DISPLAY", ":1")
os.environ.setdefault("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
```

### 4. Window Stacking / Not-on-Top
Even after `xdotool windowactivate`, QCAD may sit behind Discord or other windows. The ImageMagick `import -window` captures the window's backing pixmap which may show stale content if the window is occluded.

**Symptom:** Screenshot is 68% black pixels (desktop showing through).

### 5. No Qt6 AT-SPI Accessibility Bridge
QCAD's Qt6 UI elements do not appear in the AT-SPI2 tree. `qt6-atspi` package does not exist in Ubuntu 24.04 noble repositories. Element-based automation (click by title/role) is impossible.

## Recommended Alternatives

| Goal | Use This | Not That |
|------|---------|----------|
| Visual VLM verification | `matplotlib DXF renderer` + `PIL` crop | QCAD GUI screenshot |
| Side-by-side comparison | `ezdxf` + `matplotlib` subplots | Geisterhand `/screenshot` |
| Zoom to region | `ax.set_xlim(xmin, xmax)` | QCAD `zoom window` command |
| Highlight specific entity | `ax.plot(..., color='lime', linewidth=3)` | QCAD entity selection |
| Export DWG | QCAD headless `-no-gui` autostart | QCAD GUI |

### Matplotlib DXF Render (Reliable)
```python
import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, matplotlib
import matplotlib.pyplot as plt

doc = ezdxf.readfile(dxf_path)
fig, ax = plt.subplots(figsize=(14, 8))
ax.set_facecolor('black')
ax.set_xlim(xmin, xmax)
ax.set_ylim(ymin, ymax)
ax.set_aspect('equal')
ctx = RenderContext(doc)
out = matplotlib.MatplotlibBackend(ax)
frontend = Frontend(ctx, out)
frontend.draw_layout(doc.modelspace(), finalize=True)
fig.savefig('/tmp/render.png', dpi=150, facecolor='black')
```

### Render with Entity Highlighting
```python
# After main render, overlay specific entity in bright color
for v in entity.vertices:
    ax.plot(v.x, v.y, 'yo', markersize=4)
ax.plot(xs + [xs[0]], ys + [ys[0]], color='lime', linewidth=3)
```

## When GUI Automation IS Still Useful

Only for true GUI-specific workflows that can't be done via DXF:
- Interactive entity selection by clicking
- Menu-driven operations (File > Export > specific format)
- Dialog-based configuration

For these, the pattern is:
1. Launch QCAD with `subprocess.Popen(..., env={DISPLAY:':1', ...})`
2. Use Geisterhand `/key` and `/type` (NOT `/click` via xdotool) to drive QCAD
3. Wait with `time.sleep()` (no reliable readiness signal)
4. Screenshot via Geisterhand `/screenshot` (or `import -window root` as fallback)
5. Accept that success rate is ~60%

## Test Script Status

`~/openclaw-shared/test_qcad_automation.sh` — **PASSES** after adding `export DISPLAY=:1` and `export XAUTHORITY=...` at the top. The 6-check test (service status, QCAD binary, screenshot via ImageMagick, xdotool click, xdotool type, accessibility tree) completes successfully when run from a non-interactive shell. Screenshot captures the actual QCAD window (3374×1408, not black/blank).

`~/openclaw-shared/qcad_automation_example.py` — patched with `os.environ.setdefault("DISPLAY", ":1")` and `os.environ.setdefault("XAUTHORITY", "/run/user/1000/gdm/Xauthority")` in `QCADAutomator.__init__()`. The class now works from systemd/cron/non-interactive shells without manual env export.

**Limitation persists:** accessibility tree only shows gnome-shell (Qt6 AT-SPI bridge is absent on Ubuntu 24.04), so element-based automation remains impossible. Coordinate-based automation (xdotool click, type, screenshot) is the only viable path.

## Files
- Test script: `~/openclaw-shared/test_qcad_automation.sh` (verified)
- Python example: `~/openclaw-shared/qcad_automation_example.py` (verified)
- Documentation: `~/openclaw-shared/geisterhand-qcad-automation.md`
