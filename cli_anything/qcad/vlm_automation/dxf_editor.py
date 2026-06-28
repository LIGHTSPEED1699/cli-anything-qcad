#!/usr/bin/env python3
"""
DXF Editor: Direct DXF modification via ezdxf.

Replaces fragile X11 GUI automation with deterministic file-level edits:
  • Replace text in TEXT/MTEXT entities
  • Replace block references (INSERT → different block name)
  • Move entities to new coordinates
  • Reorder rows (swap entity positions)
  • Delete entities

After editing, convert DXF → DWG via ODAFileConverter (headless via Xvfb):
  ODAFileConverter input_dir output_dir "ACAD2018" "DWG" "0" "1"

Usage:
    python dxf_editor.py --dxf /tmp/example.dxf --action replace --target "Blu" --new-value "Wht"
    python dxf_editor.py --dxf /tmp/example.dxf --action move --target "NT111" --to-x 100 --to-y 200
    python dxf_editor.py --dxf /tmp/example.dxf --action delete --target "NT111"

Batch mode from pipeline JSON:
    python dxf_editor.py --dxf /tmp/example.dxf --tasks /tmp/pipeline_results.json
"""

import os
import sys
import json
import shutil
import subprocess
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

try:
    import ezdxf
    from ezdxf import bbox
except ImportError:
    print("ERROR: ezdxf not installed. Run: pip install ezdxf")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from dxf_entity_lookup import DxfEntityIndex, DxfEntity


@dataclass
class EditResult:
    """Result of a single DXF edit operation."""
    success: bool
    action: str
    target_text: str
    new_value: Optional[str] = None
    entity_handle: Optional[str] = None
    entity_type: Optional[str] = None
    old_coords: Optional[Tuple[float, float]] = None
    new_coords: Optional[Tuple[float, float]] = None
    error: Optional[str] = None


class DXFEditor:
    """Edit DXF files directly using ezdxf."""

    def __init__(self, dxf_path: str):
        self.dxf_path = dxf_path
        self.doc = None
        self.msp = None
        self.index = None

    def load(self) -> bool:
        """Load the DXF file."""
        try:
            self.doc = ezdxf.readfile(self.dxf_path)
            self.msp = self.doc.modelspace()
            self.index = DxfEntityIndex(self.dxf_path)
            self.index.load()
            print(f"Loaded DXF: {self.dxf_path}")
            print(f"  Modelspace entities: {len(self.msp)}")
            return True
        except Exception as e:
            print(f"ERROR: Failed to load DXF: {e}")
            return False

    def _find_entity(self, target_text: str) -> Optional[DxfEntity]:
        """Find the best matching entity for target text."""
        # Exact match
        exact = self.index.search_exact(target_text)
        if exact:
            return exact[0]

        # Fuzzy match
        fuzzy = self.index.search_fuzzy(target_text, threshold=0.6)
        if fuzzy:
            return fuzzy[0][0]

        # Try common variations (NT111 → NT-111, NT 111)
        import re
        m = re.match(r'^([A-Za-z]{2,})(\d{3,})$', target_text)
        if m:
            alt = f"{m.group(1)}-{m.group(2)}"
            exact = self.index.search_exact(alt)
            if exact:
                return exact[0]

        return None

    def _get_dxf_entity(self, handle: str):
        """Get the raw ezdxf entity by handle."""
        try:
            return self.doc.entitydb.get(handle)
        except Exception:
            return None

    def replace_text(self, target_text: str, new_value: str) -> EditResult:
        """
        Replace text in a TEXT or MTEXT entity.
        For block references (INSERT), this changes the block name.
        """
        entity = self._find_entity(target_text)
        if not entity:
            return EditResult(
                success=False, action="replace_text",
                target_text=target_text, new_value=new_value,
                error=f"Entity not found for '{target_text}'"
            )

        dxf_ent = self._get_dxf_entity(entity.handle)
        if not dxf_ent:
            return EditResult(
                success=False, action="replace_text",
                target_text=target_text, new_value=new_value,
                error=f"DXF entity handle {entity.handle} not found in document"
            )

        etype = dxf_ent.dxftype()
        old_text = getattr(dxf_ent.dxf, 'text', None)
        old_coords = entity.insertion_point

        try:
            if etype == 'TEXT':
                dxf_ent.dxf.text = new_value
                return EditResult(
                    success=True, action="replace_text",
                    target_text=target_text, new_value=new_value,
                    entity_handle=entity.handle, entity_type='TEXT',
                    old_coords=old_coords
                )

            elif etype == 'MTEXT':
                dxf_ent.dxf.text = new_value
                return EditResult(
                    success=True, action="replace_text",
                    target_text=target_text, new_value=new_value,
                    entity_handle=entity.handle, entity_type='MTEXT',
                    old_coords=old_coords
                )

            elif etype == 'INSERT':
                # For block references: check if new_value is a valid block name
                # If the block exists, rename the INSERT's block name
                # Otherwise, this might be a text label inside a block
                if new_value in self.doc.blocks:
                    old_block = dxf_ent.dxf.name
                    dxf_ent.dxf.name = new_value
                    return EditResult(
                        success=True, action="replace_block",
                        target_text=target_text, new_value=new_value,
                        entity_handle=entity.handle, entity_type='INSERT',
                        old_coords=old_coords,
                        error=f"Changed block from '{old_block}' to '{new_value}'"
                    )
                else:
                    # Can't directly change text inside a block instance
                    # Could explode and recreate, but that's complex
                    return EditResult(
                        success=False, action="replace_text",
                        target_text=target_text, new_value=new_value,
                        entity_handle=entity.handle, entity_type='INSERT',
                        old_coords=old_coords,
                        error=f"INSERT entity found but cannot change text inside block. "
                              f"Block '{new_value}' does not exist in block table."
                    )

            else:
                return EditResult(
                    success=False, action="replace_text",
                    target_text=target_text, new_value=new_value,
                    entity_handle=entity.handle, entity_type=etype,
                    old_coords=old_coords,
                    error=f"Unsupported entity type: {etype}"
                )

        except Exception as e:
            return EditResult(
                success=False, action="replace_text",
                target_text=target_text, new_value=new_value,
                entity_handle=entity.handle, entity_type=etype,
                old_coords=old_coords,
                error=str(e)
            )

    def move_entity(self, target_text: str, new_x: float, new_y: float) -> EditResult:
        """Move an entity to new coordinates."""
        entity = self._find_entity(target_text)
        if not entity:
            return EditResult(
                success=False, action="move",
                target_text=target_text, new_coords=(new_x, new_y),
                error=f"Entity not found for '{target_text}'"
            )

        dxf_ent = self._get_dxf_entity(entity.handle)
        if not dxf_ent:
            return EditResult(
                success=False, action="move",
                target_text=target_text, new_coords=(new_x, new_y),
                error=f"DXF entity handle {entity.handle} not found"
            )

        old_coords = entity.insertion_point
        etype = dxf_ent.dxftype()

        try:
            if etype in ('TEXT', 'MTEXT', 'INSERT'):
                # ezdxf move method
                dxf_ent.move_to(new_x, new_y)
                return EditResult(
                    success=True, action="move",
                    target_text=target_text, new_coords=(new_x, new_y),
                    entity_handle=entity.handle, entity_type=etype,
                    old_coords=old_coords
                )
            elif etype == 'DIMENSION':
                # Dimensions have text_midpoint
                dxf_ent.dxf.text_midpoint = (new_x, new_y)
                return EditResult(
                    success=True, action="move",
                    target_text=target_text, new_coords=(new_x, new_y),
                    entity_handle=entity.handle, entity_type=etype,
                    old_coords=old_coords
                )
            else:
                return EditResult(
                    success=False, action="move",
                    target_text=target_text, new_coords=(new_x, new_y),
                    entity_handle=entity.handle, entity_type=etype,
                    old_coords=old_coords,
                    error=f"Unsupported entity type for move: {etype}"
                )

        except Exception as e:
            return EditResult(
                success=False, action="move",
                target_text=target_text, new_coords=(new_x, new_y),
                entity_handle=entity.handle, entity_type=etype,
                old_coords=old_coords,
                error=str(e)
            )

    def delete_entity(self, target_text: str) -> EditResult:
        """Delete an entity from the DXF."""
        entity = self._find_entity(target_text)
        if not entity:
            return EditResult(
                success=False, action="delete",
                target_text=target_text,
                error=f"Entity not found for '{target_text}'"
            )

        dxf_ent = self._get_dxf_entity(entity.handle)
        if not dxf_ent:
            return EditResult(
                success=False, action="delete",
                target_text=target_text,
                error=f"DXF entity handle {entity.handle} not found"
            )

        old_coords = entity.insertion_point
        etype = dxf_ent.dxftype()

        try:
            self.msp.delete_entity(dxf_ent)
            return EditResult(
                success=True, action="delete",
                target_text=target_text,
                entity_handle=entity.handle, entity_type=etype,
                old_coords=old_coords
            )
        except Exception as e:
            return EditResult(
                success=False, action="delete",
                target_text=target_text,
                entity_handle=entity.handle, entity_type=etype,
                old_coords=old_coords,
                error=str(e)
            )

    def reorder_rows(self, target_text: str, reference_text: str, after: bool = True) -> EditResult:
        """
        Move a row/entity to a new position relative to a reference row.
        This is a specialized move that computes the new Y coordinate.
        """
        target = self._find_entity(target_text)
        reference = self._find_entity(reference_text)

        if not target:
            return EditResult(
                success=False, action="reorder",
                target_text=target_text,
                error=f"Target entity '{target_text}' not found"
            )
        if not reference:
            return EditResult(
                success=False, action="reorder",
                target_text=target_text,
                error=f"Reference entity '{reference_text}' not found"
            )

        # Compute new position
        ref_y = reference.insertion_point[1]
        target_height = 20  # Estimate row height; ideally get from bbox

        if after:
            new_y = ref_y - target_height  # Below in screen coords = lower Y in CAD
        else:
            new_y = ref_y + target_height  # Above

        return self.move_entity(target_text, target.insertion_point[0], new_y)

    def save(self, output_path: Optional[str] = None) -> str:
        """Save the modified DXF. Returns the saved path."""
        if output_path is None:
            output_path = self.dxf_path

        # Backup original if overwriting
        if output_path == self.dxf_path and os.path.exists(output_path):
            backup = output_path + ".backup"
            shutil.copy2(output_path, backup)
            print(f"  Backup: {backup}")

        self.doc.saveas(output_path)
        print(f"  Saved DXF: {output_path}")
        return output_path

    def _convert_via_oda(
        self,
        input_dir: str,
        output_dir: str,
        input_format: str,
        output_format: str,
    ) -> bool:
        """
        Convert files using ODAFileConverter in an isolated virtual framebuffer.
        Uses xvfb-run --server-num to avoid any window appearing on the user's
        real display, plus timeout to prevent indefinite hangs.
        """
        oda = "/home/hongbin/.hermes/hermes-agent/squashfs-root/usr/bin/ODAFileConverter"
        if not os.path.exists(oda):
            print(f"ERROR: ODAFileConverter not found: {oda}")
            return False

        os.makedirs(output_dir, exist_ok=True)

        import random
        display_num = random.randint(120, 250)

        cmd = [
            "timeout", "45",
            "xvfb-run", "--server-num", str(display_num),
            "--server-args", "-screen 0 1280x1024x24 -ac +extension GLX +render -noreset",
            "--auto-servernum", "--error-file", "/dev/null",
            oda, input_dir, output_dir, input_format, output_format, "0", "1",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "QT_QPA_PLATFORM": "xcb"},
            )
            out_files = list(Path(output_dir).glob("*"))
            return len(out_files) > 0
        except subprocess.TimeoutExpired:
            print("  ✗ ODA conversion timed out")
            return False
        except Exception as e:
            print(f"  ✗ ODA error: {e}")
            return False

    def convert_dwg_to_dxf(self, dwg_path: str, dxf_path: str) -> bool:
        """Convert DWG to DXF using ODAFileConverter."""
        print(f"  Converting DWG → DXF via ODA...")

        input_dir = os.path.dirname(dwg_path) or "/tmp"
        output_dir = os.path.dirname(dxf_path) or "/tmp"
        basename = Path(dwg_path).stem

        # ODA outputs to a directory; move result afterward
        tmp_out = f"/tmp/oda_dwg2dxf_{os.getpid()}"
        os.makedirs(tmp_out, exist_ok=True)

        success = self._convert_via_oda(input_dir, tmp_out, "ACAD2018", "DXF")
        if not success:
            return False

        # Find the output DXF
        out_files = list(Path(tmp_out).glob("*.dxf"))
        if not out_files:
            print(f"  ✗ No DXF produced in {tmp_out}")
            return False

        # Move to desired path
        shutil.move(str(out_files[0]), dxf_path)
        shutil.rmtree(tmp_out, ignore_errors=True)
        print(f"  ✓ DXF: {dxf_path}")
        return True

    def convert_to_dwg(self, dxf_path: str, dwg_path: str) -> bool:
        """Convert DXF to DWG using ODAFileConverter."""
        print(f"  Converting DXF → DWG via ODA...")

        input_dir = os.path.dirname(dxf_path) or "/tmp"
        tmp_out = f"/tmp/oda_dxf2dwg_{os.getpid()}"
        os.makedirs(tmp_out, exist_ok=True)

        success = self._convert_via_oda(input_dir, tmp_out, "ACAD2018", "DWG")
        if not success:
            return False

        out_files = list(Path(tmp_out).glob("*.dwg"))
        if not out_files:
            print(f"  ✗ No DWG produced in {tmp_out}")
            return False

        shutil.move(str(out_files[0]), dwg_path)
        shutil.rmtree(tmp_out, ignore_errors=True)
        print(f"  ✓ DWG: {dwg_path}")
        return True

    def process_tasks(self, tasks: List[Dict[str, Any]], output_dxf: str, output_dwg: Optional[str] = None) -> List[EditResult]:
        """
        Process a list of tasks (from pipeline JSON) and save results.

        Each task dict should have:
          - action_type: 'replace' | 'change_property' | 'move' | 'delete' | 'reorder'
          - target_text: str
          - new_value: str (for replace/change)
          - new_x, new_y: float (for move)
          - reference_text: str (for reorder)
        """
        results = []

        print(f"\nProcessing {len(tasks)} DXF edit tasks...")
        print("=" * 60)

        for task in tasks:
            action = task.get("action_type", "").lower()
            target = task.get("target_text", "")
            new_val = task.get("new_value", "")

            print(f"\n[{len(results)+1}] {action}: '{target}'")

            if action in ("replace", "change_property"):
                result = self.replace_text(target, new_val)
            elif action == "move":
                nx = task.get("new_x", 0)
                ny = task.get("new_y", 0)
                result = self.move_entity(target, nx, ny)
            elif action == "delete":
                result = self.delete_entity(target)
            elif action == "reorder":
                ref = task.get("reference_text", "")
                result = self.reorder_rows(target, ref)
            else:
                result = EditResult(
                    success=False, action=action, target_text=target,
                    error=f"Unknown action type: {action}"
                )

            results.append(result)
            status = "✅" if result.success else "❌"
            print(f"  {status} {result.action} [{result.entity_type}] {result.error or 'OK'}")
            if result.new_coords:
                print(f"      → ({result.new_coords[0]:.2f}, {result.new_coords[1]:.2f})")

        # Save modified DXF
        print(f"\n{'='*60}")
        self.save(output_dxf)

        # Convert to DWG if requested
        if output_dwg:
            print()
            self.convert_to_dwg(output_dxf, output_dwg)

        return results

    def close(self):
        if self.doc:
            self.doc = None


def main():
    parser = argparse.ArgumentParser(description="DXF Editor")
    parser.add_argument("--dxf", required=True, help="Input DXF file")
    parser.add_argument("--action", choices=["replace", "move", "delete", "reorder"],
                        help="Edit action")
    parser.add_argument("--target", help="Target entity text")
    parser.add_argument("--new-value", help="New text value (for replace)")
    parser.add_argument("--to-x", type=float, help="New X coordinate (for move)")
    parser.add_argument("--to-y", type=float, help="New Y coordinate (for move)")
    parser.add_argument("--reference", help="Reference entity text (for reorder)")
    parser.add_argument("--tasks", help="JSON tasks file (batch mode)")
    parser.add_argument("--output-dxf", "-o", help="Output DXF path (default: overwrite input)")
    parser.add_argument("--output-dwg", help="Also convert to DWG")
    parser.add_argument("--report", "-r", help="Save JSON report")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't save")

    args = parser.parse_args()

    editor = DXFEditor(args.dxf)

    try:
        if not editor.load():
            sys.exit(1)

        results = []

        if args.tasks:
            with open(args.tasks) as f:
                tasks = json.load(f)
            # Support both flat list and nested format
            if isinstance(tasks, dict):
                tasks = tasks.get("tasks", tasks.get("results", []))
            results = editor.process_tasks(
                tasks,
                output_dxf=args.output_dxf or args.dxf,
                output_dwg=args.output_dwg,
            )

        elif args.action == "replace" and args.target and args.new_value:
            results.append(editor.replace_text(args.target, args.new_value))
            if not args.dry_run:
                editor.save(args.output_dxf or args.dxf)
                if args.output_dwg:
                    editor.convert_to_dwg(args.output_dxf or args.dxf, args.output_dwg)

        elif args.action == "move" and args.target and args.to_x is not None and args.to_y is not None:
            results.append(editor.move_entity(args.target, args.to_x, args.to_y))
            if not args.dry_run:
                editor.save(args.output_dxf or args.dxf)

        elif args.action == "delete" and args.target:
            results.append(editor.delete_entity(args.target))
            if not args.dry_run:
                editor.save(args.output_dxf or args.dxf)

        elif args.action == "reorder" and args.target and args.reference:
            results.append(editor.reorder_rows(args.target, args.reference))
            if not args.dry_run:
                editor.save(args.output_dxf or args.dxf)

        else:
            print("ERROR: Insufficient arguments. Use --tasks or --action + required params.")
            sys.exit(1)

        # Print summary
        success_count = sum(1 for r in results if r.success)
        print(f"\n{'='*60}")
        print(f"Summary: {success_count}/{len(results)} operations succeeded")

        if args.report:
            report = {
                "input_dxf": args.dxf,
                "output_dxf": args.output_dxf,
                "output_dwg": args.output_dwg,
                "total": len(results),
                "successful": success_count,
                "failed": len(results) - success_count,
                "results": [
                    {
                        "success": r.success,
                        "action": r.action,
                        "target": r.target_text,
                        "new_value": r.new_value,
                        "entity_handle": r.entity_handle,
                        "entity_type": r.entity_type,
                        "old_coords": r.old_coords,
                        "new_coords": r.new_coords,
                        "error": r.error,
                    }
                    for r in results
                ],
            }
            with open(args.report, 'w') as f:
                json.dump(report, f, indent=2)
            print(f"Report saved: {args.report}")

    finally:
        editor.close()


if __name__ == "__main__":
    main()
