"""VLM visual verifier using QCAD + Ollama vision."""
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
    """Open DWG in QCAD, screenshot, ask VLM."""

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
        self.model = model or os.environ.get("VISION_MODEL", "qwen2.5vl:latest")
        self.window_title = window_title
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._window_id = None
        self._step = 0

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

    def _get_window_id(self, timeout: int = 15) -> Optional[str]:
        if self._window_id:
            return self._window_id
        for _ in range(timeout * 2):
            try:
                result = subprocess.run(
                    ["xdotool", "search", "--onlyvisible", "--name", self.window_title],
                    capture_output=True, text=True, timeout=5,
                    env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
                )
                if result.returncode == 0 and result.stdout.strip():
                    ids = result.stdout.strip().split("\n")
                    for wid in ids:
                        geom = subprocess.run(
                            ["xdotool", "getwindowgeometry", wid],
                            capture_output=True, text=True, timeout=5
                        )
                        if "Geometry:" in geom.stdout:
                            for line in geom.stdout.split("\n"):
                                if "Geometry:" in line:
                                    parts = line.strip().split(":")
                                    if len(parts) >= 2:
                                        wh = parts[1].strip().split("x")
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

    def focus(self) -> bool:
        wid = self._get_window_id()
        if not wid:
            return False
        subprocess.run(["xdotool", "windowactivate", wid, "windowraise", wid],
                       capture_output=True, text=True, timeout=5)
        time.sleep(0.3)
        return True

    def kill_qcad(self) -> None:
        subprocess.run(["pkill", "-9", "-f", "qcad-bin"], capture_output=True)
        time.sleep(1)
        self._window_id = None

    def launch(self, filepath: str = None) -> Dict:
        self.kill_qcad()
        qcad = self._find_qcad()
        cmd = [qcad]
        if filepath:
            cmd.append(str(filepath))
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        time.sleep(5)
        wid = self._get_window_id(timeout=15)
        self.focus()
        return {"pid": proc.pid, "window_id": wid}

    def screenshot(self, label: str = "") -> str:
        self.focus()
        self._step += 1
        filename = self.screenshot_dir / f"step_{self._step:02d}_{label}.png"
        wid = self._get_window_id()
        if not wid:
            raise RuntimeError("QCAD window not found")
        result = subprocess.run(["import", "-window", wid, str(filename)],
                                capture_output=True, text=True, timeout=10)
        if result.returncode != 0 or not filename.exists():
            raise RuntimeError(f"Screenshot failed: {result.stderr}")
        return str(filename)

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

    def verify(self, dwg_path: str, question: str, zoom_extents: bool = True,
               wait_seconds: int = 3) -> Dict:
        dwg_path = Path(dwg_path).resolve()
        if not dwg_path.exists():
            raise FileNotFoundError(dwg_path)
        self.launch(str(dwg_path))
        time.sleep(wait_seconds)
        if zoom_extents:
            wid = self._get_window_id()
            if wid:
                subprocess.run(["xdotool", "key", "--window", wid, "ctrl+e"],
                               capture_output=True, text=True, timeout=5)
            time.sleep(2)
        png = self.screenshot("verify")
        full_question = (
            f"You are examining an electrical CAD drawing (DWG file opened in QCAD).\n\n"
            f"QUESTION: {question}\n\n"
            f"Please answer clearly: YES or NO, then explain what you see."
        )
        result = self.query_vlm(png, full_question)
        answer = result.get("answer", "").lower()
        passed = "yes" in answer[:50] if answer else None
        self.kill_qcad()
        return {"pass": passed, "answer": result.get("answer"), "screenshot": png,
                "model": self.model, "eval_count": result.get("eval_count", 0)}
