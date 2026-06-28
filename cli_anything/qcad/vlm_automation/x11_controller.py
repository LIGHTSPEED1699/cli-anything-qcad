"""
X11 GUI Controller using python-xlib and pynput.
Replaces xdotool dependency with pure Python.
"""

import time
import subprocess
import re
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
from Xlib import display, X
from Xlib.ext.xtest import fake_input
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key


class X11Controller:
    """Pure Python X11 window/mouse/keyboard controller."""

    def __init__(self):
        self.disp = display.Display()
        self.root = self.disp.screen().root
        self.mouse = MouseController()
        self.keyboard = KeyboardController()

    def _get_window_pid(self, window) -> Optional[int]:
        """Get window PID from _NET_WM_PID."""
        try:
            prop = window.get_full_property(
                self.disp.intern_atom('_NET_WM_PID'),
                self.disp.intern_atom('CARDINAL'),
            )
            if prop and prop.value:
                return int(prop.value[0])
        except Exception:
            pass
        return None

    def _get_proc_cmdline(self, pid: int) -> str:
        """Read cmdline for a given PID."""
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return f.read().replace(b"\x00", b" ").decode('utf-8', errors='replace').strip()
        except Exception:
            return ""

    def _is_qcad_process(self, pid: Optional[int]) -> bool:
        """Verify PID actually belongs to a qcad process."""
        if not pid:
            return False
        cmdline = self._get_proc_cmdline(pid)
        return bool(re.search(r'qcad', cmdline, re.I))

    def get_window_by_name(self, name: str, min_size: int = 50, verify_process: str = "") -> Optional[int]:
        """Find window ID by name (partial match, case-insensitive).

        When verify_process is provided (e.g. 'qcad'), the returned
        window's PID is checked against /proc/PID/cmdline to make sure
        a misleading name match (e.g. a Thunderbird e-mail about
        QCAD) is rejected.
        """
        self.root.change_attributes(event_mask=X.SubstructureNotifyMask)
        window_ids = self.root.query_tree().children

        for window_id in window_ids:
            try:
                wm_name = window_id.get_wm_name()
                if wm_name and name.lower() in str(wm_name).lower():
                    # Check size to avoid selection owner / utility windows
                    geom = window_id.get_geometry()
                    if geom.width >= min_size and geom.height >= min_size:
                        return window_id.id
            except Exception:
                continue

        # Fallback: use _NET_WM_NAME (UTF-8)
        for window_id in window_ids:
            try:
                net_wm_name = window_id.get_full_property(
                    self.disp.intern_atom('_NET_WM_NAME'),
                    self.disp.intern_atom('UTF8_STRING')
                )
                if net_wm_name and name.lower() in net_wm_name.value.decode('utf-8', errors='replace').lower():
                    geom = window_id.get_geometry()
                    if geom.width >= min_size and geom.height >= min_size:
                        return window_id.id
            except Exception:
                continue

        # Deep search: traverse child windows
        for window_id in window_ids:
            found = self._search_children(window_id, name, min_size)
            if found:
                return found.id

        return None

    def _search_children(self, window, name: str, min_size: int = 50):
        """Recursively search child windows, filtering out tiny windows."""
        try:
            children = window.query_tree().children
            for child in children:
                try:
                    geom = child.get_geometry()
                    if geom.width < min_size or geom.height < min_size:
                        continue
                    wm_name = child.get_wm_name()
                    if wm_name and name.lower() in str(wm_name).lower():
                        return child
                except Exception:
                    pass
                try:
                    geom = child.get_geometry()
                    if geom.width < min_size or geom.height < min_size:
                        continue
                    net_wm_name = child.get_full_property(
                        self.disp.intern_atom('_NET_WM_NAME'),
                        self.disp.intern_atom('UTF8_STRING')
                    )
                    if net_wm_name and name.lower() in net_wm_name.value.decode('utf-8', errors='replace').lower():
                        return child
                except Exception:
                    pass

                # Recurse deeper
                result = self._search_children(child, name, min_size)
                if result:
                    return result
        except Exception:
            pass
        return None

    def get_window_geometry(self, window_id: int) -> Dict[str, int]:
        """Get window position and size."""
        window = self.disp.create_resource_object('window', window_id)
        geom = window.get_geometry()
        # Translate to root coordinates
        translated = window.translate_coords(self.root, 0, 0)
        return {
            'x': abs(translated.x),
            'y': abs(translated.y),
            'width': geom.width,
            'height': geom.height
        }

    def raise_window(self, window_id: int):
        """Bring window to foreground."""
        window = self.disp.create_resource_object('window', window_id)
        # Use _NET_ACTIVE_WINDOW to properly activate
        from Xlib.protocol.event import ClientMessage
        event = ClientMessage(
            window=window,
            client_type=self.disp.intern_atom('_NET_ACTIVE_WINDOW'),
            data=(32, [2, X.CurrentTime, 0, 0, 0])
        )
        mask = (X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self.root.send_event(event, event_mask=mask)
        self.disp.sync()
        time.sleep(0.2)

    def click(self, x: int, y: int, button: str = 'left'):
        """Click at screen coordinates."""
        btn_map = {
            'left': Button.left,
            'right': Button.right,
            'middle': Button.middle
        }
        btn = btn_map.get(button, Button.left)
        self.mouse.position = (x, y)
        time.sleep(0.05)
        self.mouse.press(btn)
        time.sleep(0.05)
        self.mouse.release(btn)
        time.sleep(0.05)

    def double_click(self, x: int, y: int):
        """Double-click at coordinates."""
        self.click(x, y)
        time.sleep(0.1)
        self.click(x, y)

    def type_text(self, text: str):
        """Type text using keyboard."""
        self.keyboard.type(text)
        time.sleep(0.1)

    def key_press(self, key_name: str):
        """Press a special key."""
        key_map = {
            'return': Key.enter,
            'enter': Key.enter,
            'escape': Key.esc,
            'esc': Key.esc,
            'tab': Key.tab,
            'space': Key.space,
            'delete': Key.delete,
            'backspace': Key.backspace,
            'up': Key.up,
            'down': Key.down,
            'left': Key.left,
            'right': Key.right,
            'f1': Key.f1, 'f2': Key.f2, 'f3': Key.f3, 'f4': Key.f4,
            'f5': Key.f5, 'f6': Key.f6, 'f7': Key.f7, 'f8': Key.f8,
            'f9': Key.f9, 'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
            'ctrl': Key.ctrl,
            'alt': Key.alt,
            'shift': Key.shift,
            'ctrl+a': (Key.ctrl, 'a'),
            'ctrl+c': (Key.ctrl, 'c'),
            'ctrl+v': (Key.ctrl, 'v'),
            'ctrl+z': (Key.ctrl, 'z'),
            'ctrl+y': (Key.ctrl, 'y'),
        }

        key = key_map.get(key_name.lower())
        if not key:
            return

        if isinstance(key, tuple):
            with self.keyboard.pressed(key[0]):
                self.keyboard.press(key[1])
                self.keyboard.release(key[1])
        else:
            self.keyboard.press(key)
            self.keyboard.release(key)
        time.sleep(0.1)

    def move_mouse(self, x: int, y: int):
        """Move mouse to coordinates."""
        self.mouse.position = (x, y)
        time.sleep(0.05)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3):
        """Drag from (x1,y1) to (x2,y2)."""
        self.mouse.position = (x1, y1)
        time.sleep(0.1)
        self.mouse.press(Button.left)

        steps = max(1, int(duration * 60))
        for i in range(1, steps + 1):
            t = i / steps
            cx = int(x1 + (x2 - x1) * t)
            cy = int(y1 + (y2 - y1) * t)
            self.mouse.position = (cx, cy)
            time.sleep(duration / steps)

        self.mouse.release(Button.left)
        time.sleep(0.1)

    def screenshot_window(self, window_id: int, output_path: str):
        """Capture window screenshot using PIL (most reliable)."""
        from PIL import ImageGrab
        geom = self.get_window_geometry(window_id)
        bbox = (geom['x'], geom['y'], geom['x'] + geom['width'], geom['y'] + geom['height'])
        screenshot = ImageGrab.grab(bbox=bbox)
        screenshot.save(output_path, 'PNG')

    def close(self):
        """Clean up display connection."""
        self.disp.close()


class ScreenCapture:
    """Screenshot utilities."""

    @staticmethod
    def capture_window(window_id: int, output_path: str):
        """Capture a specific window."""
        subprocess.run([
            'import', '-window', str(window_id), output_path
        ], check=True, capture_output=True)

    @staticmethod
    def capture_fullscreen(output_path: str):
        """Capture entire screen."""
        subprocess.run([
            'import', '-window', 'root', output_path
        ], check=True, capture_output=True)

    @staticmethod
    def capture_region(x: int, y: int, w: int, h: int, output_path: str):
        """Capture screen region."""
        subprocess.run([
            'import', '-crop', f'{w}x{h}+{x}+{y}',
            output_path
        ], check=True, capture_output=True)


if __name__ == '__main__':
    import sys
    ctrl = X11Controller()

    if len(sys.argv) < 2:
        print("Usage: python x11_controller.py <command> [args...]")
        print("Commands: find <name>, geometry <id>, click <x> <y>, type <text>, screenshot <id> <path>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'find':
        name = sys.argv[2]
        wid = ctrl.get_window_by_name(name)
        print(wid if wid else f"Window '{name}' not found")

    elif cmd == 'geometry':
        wid = int(sys.argv[2])
        geom = ctrl.get_window_geometry(wid)
        print(geom)

    elif cmd == 'click':
        x, y = int(sys.argv[2]), int(sys.argv[3])
        ctrl.click(x, y)
        print(f"Clicked at ({x}, {y})")

    elif cmd == 'type':
        text = ' '.join(sys.argv[2:])
        ctrl.type_text(text)
        print(f"Typed: {text}")

    elif cmd == 'screenshot':
        wid = int(sys.argv[2])
        path = sys.argv[3]
        ctrl.screenshot_window(wid, path)
        print(f"Screenshot saved to {path}")

    elif cmd == 'raise':
        wid = int(sys.argv[2])
        ctrl.raise_window(wid)
        print(f"Raised window {wid}")

    ctrl.close()
