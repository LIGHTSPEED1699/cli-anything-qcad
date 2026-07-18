# ODA File Converter GUI Automation via Geisterhand

## Problem

ODA File Converter has a CLI (`OdaFC`), but on Linux it is **unstable in non-interactive shells** — it requires Qt platform initialization that fails without a real X11 session. Even with `xvfb-run`, the tool hangs or crashes with Qt platform plugin errors. This prevents fully automated headless DXF→DWG conversion that preserves BLOCK/ATTDEF/ATTRIB data.

**Validated failure modes:**
- `ODAFileConverter --help` with `QT_QPA_PLATFORM=offscreen` → "Could not find the Qt platform plugin 'offscreen'"
- `ODAFileConverter` with `QT_QPA_PLATFORM=xcb` in a non-interactive shell → hangs indefinitely (180s timeout)
- The extracted AppImage uses Qt5/6 plugins that expect a real X11 compositor

## Solution: GUI Automation via Geisterhand

Since the ODA File Converter GUI works fine when launched in the user's active X11 session, automate it via **Geisterhand HTTP API** + **xdotool** + **ImageMagick `import`**:

1. Launch ODA File Converter GUI in the active X11 session
2. Use Geisterhand `/click` and `/type` endpoints to navigate the GUI
3. Use ImageMagick `import -window` for screenshots (Geisterhand's `/screenshot` is broken on GNOME)
4. File dialog → type full DXF path → Enter
5. Output dialog → type full DWG path → Enter
6. Click "Convert" button
7. Monitor for output file creation
8. Kill process, return path

## Calibration Required

ODA File Converter's GUI coordinates are **not predictable** across screen resolutions. The button positions depend on:
- Window manager decorations
- Screen resolution (3440×1440 ultrawide vs 1920×1080)
- Whether the window opens centered or offset
- Qt theme and font scaling

**One-time calibration workflow:**
```bash
python3 oda_converter_automator.py input.dxf output.dwg --calibrate
# → Launches ODA, takes screenshot, exits
# → Open /tmp/oda_screenshots/step_01_launch.png
# → Measure button coordinates with GIMP, Krita, or ImageMagick identify
# → Edit COORDS dict in the script with measured (x, y) values
```

## Script Location

`~/.hermes/skills/geisterhand-qcad/scripts/oda_converter_automator.py`

Also synced to: `vlm-gui-automation/scripts/oda_converter_automator.py` (GitHub repo)

## Key Implementation Details

### Environment Variables
```python
os.environ.setdefault("DISPLAY", ":1")
os.environ.setdefault("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
```

### ODA Launch (NOT the wrapper script)
The `~/.local/bin/ODAFileConverter` wrapper sets `LD_LIBRARY_PATH` and `QT_QPA_PLATFORM=xcb`. Use it directly:
```python
oda_extract = os.path.expanduser("~/.hermes/hermes-agent/squashfs-root")
env["LD_LIBRARY_PATH"] = f"{oda_extract}/usr/lib:{env.get('LD_LIBRARY_PATH', '')}"
env["QT_PLUGIN_PATH"] = f"{oda_extract}/usr/plugins"
env["QT_QPA_PLATFORM"] = "xcb"
subprocess.Popen([oda_bin], env=env, ...)
```

### Window Detection
Use `xdotool search --onlyvisible --name "ODA File Converter"` with size filtering (w>200, h>100) to find the actual GUI window, not any background process.

### File Dialog Automation
ODA File Converter uses native Qt file dialogs. The reliable sequence:
1. Click "Open" button → dialog appears
2. Type full absolute path (no navigation needed)
3. Press Enter → dialog closes
4. Repeat for output path
5. Click "Convert" → conversion starts

### Completion Detection
Monitor output file size. ODA File Converter writes the DWG incrementally:
```python
for i in range(60):
    time.sleep(1)
    if output_dwg.exists() and output_dwg.stat().st_size > 100:
        break
```

## When to Use This vs. Other Methods

| Method | Preserves BLOCKs | Headless | Reliable | Speed |
|--------|---------------|----------|----------|-------|
| QCAD ODA (`qcad-bin -autostart`) | ❌ No | ✅ Yes | ⚠️ Handle-range bug | 5–10s |
| ODA File Converter CLI (`OdaFC`) | ✅ Yes | ❌ No | ❌ Qt hangs | N/A |
| **ODA File Converter GUI + Geisterhand** | ✅ Yes | ❌ No (needs X11) | ✅ Yes (after calibration) | 10–30s |
| ezdxf + LibreDWG `dxf2dwg` | ⚠️ Partial | ✅ Yes | ⚠️ R2004 only | 5–10s |
| Edit original DWG directly via ezdxf | ✅ Yes | ✅ Yes | ✅ Best | 1–3s |

## Qt6 Widget Event Swallowing (2026-06-10)

**Discovery:** On Qt6 builds of ODA File Converter, **xdotool synthetic events do not register in Qt widgets at all.**

Tested and confirmed non-working:
- `xdotool click 1` / `mousedown 1` / `mouseup 1`
- `xdotool type` (both window-targeted and global)
- `xdotool key Tab` / `key Return` / `key Escape`
- Clipboard paste (`xdotool key ctrl+v` after `xclip -selection clipboard`)
- `xdotool mousemove x y` followed by click

The Qt6 event loop swallows all synthetic X11 events. The window is visible and fully interactive for a **human with a physical mouse**, but not scriptable via xdotool.

**Implication:** ODA File Converter GUI automation is **semi-automated at best**. The agent can:
1. Launch the ODA GUI with correct environment variables
2. Pre-configure input/output folders and format via the GUI's persistent settings (ODA remembers last-used paths)
3. Copy the source DWG to the pre-configured input folder
4. **Notify the user to click the Start button**
5. Poll for output file creation
6. Move the output DWG to the target location

**What the agent CANNOT do:** Click the Start button programmatically.

## Practical Semi-Automated Workflow

```python
# Agent does this:
import subprocess, time, shutil
from pathlib import Path

src_dwg = Path("pairs/pair4/original/4.dwg")
input_folder = Path("/tmp/oda_input")   # Pre-configured in ODA GUI
output_folder = Path("~/Downloads")  # Pre-configured in ODA GUI
shutil.copy(src_dwg, input_folder / "4.dwg")

# Launch ODA (it will see 4.dwg in the input folder)
env = os.environ.copy()
env["DISPLAY"] = ":0"
env["XAUTHORITY"] = "/run/user/1000/gdm/Xauthority"
subprocess.Popen(["~/.local/bin/ODAFileConverter"], env=env)

# Prompt user
print("ODA File Converter is open. Please click START. Waiting...")

# Poll for output
for i in range(120):
    time.sleep(1)
    out_dxf = output_folder / "4.dxf"
    if out_dxf.exists() and out_dxf.stat().st_size > 1000:
        shutil.move(out_dxf, "pairs/pair4/output/4_oda.dxf")
        break
```

## Bottom Line

This is a **workaround for a tool limitation**, not an ideal solution. The best long-term fix is to edit the original DWG directly via ezdxf (bypassing DXF round-trip entirely), which preserves all BLOCK/ATTRIB data natively. Use Geisterhand+ODA GUI only when:
1. You must start from a DXF (e.g., after text-based editing by handle)
2. The DXF was produced by `dwg2dxf` and needs clean DWG export
3. LibreDWG `dxf2dwg` is unavailable or produces R2004-only output

**When ODA GUI is the only viable path, expect a human-in-the-loop step for the Start button click.**

## References

- `references/oda-file-converter-recommendation.md` — Standalone ODA File Converter CLI recommendation (original, before headless failures discovered)
- `geisterhand-qcad/SKILL.md` — Geisterhand skill with full API docs
- `scripts/oda_converter_automator.py` — Production script
