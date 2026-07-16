#!/usr/bin/env python3
"""
qcad_vlm_verifier.py — Open DWG in QCAD → screenshot → VLM verification via Ollama

Uses xdotool + ImageMagick for window management and Ollama vision model (qwen2.5vl)
for visual analysis of CAD drawings.

Usage:
    # Verify F174 ground lines are present
    python3 qcad_vlm_verifier.py /path/to/1_FINAL_v10.dwg \
        --question "Are the two short ground-reference lines on the right side of F174 present?"

    # Generic verification with any question
    python3 qcad_vlm_verifier.py /path/to/drawing.dwg \
        --question "Does this drawing show PLC22 and CA-1452 labels in terminal row 7?"

    # Calibrate screenshot coordinates
    python3 qcad_vlm_verifier.py /path/to/drawing.dwg --calibrate
"""

import os
import sys
import time
import json
import base64
import subprocess
from pathlib import Path

# Ensure env vars (critical when running from non-interactive shells)
os.environ.setdefault("DISPLAY", ":1")
os.environ.setdefault("XAUTHORITY", "/run/user/1000/gdm/Xauthority")
os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")

QCAD_BIN = os.environ.get("QCAD_BIN",
    os.path.expanduser("~/opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.2.15:11434")
VISION_MODEL = os.environ.get("VISION_MODEL", "gemma4:31b-cloud")


class QCADVLMVerifier:
    """Open DWG in QCAD, screenshot, send to VLM for visual verification."""

    def __init__(self, qcad_bin=QCAD_BIN, ollama_url=OLLAMA_URL, model=VISION_MODEL,
                 window_title="QCAD", screenshot_dir="/tmp/qcad_vlm_verify"):
        self.qcad_bin = qcad_bin
        self.ollama_url = ollama_url
        self.model = model
        self.window_title = window_title
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._window_id = None
        self._step = 0

    def _get_window_id(self, timeout=15):
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
                                            if w > 300 and h > 200:
                                                self._window_id = wid
                                                return wid
                    if ids:
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
            time.sleep(0.3)
            return True
        except Exception:
            return False

    def kill_qcad(self):
        subprocess.run(["pkill", "-9", "-f", "qcad-bin"], capture_output=True)
        time.sleep(1)
        self._window_id = None

    def launch(self, filepath=None):
        self.kill_qcad()
        cmd = [self.qcad_bin]
        if filepath:
            cmd.append(str(filepath))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        time.sleep(5)
        self._window_id = None
        wid = self._get_window_id(timeout=15)
        if not wid:
            print("WARNING: QCAD window not detected, retrying...", file=sys.stderr)
            time.sleep(5)
            wid = self._get_window_id(timeout=10)
        self.focus()
        return {"pid": proc.pid, "window_id": wid}

    def screenshot(self, label=""):
        self.focus()
        self._step += 1
        filename = self.screenshot_dir / f"step_{self._step:02d}_{label}.png"
        wid = self._get_window_id()
        if not wid:
            raise RuntimeError("QCAD window not found for screenshot")
        try:
            result = subprocess.run(
                ["import", "-window", wid, str(filename)],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and filename.exists():
                return str(filename)
        except FileNotFoundError:
            raise RuntimeError("ImageMagick `import` not found. Install: sudo apt install imagemagick")
        except Exception as e:
            raise RuntimeError(f"Screenshot failed: {e}")
        raise RuntimeError(f"Screenshot failed: {result.stderr}")

    def _encode_image(self, png_path):
        with open(png_path, "rb") as f:
            return base64.b64encode(f.read()).decode()

    def query_vlm(self, image_path, question, max_tokens=4096):
        image_b64 = self._encode_image(image_path)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": question,
                    "images": [image_b64]
                }
            ],
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0.3
            }
        }
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.ollama_url}/api/chat",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode())
                answer = result.get("message", {}).get("content", "")
                return {
                    "answer": answer,
                    "model": self.model,
                    "done": result.get("done", False),
                    "eval_count": result.get("eval_count", 0),
                    "raw": result
                }
        except Exception as e:
            return {
                "answer": f"ERROR: {str(e)}",
                "model": self.model,
                "error": str(e),
                "done": False
            }

    def verify(self, dwg_path, question, zoom_extents=True, wait_seconds=3):
        dwg_path = Path(dwg_path).resolve()
        if not dwg_path.exists():
            raise FileNotFoundError(f"DWG not found: {dwg_path}")
        print(f"Launching QCAD with {dwg_path.name}...")
        info = self.launch(dwg_path)
        print(f"  QCAD window: {info.get('window_id', 'unknown')}")
        time.sleep(wait_seconds)
        if zoom_extents:
            print("Zooming to extents...")
            self._xdotool_key("e", ["ctrl"])
            time.sleep(2)
        print("Capturing screenshot...")
        png = self.screenshot("verify")
        print(f"  Screenshot: {png}")
        print(f"Querying VLM ({self.model})...")
        full_question = (
            f"You are examining an electrical CAD drawing (DWG file opened in QCAD).\n\n"
            f"QUESTION: {question}\n\n"
            f"Please answer clearly: YES or NO, then explain what you see. "
            f"Be specific about labels, lines, symbols, and their positions."
        )
        result = self.query_vlm(png, full_question)
        answer = result.get("answer", "")
        pass_ = None
        ans_lower = answer.lower()
        if "yes" in ans_lower[:50]:
            pass_ = True
        elif "no" in ans_lower[:50]:
            pass_ = False
        self.kill_qcad()
        print(f"\n--- VLM Response ---")
        print(f"PASS: {'YES' if pass_ else ('NO' if pass_ is False else 'UNCLEAR')}")
        print(f"Answer: {answer[:500]}...")
        print(f"--------------------")
        return {
            "pass": pass_,
            "answer": answer,
            "screenshot": png,
            "model": self.model,
            "eval_count": result.get("eval_count", 0)
        }

    def _xdotool_key(self, key, modifiers=None):
        cmd = ["xdotool", "key"]
        if modifiers:
            for m in modifiers:
                cmd.append(f"{m}+{key}")
        else:
            cmd.append(key)
        subprocess.run(cmd, capture_output=True, text=True, timeout=5)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="QCAD screenshot + VLM verification")
    parser.add_argument("dwg", help="DWG file to verify")
    parser.add_argument("--question", "-q", required=True, help="Question to ask the VLM")
    parser.add_argument("--model", default=VISION_MODEL, help="Ollama vision model")
    parser.add_argument("--ollama-url", default=OLLAMA_URL, help="Ollama API URL")
    parser.add_argument("--no-zoom", action="store_true", help="Skip Ctrl+E zoom extents")
    parser.add_argument("--wait", type=int, default=3, help="Seconds to wait after open")
    parser.add_argument("--calibrate", action="store_true",
                        help="Launch QCAD with DWG, screenshot, and exit (no VLM)")
    parser.add_argument("--keep-qcad", action="store_true",
                        help="Keep QCAD running after verification")
    args = parser.parse_args()
    verifier = QCADVLMVerifier(model=args.model, ollama_url=args.ollama_url)
    if args.calibrate:
        info = verifier.launch(args.dwg)
        print(f"Launched — window {info.get('window_id')}")
        time.sleep(args.wait)
        if not args.no_zoom:
            verifier._xdotool_key("e", ["ctrl"])
            time.sleep(2)
        png = verifier.screenshot("calibration")
        print(f"Calibration screenshot: {png}")
        if not args.keep_qcad:
            verifier.kill_qcad()
        return
    verdict = verifier.verify(
        args.dwg, args.question,
        zoom_extents=not args.no_zoom,
        wait_seconds=args.wait
    )
    if args.keep_qcad:
        print(f"\nQCAD kept running. Window ID: {verifier._get_window_id()}")
    sys.exit(0 if verdict.get("pass") else 1)


if __name__ == "__main__":
    main()
