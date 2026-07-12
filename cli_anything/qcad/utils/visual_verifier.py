"""VLM visual verifier using QCAD GUI + cua-driver + Ollama vision.

Uses cua-driver (via CLI) for window detection and AT-SPI tree verification,
and ImageMagick import for reliable screenshots. No focus stealing — all
operations run in the background via cua-driver's AT-SPI path.

Requires:
  - cua-driver installed (hermes computer-use install)
  - ImageMagick import (sudo apt install imagemagick)
  - QCAD Pro (Qt6) at ~/opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad
"""
import base64
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Dict, Optional


class QcadVlmVerifier:
    """Open DWG in QCAD with AT-SPI bridge, screenshot via cua-driver, ask VLM.

    Key differences from xdotool-based approach:
      - Launches QCAD with QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 (activates AT-SPI bridge)
      - Finds window via cua-driver list_windows (no xdotool search)
      - Verifies AT-SPI tree via get_window_state (confirms widgets are accessible)
      - No focus stealing (no windowactivate/windowraise)
      - Screenshot via cua-driver capture or ImageMagick import
    """

    def __init__(
        self,
        qcad_bin: Optional[str] = None,
        ollama_url: str = None,
        model: str = None,
        window_title: str = "QCAD",
        screenshot_dir: str = "/tmp/qcad_vlm_verify",
    ):
        self.qcad_bin = qcad_bin
        self.ollama_url = ollama_url or os.environ.get("OLLAMA_URL", "http://192.168.2.15:11434")
        self.model = model or os.environ.get("VISION_MODEL", "gemma4:31b-cloud")
        self.window_title = window_title
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._pid = None
        self._window_id = None
        self._window_dims = None
        self._step = 0

    # ── helpers ──────────────────────────────────────────────

    def _find_qcad(self) -> str:
        if self.qcad_bin and Path(self.qcad_bin).exists():
            return self.qcad_bin
        candidates = [
            shutil.which("qcad"),
            str(Path.home() / "opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad"),
        ]
        for c in candidates:
            if c and Path(c).exists():
                return c
        raise RuntimeError("QCAD binary not found")

    def _cua_call(self, tool: str, args: dict, timeout: int = 15) -> dict:
        """Run a cua-driver tool via CLI and return parsed JSON."""
        try:
            result = subprocess.run(
                ["cua-driver", "call", tool, json.dumps(args)],
                capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError:
            return {"error": "cua-driver not found — run 'hermes computer-use install'"}
        except subprocess.TimeoutExpired:
            return {"error": f"cua-driver {tool} timed out after {timeout}s"}
        except Exception as e:
            return {"error": str(e)}
        if result.returncode != 0:
            return {"error": f"cua-driver exit {result.returncode}: {result.stderr[:200]}"}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"error": f"cua-driver returned non-JSON: {result.stdout[:200]}"}

    # ── lifecycle ────────────────────────────────────────────

    def kill_qcad(self) -> None:
        subprocess.run(["pkill", "-9", "-f", "qcad-bin"], capture_output=True)
        time.sleep(1)
        self._pid = None
        self._window_id = None
        self._window_dims = None

    def launch(self, filepath: str = None, wait_seconds: int = 10) -> Dict:
        """Launch QCAD with AT-SPI bridge activated and find its window via cua-driver.

        Returns dict with pid, window_id, width, height, elements (number of
        AT-SPI nodes found), or error.
        """
        self.kill_qcad()
        qcad = self._find_qcad()

        cmd = [qcad]
        if filepath:
            cmd.append(str(filepath))

        # KEY: activate Qt6's AT-SPI bridge (compiled into bundled Qt 6.11.0)
        env = os.environ.copy()
        env["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = "1"
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")

        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, env=env,
        )
        self._pid = proc.pid
        time.sleep(wait_seconds)

        # Find the QCAD window via cua-driver list_windows
        wid = self._find_window_with_retry(timeout=15)
        if not wid:
            return {"pid": proc.pid, "window_id": None,
                    "error": "QCAD window not found via cua-driver"}

        # Verify the AT-SPI tree is populated
        tree = self._cua_call("get_window_state", {"pid": self._pid, "window_id": wid,
                                                    "max_elements": 10, "include_screenshot": False})
        element_count = None
        if "elements" in tree:
            element_count = len(tree["elements"])
        elif "element_count" in tree:
            element_count = tree["element_count"]

        return {
            "pid": proc.pid,
            "window_id": wid,
            "width": self._window_dims[0] if self._window_dims else None,
            "height": self._window_dims[1] if self._window_dims else None,
            "elements": element_count,
            "atspi_ok": element_count is not None and element_count > 10,
        }

    def _find_window_with_retry(self, timeout: int = 30) -> Optional[int]:
        """Poll cua-driver list_windows until a QCAD window with the expected title appears.
        
        Note: the 'qcad' launcher script forks 'qcad-bin' with a different PID,
        so we search ALL windows (not filtered by PID) and match by title only.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Don't filter by pid — qcad wrapper forks qcad-bin with a different pid
            result = self._cua_call("list_windows", {})
            windows = result.get("windows", [])
            for w in windows:
                title = w.get("title", "")
                if self.window_title.lower() in title.lower():
                    wid = w.get("window_id")
                    self._window_id = wid
                    self._window_dims = (w.get("width"), w.get("height"))
                    # Update self._pid to the actual qcad-bin pid
                    self._pid = w.get("pid", self._pid)
                    return wid
            time.sleep(0.5)
        return None

    # ── screenshot ───────────────────────────────────────────

    def screenshot(self, label: str = "") -> str:
        """Capture QCAD window screenshot.

        Tries xdotool+ImageMagick import first (fast, reliable on Qt6).
        Falls back to cua-driver get_window_state with a short timeout.
        """
        self._step += 1
        filename = self.screenshot_dir / f"step_{self._step:02d}_{label}.png"

        # Method 1: xdotool search + ImageMagick import (works reliably on Qt6)
        try:
            xid_result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", self.window_title],
                capture_output=True, text=True, timeout=5,
            )
            if xid_result.returncode == 0 and xid_result.stdout.strip():
                # Use the last matching window ID (QCAD has multiple X11 windows;
                # the main one with the drawing is typically the last)
                xid = xid_result.stdout.strip().split("\n")[-1]
                result = subprocess.run(
                    ["import", "-window", xid, str(filename)],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0 and filename.exists() and filename.stat().st_size > 1000:
                    return str(filename)
        except Exception:
            pass

        # Method 2: cua-driver get_window_state with screenshot (has hung on QCAD's
        # massive AT-SPI tree in the past, so use a short timeout as last resort)
        if self._pid and self._window_id:
            try:
                result = subprocess.run(
                    ["cua-driver", "call", "get_window_state",
                     json.dumps({"pid": self._pid, "window_id": self._window_id,
                                 "screenshot_out_file": str(filename)})],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0 and filename.exists() and filename.stat().st_size > 1000:
                    return str(filename)
            except Exception:
                pass

        raise RuntimeError("All screenshot methods failed")

    # ── keyboard input ───────────────────────────────────────

    def _press_key(self, key: str, modifiers: list = None, delay: float = 0.5) -> bool:
        """Send a keypress to QCAD via xdotool (Qt6-compatible path).

        Uses xdotool keydown+keyup pattern which works on Qt6 (unlike
        cua-driver press_key/hotkey which use XSendEvent — blocked by Qt6).
        """
        try:
            wid_str = str(self._window_id) if self._window_id else ""
            keys = "+".join((modifiers or []) + [key])
            subprocess.run(
                ["xdotool", "key", "--window", wid_str, keys] if wid_str
                else ["xdotool", "key", keys],
                capture_output=True, text=True, timeout=5,
            )
            time.sleep(delay)
            return True
        except Exception:
            return False

    # ── VLM query ────────────────────────────────────────────

    def query_vlm(self, image_path: str, question: str, max_tokens: int = 4096) -> Dict:
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": question, "images": [image_b64]}],
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.3},
        }
        try:
            req = urllib.request.Request(
                f"{self.ollama_url}/api/chat",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode())
                return {
                    "answer": result.get("message", {}).get("content", ""),
                    "model": self.model,
                    "done": result.get("done", False),
                    "eval_count": result.get("eval_count", 0),
                    "raw": result,
                }
        except Exception as e:
            return {"answer": f"ERROR: {e}", "model": self.model, "error": str(e), "done": False}

    # ── full verification ────────────────────────────────────

    def verify(self, dwg_path: str, question: str, zoom_extents: bool = True,
               wait_seconds: int = 3) -> Dict:
        """Open DWG in QCAD (with AT-SPI bridge), screenshot, and ask VLM."""
        dwg_path = Path(dwg_path).resolve()
        if not dwg_path.exists():
            raise FileNotFoundError(dwg_path)

        info = self.launch(str(dwg_path))
        if info.get("error"):
            return {"pass": None, "error": info["error"], "launch_info": info}

        time.sleep(wait_seconds)

        # Zoom to extents via Ctrl+E (works with xdotool keydown+keyup on Qt6)
        if zoom_extents:
            self._press_key("e", modifiers=["ctrl"], delay=1.5)

        # Screenshot via cua-driver (background, no focus steal)
        try:
            png = self.screenshot("verify")
        except RuntimeError as e:
            return {"pass": None, "error": f"Screenshot failed: {e}"}

        full_question = (
            f"You are examining an electrical CAD drawing (DWG file opened in QCAD).\n\n"
            f"QUESTION: {question}\n\n"
            f"Please answer clearly: YES or NO, then explain what you see. "
            f"Be specific about labels, lines, symbols, and their positions."
        )
        result = self.query_vlm(png, full_question)
        answer = result.get("answer", "").lower()
        passed = None
        if "yes" in answer[:50]:
            passed = True
        elif "no" in answer[:50]:
            passed = False

        # Keep QCAD running for inspection — caller decides whether to kill
        return {
            "pass": passed,
            "answer": result.get("answer"),
            "screenshot": png,
            "model": self.model,
            "eval_count": result.get("eval_count", 0),
            "atspi_ok": info.get("atspi_ok"),
            "elements": info.get("elements"),
            "window": {"pid": info.get("pid"), "window_id": info.get("window_id"),
                       "width": info.get("width"), "height": info.get("height")},
        }
