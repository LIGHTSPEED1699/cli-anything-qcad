#!/usr/bin/env python3
"""
QCAD Action Executor

Executes identified actions in QCAD via X11 automation.
Supports: click, double_click, type, select, move (drag), delete.

Usage:
    python qcad_action_executor.py --action click --coords 500,400 --window-id 12345678
    python qcad_action_executor.py --action type --coords 500,400 --text "NT-110" --window-id 12345678
    python qcad_action_executor.py --action delete --coords 500,400 --window-id 12345678
    python qcad_action_executor.py --tasks /tmp/vlm_matches.json --window-id 12345678 --dry-run
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

sys.path.insert(0, str(Path(__file__).parent))

from x11_controller import X11Controller


class QCADActionExecutor:
    """Executes actions in QCAD via X11."""

    def __init__(self, qcad_window_name: str = "QCAD", window_id: Optional[int] = None):
        self.x11 = X11Controller()
        self.qcad_window_name = qcad_window_name
        self.window_id = window_id or self._find_window()

    def _find_window(self) -> Optional[int]:
        """Find QCAD window ID."""
        for name in ["QCAD", "QCAD Professional", "QCAD Trial", "QCAD 3", "RivieraWaves"]:
            wid = self.x11.get_window_by_name(name)
            if wid:
                print(f"Found QCAD window: {wid} (name: {name})")
                return wid
        return None

    def _ensure_window(self):
        """Raise error if no QCAD window."""
        if not self.window_id:
            raise RuntimeError("QCAD window not found. Is QCAD running?")

    def _to_absolute(self, coords: Tuple[int, int]) -> Tuple[int, int]:
        """Convert QCAD screenshot-relative coords to screen-absolute."""
        self._ensure_window()
        geom = self.x11.get_window_geometry(self.window_id)
        abs_x = geom["x"] + coords[0]
        abs_y = geom["y"] + coords[1]
        return (abs_x, abs_y)

    def click(self, coords: Tuple[int, int], dry_run: bool = False) -> bool:
        """Click at coordinates."""
        abs_x, abs_y = self._to_absolute(coords)
        print(f"  🖱️  Click at QCAD({coords[0]},{coords[1]}) → screen({abs_x},{abs_y})")
        if dry_run:
            return True
        self.x11.click(abs_x, abs_y)
        return True

    def double_click(self, coords: Tuple[int, int], dry_run: bool = False) -> bool:
        """Double-click at coordinates."""
        abs_x, abs_y = self._to_absolute(coords)
        print(f"  🖱️  Double-click at QCAD({coords[0]},{coords[1]}) → screen({abs_x},{abs_y})")
        if dry_run:
            return True
        self.x11.double_click(abs_x, abs_y)
        return True

    def type_text(self, coords: Tuple[int, int], text: str, dry_run: bool = False) -> bool:
        """Click then type text."""
        if not self.click(coords, dry_run):
            return False
        print(f"  ⌨️  Type: \"{text}\"")
        if dry_run:
            return True
        time.sleep(0.3)
        self.x11.type_text(text)
        return True

    def select(self, coords: Tuple[int, int], dry_run: bool = False) -> bool:
        """Select entity (click to select)."""
        return self.click(coords, dry_run)

    def delete(self, coords: Tuple[int, int], dry_run: bool = False) -> bool:
        """Select then delete entity."""
        if not self.click(coords, dry_run):
            return False
        print(f"  🗑️  Press Delete")
        if dry_run:
            return True
        time.sleep(0.2)
        self.x11.key_press("delete")
        return True

    def move_entity(self, coords_from: Tuple[int, int], coords_to: Tuple[int, int], dry_run: bool = False) -> bool:
        """Drag entity from one position to another."""
        abs_from = self._to_absolute(coords_from)
        abs_to = self._to_absolute(coords_to)
        print(f"  ✋ Drag from QCAD{coords_from} → QCAD{coords_to}")
        if dry_run:
            return True
        self.x11.drag(abs_from[0], abs_from[1], abs_to[0], abs_to[1])
        return True

    def press_key(self, key: str, dry_run: bool = False) -> bool:
        """Press a key in QCAD."""
        print(f"  ⌨️  Key: {key}")
        if dry_run:
            return True
        self.x11.key_press(key)
        return True

    def activate_tool(self, tool_name: str, dry_run: bool = False) -> bool:
        """
        Activate a QCAD tool by name (simplified).
        Uses keyboard shortcuts where known.
        """
        shortcuts = {
            "select": "s",
            "line": "l",
            "circle": "c",
            "rectangle": "r",
            "text": "t",
            "move": "m",
            "copy": "co",
            "rotate": "ro",
            "scale": "sc",
            "delete": "e",
            "trim": "tr",
            "extend": "ex",
            "offset": "o",
            "mirror": "mi",
            "array": "ar",
            "zoom_extents": "ctrl+0",
            "properties": "ctrl+1",
        }

        shortcut = shortcuts.get(tool_name.lower())
        if shortcut:
            print(f"  🔧 Tool: {tool_name} → shortcut '{shortcut}'")
            if not dry_run:
                if "+" in shortcut:
                    self.x11.key_press(shortcut)
                else:
                    self.x11.type_text(shortcut)
                time.sleep(0.3)
            return True
        else:
            print(f"  ⚠️ No shortcut known for tool: {tool_name}")
            return False

    def execute_action(self, action: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
        """
        Execute a single action dict.
        Expected keys: action (str), coordinates (list), text (str, optional)
        Returns result dict.
        """
        action_type = action.get("action", "").lower()
        coords_raw = action.get("coordinates") or action.get("qcad_coords")

        if coords_raw is None and action_type not in ("save", "done"):
            return {"success": False, "error": "No coordinates provided"}

        coords = tuple(coords_raw) if coords_raw else (0, 0)

        result = {"success": False, "action": action_type, "coordinates": coords}

        try:
            if action_type == "click":
                result["success"] = self.click(coords, dry_run)
            elif action_type == "double_click":
                result["success"] = self.double_click(coords, dry_run)
            elif action_type == "type":
                text = action.get("text", action.get("text_input", ""))
                result["success"] = self.type_text(coords, text, dry_run)
                result["text"] = text
            elif action_type == "select":
                result["success"] = self.select(coords, dry_run)
            elif action_type == "delete":
                result["success"] = self.delete(coords, dry_run)
            elif action_type == "move":
                to_coords = action.get("to_coordinates")
                if to_coords:
                    result["success"] = self.move_entity(coords, tuple(to_coords), dry_run)
                else:
                    result["error"] = "Move action missing to_coordinates"
            elif action_type == "key":
                key = action.get("key", "")
                result["success"] = self.press_key(key, dry_run)
            elif action_type in ("save", "done"):
                result["success"] = True
            else:
                result["error"] = f"Unknown action: {action_type}"

        except Exception as e:
            result["error"] = str(e)

        return result

    def process_matches(self, matches_path: str, dry_run: bool = False) -> List[Dict[str, Any]]:
        """Process a VLM matches JSON file and execute actions."""
        with open(matches_path) as f:
            data = json.load(f)

        results = data.get("results", [])
        executed = []

        print(f"Processing {len(results)} matched entities...")
        print("=" * 60)

        for r in results:
            task_id = r.get("task_id")
            instruction = r.get("instruction", "")
            action_type = r.get("action_type", "")
            coords = r.get("coordinates")

            print(f"\n[{task_id}] {instruction}")

            if not r.get("target_found"):
                print(f"  ⚠️ Skipped: target not found by VLM")
                executed.append({
                    "task_id": task_id,
                    "success": False,
                    "reason": "target_not_found",
                })
                continue

            if not coords:
                print(f"  ⚠️ Skipped: no coordinates")
                executed.append({
                    "task_id": task_id,
                    "success": False,
                    "reason": "no_coordinates",
                })
                continue

            # Map action_type to executor action
            action_map = {
                "replace": {"action": "double_click", "text_input": r.get("new_value", "")},
                "change_property": {"action": "double_click", "text_input": r.get("new_value", "")},
                "move": {"action": "move", "to_coordinates": r.get("to_coordinates")},
                "delete": {"action": "delete"},
                "reorder": {"action": "move"},
            }

            action_def = action_map.get(action_type, {"action": "click"})
            action_def["coordinates"] = coords

            result = self.execute_action(action_def, dry_run)
            executed.append({
                "task_id": task_id,
                "instruction": instruction,
                **result,
            })

            time.sleep(0.5)

        success_count = sum(1 for e in executed if e.get("success"))
        print(f"\n{'='*60}")
        print(f"Executed: {success_count}/{len(executed)} actions succeeded")

        return executed

    def close(self):
        self.x11.close()


def main():
    parser = argparse.ArgumentParser(description="Execute actions in QCAD")
    parser.add_argument("--action", choices=["click", "double_click", "type", "select", "delete", "move"],
                        help="Action to perform")
    parser.add_argument("--coords", help="Coordinates as x,y")
    parser.add_argument("--to-coords", help="Destination for move action as x,y")
    parser.add_argument("--text", help="Text to type")
    parser.add_argument("--key", help="Key to press")
    parser.add_argument("--window-id", type=int, help="QCAD window ID")
    parser.add_argument("--tasks", "-t", help="Path to VLM matches JSON for batch processing")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without executing")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between actions")

    args = parser.parse_args()

    executor = QCADActionExecutor(window_id=args.window_id)

    if not executor.window_id:
        print("ERROR: QCAD window not found. Is QCAD running?")
        sys.exit(1)

    try:
        if args.tasks:
            if not Path(args.tasks).exists():
                print(f"ERROR: File not found: {args.tasks}")
                sys.exit(1)
            results = executor.process_matches(args.tasks, args.dry_run)
            print(f"\nResults: {json.dumps(results, indent=2)}")
        elif args.action:
            coords = tuple(int(c) for c in args.coords.split(",")) if args.coords else (0, 0)
            action = {"action": args.action, "coordinates": coords}
            if args.text:
                action["text"] = args.text
            if args.to_coords:
                action["to_coordinates"] = tuple(int(c) for c in args.to_coords.split(","))
            if args.key:
                action["key"] = args.key

            result = executor.execute_action(action, args.dry_run)
            print(json.dumps(result, indent=2))
        else:
            print("ERROR: Provide --tasks or --action")
            sys.exit(1)
    finally:
        executor.close()


if __name__ == "__main__":
    main()
