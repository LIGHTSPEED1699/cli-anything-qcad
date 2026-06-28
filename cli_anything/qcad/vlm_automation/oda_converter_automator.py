#!/usr/bin/env python3
"""
oda_converter_automator.py — Automate ODA File Converter GUI via xdotool

Uses coordinate-based GUI automation to open DXF → select format → save as DWG.
ODA File Converter preserves BLOCK/ATTDEF/ATTRIB data that QCAD ODA strips.

Usage:
    python3 oda_converter_automator.py input.dxf output.dwg

Environment:
    DISPLAY and XAUTHORITY are auto-detected from the running gnome-shell process.
    Screen lock is temporarily disabled during calibration and restored on exit.
"""

import os
import sys
import time
import json
import subprocess
import atexit
from pathlib import Path

os.environ.setdefault("DISPLAY", _DISPLAY)
os.environ.setdefault("XAUTHORITY", _XAUTHORITY)
os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")

_SCREEN_LOCK_ORIGINAL = {}

def _guard_screen_lock():
    """Disable screen lock/blanking so ODA doesn't lock during calibration."""
    global _SCREEN_LOCK_ORIGINAL
    try:
        r1 = subprocess.run(["gsettings", "get", "org.gnome.desktop.screensaver", "lock-enabled"],
                          capture_output=True, text=True, timeout=5)
        r2 = subprocess.run(["gsettings", "get", "org.gnome.desktop.session", "idle-delay"],
                          capture_output=True, text=True, timeout=5)
        if r1.returncode == 0:
            _SCREEN_LOCK_ORIGINAL['lock'] = r1.stdout.strip()
        if r2.returncode == 0:
            _SCREEN_LOCK_ORIGINAL['idle'] = r2.stdout.strip()

        subprocess.run(["gsettings", "set", "org.gnome.desktop.screensaver", "lock-enabled", "false"],
                       capture_output=True, timeout=5)
        subprocess.run(["gsettings", "set", "org.gnome.desktop.session", "idle-delay", "3600"],
                       capture_output=True, timeout=5)
        subprocess.run(["xset", "s", "off"], capture_output=True, timeout=5)
        subprocess.run(["xset", "-dpms"], capture_output=True, timeout=5)
    except Exception:
        pass


def _restore_screen_lock():
    """Restore original screen lock settings."""
    if not _SCREEN_LOCK_ORIGINAL:
        return
    try:
        if 'lock' in _SCREEN_LOCK_ORIGINAL:
            subprocess.run(["gsettings", "set", "org.gnome.desktop.screensaver", "lock-enabled",
                           _SCREEN_LOCK_ORIGINAL['lock']], capture_output=True, timeout=5)
        if 'idle' in _SCREEN_LOCK_ORIGINAL:
            subprocess.run(["gsettings", "set", "org.gnome.desktop.session", "idle-delay",
                           _SCREEN_LOCK_ORIGINAL['idle']], capture_output=True, timeout=5)
    except Exception:
        pass


atexit.register(_restore_screen_lock)

ODA_BIN = os.environ.get("ODA_CONVERTER", os.path.expanduser("~/.local/bin/ODAFileConverter"))

def _detect_display():
    """Auto-detect the active X11 display from the gdm/Xorg process."""
    try:
        import glob
        for pid_dir in glob.glob('/proc/[0-9]*'):
            environ_file = f"{pid_dir}/environ"
            try:
                with open(environ_file, 'rb') as f:
                    env_data = f.read().decode('utf-8', errors='ignore').split('\0')
                    env_dict = {}
                    for item in env_data:
                        if '=' in item:
                            k, v = item.split('=', 1)
                            env_dict[k] = v
                    if 'DISPLAY' in env_dict and 'XAUTHORITY' in env_dict:
                        return env_dict.get('DISPLAY'), env_dict.get('XAUTHORITY')
            except (PermissionError, OSError):
                continue
    except Exception:
        pass
    # Fallback to pgrep gnome-shell
    try:
        result = subprocess.run(
            ["pgrep", "-f", "gnome-shell"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = result.stdout.strip().split('\n')[0]
            env_file = f"/proc/{pid}/environ"
            with open(env_file, 'rb') as f:
                env_data = f.read().decode('utf-8', errors='ignore').split('\0')
                env_dict = {item.split('=', 1)[0]: item.split('=', 1)[1] for item in env_data if '=' in item}
                return env_dict.get('DISPLAY', ':0'), env_dict.get('XAUTHORITY', '/run/user/1000/gdm/Xauthority')
    except Exception:
        pass
    return ':0', '/run/user/1000/gdm/Xauthority'


_DISPLAY, _XAUTHORITY = _detect_display()


class ODAFileConverterAutomator:
    def __init__(self, oda_bin=ODA_BIN, window_title="ODA File Converter"):
        self.oda_bin = oda_bin
        self.window_title = window_title
        self._window_id = None
        self._step = 0

    def _get_window_id(self, timeout=10):
        if self._window_id:
            return self._window_id
        for _ in range(timeout * 2):
            try:
                result = subprocess.run(
                    ["xdotool", "search", "--onlyvisible", "--name", self.window_title],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    ids = result.stdout.strip().split('\n')
                    for wid in ids:
                        geom = subprocess.run(
                            ["xdotool", "getwindowgeometry", wid],
                            capture_output=True, text=True, timeout=5
                        )
                        if "Geometry:" in geom.stdout:
                            for line in geom.stdout.split('\n'):
                                if "Geometry:" in line:
                                    parts = line.strip().split(':')
                                    if len(parts) >= 2:
                                        wh = parts[1].strip().split('x')
                                        if len(wh) == 2:
                                            w, h = int(wh[0]), int(wh[1])
                                            if w > 200 and h > 100:
                                                self._window_id = wid
                                                return wid
                    self._window_id = ids[0]
                    return self._window_id
            except Exception:
                pass
            time.sleep(0.5)
        return None

    def focus(self):
        wid = self._get_window_id()
        if not wid:
            return False
        try:
            subprocess.run(
                ["xdotool", "windowactivate", wid, "windowraise", wid],
                capture_output=True, text=True, timeout=5
            )
            time.sleep(0.5)
            return True
        except Exception:
            return False

    def kill(self):
        subprocess.run(["pkill", "-9", "-f", "ODAFileConverter"], capture_output=True)
        time.sleep(1)
        self._window_id = None

    def launch(self):
        self.kill()
        _guard_screen_lock()  # Prevent GNOME from locking during calibration
        env = os.environ.copy()
        oda_extract = os.path.expanduser("~/.hermes/hermes-agent/squashfs-root")
        if not os.path.exists(oda_extract):
            # Fallback to the location pointed to by the blessed wrapper script
            oda_extract = os.path.expanduser("~/.local/bin/ODAFileConverter")
            if os.path.islink(oda_extract):
                # Resolve to the binary to find its extraction root
                import subprocess
                result = subprocess.run(["readlink", "-f", oda_extract], capture_output=True, text=True)
                if result.returncode == 0:
                    real_bin = result.stdout.strip()
                    # Walk up from usr/bin/ODAFileConverter to find the extract root
                    parts = real_bin.split(os.sep)
                    try:
                        idx = parts.index("usr")
                        oda_extract = os.sep.join(parts[:idx])
                    except ValueError:
                        oda_extract = os.path.dirname(os.path.dirname(real_bin))
        env["LD_LIBRARY_PATH"] = f"{oda_extract}/usr/lib:{env.get('LD_LIBRARY_PATH', '')}"
        env["QT_PLUGIN_PATH"] = f"{oda_extract}/usr/plugins"
        env["QT_QPA_PLATFORM"] = "xcb"
        proc = subprocess.Popen(
            [self.oda_bin],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True
        )
        time.sleep(3)
        wid = self._get_window_id(timeout=15)
        if not wid:
            print("ERROR: ODA File Converter window did not appear", file=sys.stderr)
            return None
        self.focus()
        return {"pid": proc.pid, "window_id": wid}

    def screenshot(self, label=""):
        self.focus()
        self._step += 1
        filename = f"/tmp/oda_screenshots/step_{self._step:02d}_{label}.png"
        Path("/tmp/oda_screenshots").mkdir(parents=True, exist_ok=True)
        wid = self._get_window_id()
        if wid:
            try:
                result = subprocess.run(
                    ["import", "-window", wid, filename],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0 and Path(filename).exists():
                    return filename
            except Exception:
                pass
        raise RuntimeError("Screenshot failed — ensure imagemagick and xdotool are installed")

    def click(self, x, y):
        subprocess.run(
            ["xdotool", "mousemove", str(x), str(y), "click", "1"],
            capture_output=True, text=True, timeout=5
        )

    def type_text(self, text):
        subprocess.run(
            ["xdotool", "type", text],
            capture_output=True, text=True, timeout=5
        )

    def key(self, key):
        subprocess.run(
            ["xdotool", "key", key],
            capture_output=True, text=True, timeout=5
        )

    def convert_dxf_to_dwg(self, input_dxf, output_dwg, output_format="ACAD2018"):
        input_dxf = Path(input_dxf).resolve()
        output_dwg = Path(output_dwg).resolve()
        if not input_dxf.exists():
            raise FileNotFoundError(f"Input DXF not found: {input_dxf}")
        info = self.launch()
        if not info:
            raise RuntimeError("Failed to launch ODA File Converter")
        print(f"ODA launched — window {info['window_id']}, PID {info['pid']}")
        self.screenshot("01_launch")
        # Coordinate map (3440×1440 ultrawide, approximate — calibrate first)
        COORDS = {
            "open_input_btn": (520, 420),
            "input_path_field": (700, 500),
            "save_input_ok": (1100, 700),
            "output_path_btn": (520, 470),
            "output_path_field": (700, 550),
            "save_output_ok": (1100, 750),
            "convert_btn": (960, 600),
        }
        print("Step 1: Clicking Open input...")
        self.click(*COORDS["open_input_btn"])
        time.sleep(1.5)
        print(f"Step 2: Typing input path: {input_dxf}")
        self.type_text(str(input_dxf))
        time.sleep(0.5)
        self.key("Return")
        time.sleep(2)
        print("Step 3: Setting output path...")
        self.click(*COORDS["output_path_btn"])
        time.sleep(1.5)
        self.type_text(str(output_dwg))
        time.sleep(0.5)
        self.key("Return")
        time.sleep(2)
        print("Step 4: Clicking Convert...")
        self.click(*COORDS["convert_btn"])
        time.sleep(0.5)
        print("Step 5: Waiting for conversion...")
        for i in range(60):
            time.sleep(1)
            if output_dwg.exists() and output_dwg.stat().st_size > 100:
                size_kb = output_dwg.stat().st_size / 1024
                print(f"  → Output file detected: {size_kb:.1f} KB")
                break
        final = self.screenshot("99_done")
        print(f"Final screenshot: {final}")
        if not output_dwg.exists():
            raise RuntimeError(f"Output DWG not created: {output_dwg}")
        print(f"SUCCESS: {input_dxf.name} → {output_dwg.name} ({output_dwg.stat().st_size} bytes)")
        self.kill()
        return output_dwg


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Automate ODA File Converter DXF→DWG")
    parser.add_argument("input_dxf", help="Input DXF file path")
    parser.add_argument("output_dwg", help="Output DWG file path")
    parser.add_argument("--calibrate", action="store_true",
                        help="Launch ODA, take screenshot, and exit (for coordinate calibration)")
    args = parser.parse_args()
    auto = ODAFileConverterAutomator()
    if args.calibrate:
        info = auto.launch()
        print(f"Launched — window {info['window_id']}")
        calib = auto.screenshot("calibration")
        print(f"Calibration screenshot saved: {calib}")
        print("\nReview the screenshot and measure button coordinates.")
        print("Then edit COORDS dict in the script and re-run.")
        return
    auto.convert_dxf_to_dwg(args.input_dxf, args.output_dwg)


if __name__ == "__main__":
    main()
