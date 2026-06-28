#!/home/hongbin/.openclaw/workspace/vlm-gui-automation/venv/bin/python3
"""
QCAD/LibreCAD VLM-Based GUI Automation Agent

Screenshot -> VLM (Ollama) -> Parse Action -> Execute (X11/xdotool) -> Loop

Usage:
    python qcad_vlm_agent.py "Draw a rectangle from (0,0) to (100,100)" --model qwen3.5:9b
    python qcad_vlm_agent.py "Select the line tool" --use-local --local-model gemma3:4b

Architecture:
    1. Capture window screenshot
    2. Send to VLM with task prompt
    3. Parse VLM response into structured action
    4. Execute action via X11 controller
    5. Verify result with follow-up screenshot (optional)
    6. Loop until task complete or max steps reached
"""

import os
import sys
import json
import time
import base64
import argparse
import tempfile
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

# Add local modules to path
sys.path.insert(0, str(Path(__file__).parent))

from x11_controller import X11Controller, ScreenCapture
from ollama_client import OllamaClient
from coordinate_cache import CoordinateCache


class QCADVLMAgent:
    """
    Main agent that orchestrates the screenshot -> VLM -> action loop.
    """

    def __init__(
        self,
        model: str = "qwen3.5:9b",
        ollama_url: str = "http://localhost:11434",
        cache_file: Optional[str] = None,
        window_name: str = "QCAD",
        use_cloud: bool = True,
    ):
        self.model = model
        self.ollama_url = ollama_url
        self.use_cloud = use_cloud
        self.window_name = window_name

        self.window = X11Controller()
        self.capture = ScreenCapture()
        self.client = OllamaClient(base_url=ollama_url)
        self.cache = CoordinateCache(cache_file)

    def find_window(self) -> Optional[int]:
        """Find the QCAD/LibreCAD window ID."""
        window = self.window.get_window_by_name(self.window_name)
        if not window:
            # Try alternative names
            for alt in ["LibreCAD", "QCAD Professional", "qcad"]:
                window = self.window.get_window_by_name(alt)
                if window:
                    break
        return window

    def capture_window(self, window_id: int, max_size: int = 1280) -> str:
        """Capture screenshot of window, resize if too large, return base64."""
        from PIL import Image
        screenshot_path = f"/tmp/vlm_gui_{int(time.time())}.png"
        self.window.screenshot_window(window_id, screenshot_path)

        # Resize if too large to reduce VLM processing time
        with Image.open(screenshot_path) as img:
            w, h = img.size
            if w > max_size or h > max_size:
                ratio = min(max_size / w, max_size / h)
                new_size = (int(w * ratio), int(h * ratio))
                img = img.resize(new_size, Image.LANCZOS)
                img.save(screenshot_path, 'PNG')
                print(f"  Resized screenshot to {new_size}")

        with open(screenshot_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def build_prompt(self, task: str, previous_actions: List[str] = None) -> str:
        """Build the VLM prompt for GUI automation."""
        actions_text = ""
        if previous_actions:
            actions_text = "\nPreviously taken actions:\n" + "\n".join(
                [f"  {i+1}. {a}" for i, a in enumerate(previous_actions[-5:])]
            )

        prompt = f"""You are a GUI automation assistant. You can see a screenshot of QCAD, a CAD application.

Task: {task}{actions_text}

QCAD LAYOUT GUIDE:
- LEFT EDGE (x: 0-60): Vertical toolbar with drawing tools — Line (diagonal line icon), Circle, Arc, Rectangle, etc.
- TOP EDGE (y: 0-80): Horizontal toolbar with file, edit, view, layer tools
- MAIN CANVAS: Large area to the right of left toolbar and below top toolbar
- Tool icons are small squares (~24-32px each) arranged vertically on the left
- The LINE tool icon looks like a simple diagonal line \\/

Your job: Find the tool needed for the task, report its center coordinates.

Return your response in this exact format:
OBSERVATION: <what you see in the screenshot>
ACTION: <click|type|drag|menu_select|key_press|done>
TARGET: <description of UI element>
COORDINATES: (x, y)  [center of the element, relative to the window screenshot]
TEXT: <text to type>  [if ACTION is type or menu_select]
REASONING: <why you chose this action>

Rules:
- Coordinates must be integers within the window bounds
- For drawing tools, check the LEFT toolbar first (small icons on left edge, x: 15-50)
- The LINE tool is typically the first or second icon in the left toolbar
- If the task is already complete, use ACTION: done
- Be precise: estimate the exact center of the icon
"""
        return prompt

    def parse_response(self, response: str) -> Dict[str, Any]:
        """Parse VLM response into structured action dict."""
        result = {
            "observation": "",
            "action": "",
            "target": "",
            "coordinates": None,
            "text": "",
            "reasoning": "",
        }

        for line in response.strip().split("\n"):
            line = line.strip()
            if line.startswith("OBSERVATION:"):
                result["observation"] = line[12:].strip()
            elif line.startswith("ACTION:"):
                result["action"] = line[7:].strip().lower()
            elif line.startswith("TARGET:"):
                result["target"] = line[7:].strip()
            elif line.startswith("COORDINATES:"):
                coord_str = line[12:].strip().strip("()")
                try:
                    x, y = map(int, coord_str.split(","))
                    result["coordinates"] = (x, y)
                except (ValueError, IndexError):
                    pass
            elif line.startswith("TEXT:"):
                result["text"] = line[5:].strip()
            elif line.startswith("REASONING:"):
                result["reasoning"] = line[10:].strip()

        return result

    def execute_action(self, action: Dict[str, Any], window_id: int) -> bool:
        """Execute the parsed action using X11 controller."""
        action_type = action.get("action", "")
        coords = action.get("coordinates")
        text = action.get("text", "")

        if action_type == "done":
            print("✓ Task completed")
            return True

        if action_type == "click" and coords:
            # Get window position to convert relative to absolute
            geom = self.window.get_window_geometry(window_id)
            abs_x = geom["x"] + coords[0]
            abs_y = geom["y"] + coords[1]
            print(f"  → Clicking at ({coords[0]}, {coords[1]}) [absolute: {abs_x}, {abs_y}]")
            self.window.click(abs_x, abs_y)
            return False

        elif action_type == "type" and text:
            print(f"  → Typing: {text}")
            self.window.type_text(text)
            return False

        elif action_type == "key_press" and text:
            print(f"  → Key press: {text}")
            self.window.key_press(text)
            return False

        elif action_type == "drag" and coords:
            # For drag we'd need start and end coords - VLM should provide both
            print(f"  → Drag action at {coords} (not fully implemented)")
            return False

        elif action_type == "menu_select" and text:
            print(f"  → Menu select: {text}")
            # Could implement Alt+key sequences here
            return False

        else:
            print(f"  → Unknown or incomplete action: {action_type}")
            return False

    def run_task(
        self,
        task: str,
        max_steps: int = 10,
        delay: float = 2.0,
    ) -> bool:
        """
        Run a task using the VLM loop.

        Returns True if completed successfully.
        """
        print(f"🔍 Finding {self.window_name} window...")
        window_id = self.find_window()
        if not window_id:
            print(f"❌ Could not find window matching '{self.window_name}'")
            print("   Try: xwininfo -tree -root | grep -i qcad")
            return False

        print(f"✓ Found window ID: {window_id}")
        self.window.raise_window(window_id)
        time.sleep(0.5)

        previous_actions = []

        for step in range(max_steps):
            print(f"\n--- Step {step + 1}/{max_steps} ---")

            # Capture screenshot
            print("📸 Capturing screenshot...")
            screenshot_b64 = self.capture_window(window_id)

            # Check cache first for known elements
            cache_key = f"{task}_{step}"
            cached = self.cache.get(self.window_name, (0, 0), cache_key)

            if cached:
                print("⚡ Using cached coordinates")
                action = cached
            else:
                # Query VLM
                prompt = self.build_prompt(task, previous_actions)
                print("🤖 Querying VLM...")

                # Save screenshot to temp file for API
                screenshot_path = f"/tmp/vlm_screenshot_{int(time.time())}.png"
                with open(screenshot_path, "wb") as f:
                    f.write(base64.b64decode(screenshot_b64))

                response = self.client.chat_with_image(
                    model=self.model,
                    prompt=prompt,
                    image_path=screenshot_path,
                )
                action = self.parse_response(response)

                # Cache the result
                self.cache.set(
                    self.window_name,
                    (0, 0),  # Will update with actual size
                    cache_key,
                    action.get("coordinates", (0, 0)),
                    action.get("action", "unknown"),
                )

            print(f"Observation: {action.get('observation', 'N/A')[:100]}...")
            print(f"Action: {action.get('action', 'N/A')}")
            print(f"Target: {action.get('target', 'N/A')}")
            if action.get("coordinates"):
                print(f"Coordinates: {action['coordinates']}")

            # Execute action
            is_done = self.execute_action(action, window_id)
            previous_actions.append(
                f"{action.get('action', '?')} on {action.get('target', '?')}"
            )

            if is_done:
                return True

            time.sleep(delay)

        print(f"\n⚠️ Reached max steps ({max_steps})")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="QCAD VLM GUI Automation Agent"
    )
    parser.add_argument(
        "task",
        help="Task description (e.g., 'Draw a rectangle', 'Select line tool')",
    )
    parser.add_argument(
        "--model",
        default="qwen3.5:9b",
        help="Ollama model to use (default: qwen3.5:9b)",
    )
    parser.add_argument(
        "--local-model",
        default="gemma3:4b",
        help="Local fallback model (default: gemma3:4b)",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://localhost:11434",
        help="Ollama API URL",
    )
    parser.add_argument(
        "--cache",
        default=str(Path.home() / ".openclaw" / "workspace" / "vlm-gui-automation" / "coords_cache.json"),
        help="Coordinate cache file path",
    )
    parser.add_argument(
        "--window-name",
        default="QCAD",
        help="Window name to search for",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=10,
        help="Maximum number of VLM steps",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Delay between steps (seconds)",
    )
    parser.add_argument(
        "--use-local",
        action="store_true",
        help="Use local model instead of cloud",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable coordinate caching",
    )

    args = parser.parse_args()

    # Resolve cache path
    cache = None if args.no_cache else args.cache

    # Select model
    model = args.local_model if args.use_local else args.model

    print("🚀 QCAD VLM Agent")
    print(f"   Model: {model}")
    print(f"   Ollama: {args.ollama_url}")
    print(f"   Cache: {cache or 'disabled'}")
    print(f"   Task: {args.task}")
    print()

    agent = QCADVLMAgent(
        model=model,
        ollama_url=args.ollama_url,
        cache_file=cache,
        window_name=args.window_name,
        use_cloud=not args.use_local,
    )

    success = agent.run_task(
        task=args.task,
        max_steps=args.max_steps,
        delay=args.delay,
    )

    if success:
        print("\n✅ Task completed successfully")
        sys.exit(0)
    else:
        print("\n❌ Task did not complete")
        sys.exit(1)


if __name__ == "__main__":
    main()
