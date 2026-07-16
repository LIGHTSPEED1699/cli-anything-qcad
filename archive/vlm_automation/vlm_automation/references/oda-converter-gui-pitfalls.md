# ODA File Converter GUI Calibration (2026-05-28)

Session tested on: MSI MAG342CQR 3440×1440 ultrawide, GNOME X11 (Ubuntu 24.04).

## Critical Lessons

### 1. ODA operates on folders, not files
- ODA's Input/Output buttons open file dialogs that expect a **folder path**, not a specific `.dxf` or `.dwg`.
- Passing a file path makes ODA use the file's parent folder, but the UI text field behavior is designed for folders.
- Script must pass `input_folder / output_folder` to the text field.

### 2. Text corruption: Geisterhand /type vs xdotool direct
- **Bug:** Using `geisterhand /type` endpoint caused character misordering, e.g. `/media/...` became `/emdia/...`.
- **Root cause:** Geisterhand buffers/reorders keystrokes when injecting text rapidly over its REST API.
- **Fix:** Call `xdotool type --clearmodifiers --window $WID` directly via `subprocess.run()`.
  - `--clearmodifiers` ensures no stuck modifier keys interfere.
  - `--window $WID` sends keystrokes to the specific ODA window, not the focused one.
  - Keystroke order is guaranteed by xdotool at the X11 protocol level.

### 3. All coordinates are measured, never assumed
- The ODA GUI window on a 3440×1440 monitor is NOT centered. Its dialog buttons sit at **x≈2000**, about 1000 px right of center.
- Previous guesses of `(520, 420)` etc. were off by ~1500 px.

| Element | Measured (px) | Notes |
|---|---|---|
| Input Folder Button | (2004, 462) | Far right of dialog |
| Input Path Text Field | (1525, 851) | In file browser dialog |
| Input Dialog "Choose" Button | (2000, 855) | Confirms folder selection |
| Output Folder Button | (2004, 536) | Same x as Input Folder |
| Output Path Text Field | (1600, 850) | Slightly different y from input |
| Output Dialog "Choose" | (2000, 850) | Same x as input Choose |
| Start / Convert Button | (1974, 602) | Main window, right side |

### 4. Path field pre-filled with default text
- ODA's path text fields contain the last-used folder or a default home path.
- **Must clear before typing:** Click field → Ctrl+A (select all) → type new path.
- Without Ctrl+A, the new path appends to the default, producing invalid concatenated paths.

### 5. xdotool keypress style for clearing fields
- `Ctrl+A` select-all works reliably across Qt file dialogs.
- Avoid backspace/delete loops — select-all is faster and less fragile.

```python
# Correct pattern in oda_converter_automator.py:
self.click(x, y)          # Click path field
self.key("a", ["ctrl"])   # Select all existing text
self.type_text(new_path)  # Replaces selection (xdotool direct)
self.key("Return")        # Confirm
```

## Calibration Command

```bash
python3 scripts/oda_converter_automator.py input_folder/ output_folder/ --calibrate
```

This launches ODA and takes a screenshot. Use `watch -n 0.1 xdotool getmouselocation` while hovering over each button to measure coordinates.

## Running Conversion After Calibration

```bash
export DISPLAY=:1
export XAUTHORITY=/run/user/1000/gdm/Xauthority
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

python3 scripts/oda_converter_automator.py \
  /path/to/input/folder/ \
  /path/to/output/folder/
```

## Environment Reminders

Missing `DISPLAY`, `XAUTHORITY`, or `DBUS_SESSION_BUS_ADDRESS` causes `xdotool` or `ImageMagick import` to fail with display errors. Set all three before any Geisterhand/X11 operation.
