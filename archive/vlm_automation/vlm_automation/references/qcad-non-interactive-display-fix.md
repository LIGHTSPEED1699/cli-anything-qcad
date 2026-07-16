# QCAD / Geisterhand DISPLAY Environment Fix for Non-Interactive Shells

**Session:** 2026-05-17  
**Problem:** xdotool and ImageMagick `import` fail with `Can't open display: (null)` when run from systemd services, cron, or remote agent sessions.  
**Verified fix:** Export DISPLAY, XAUTHORITY, and DBUS_SESSION_BUS_ADDRESS explicitly.

## Symptoms

```
xdotool search --name "QCAD"
# → Error: Can't open display: (null)

import -window "$WID" /tmp/out.png
# → import: unable to open X server ' (null)' @ error/import.c/ImportImageCommand/1288.
```

## Required Environment Variables

| Variable | Value | Why needed |
|----------|-------|-----------|
| `DISPLAY` | `:1` | X11 display number for GNOME session |
| `XAUTHORITY` | `/run/user/1000/gdm/Xauthority` | X authority cookie for display access |
| `DBUS_SESSION_BUS_ADDRESS` | `unix:path=/run/user/1000/bus` | D-Bus session bus (some Qt apps need it) |

## Shell Script Fix

Add to the top of any shell script that calls xdotool or ImageMagick:

```bash
#!/bin/bash
# Critical for non-interactive shells (systemd, cron, remote agents)
export DISPLAY=:1
export XAUTHORITY=/run/user/1000/gdm/Xauthority
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

# Now xdotool and import work
xdotool search --onlyvisible --name "QCAD" windowactivate windowraise
sleep 0.3
WINDOW_ID=$(xdotool search --onlyvisible --name "QCAD" | head -1)
import -window "$WINDOW_ID" /tmp/qcad_screenshot.png
```

## Python Script Fix

Add to the `__init__` of any Python class that drives GUI automation:

```python
import os

class QCADAutomator:
    def __init__(self, ...):
        # Ensure DISPLAY is set for xdotool/ImageMagick in non-interactive shells
        os.environ.setdefault("DISPLAY", ":1")
        os.environ.setdefault("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
        os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
        ...
```

Why `setdefault` instead of plain `os.environ[...] = ...`? So that if the variable is already set (e.g., from an interactive shell), the existing value is preserved. Only missing vars get the default.

## Verification Steps

1. **Check variable values in your GUI session:**
   ```bash
   echo $DISPLAY          # → :1
   echo $XAUTHORITY       # → /run/user/1000/gdm/Xauthority
   echo $DBUS_SESSION_BUS_ADDRESS
   ```

2. **Test xdotool can find QCAD:**
   ```bash
   xdotool search --onlyvisible --name "QCAD" | head -1
   # → 6292387  (some window ID)
   ```

3. **Test screenshot capture:**
   ```bash
   WINDOW_ID=$(xdotool search --onlyvisible --name "QCAD" | head -1)
   import -window "$WINDOW_ID" /tmp/qcad_test.png
   file /tmp/qcad_test.png
   # → /tmp/qcad_test.png: PNG image data, 3374 x 1408, 8-bit/color RGBA
   ```

4. **Verify screenshot contains actual content (not black/blank):**
   ```python
   from PIL import Image
   import numpy as np
   img = Image.open('/tmp/qcad_test.png')
   pixels = np.array(img)
   black_pixels = np.sum(np.all(pixels == [0, 0, 0], axis=2))
   total = pixels.shape[0] * pixels.shape[1]
   print(f"Black: {100*black_pixels/total:.1f}%")
   # → Should be < 80% (68% is typical for a QCAD drawing with border)
   # → 100% black = window not captured / occluded / wrong window
   ```

## Where to Apply

Apply this fix to any automation script that:
- Runs from systemd user services (`systemctl --user`)
- Runs from cron (`crontab -e`)
- Runs from remote SSH without X forwarding
- Is executed by an AI agent / automation framework
- Uses `subprocess.run()` or `subprocess.Popen()` from Python

## Related

- `references/geisterhand-qcad-gui-pitfalls.md` — Full GUI automation pitfalls including Qt6 AT-SPI absence, window detection, focus race conditions
- `references/geisterhand-qcad-pitfalls.md` — Fallback matplotlib DXF rendering pipeline (when GUI automation is impractical)
