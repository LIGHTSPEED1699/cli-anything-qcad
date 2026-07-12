"""Terminal position discovery for DXF electrical drawings.

Terminal numbers in electrical CAD drawings are stored as ATTRIB values
(e.g. TERMNUM) inside terminal block INSERT entities — NOT as standalone TEXT
entities like '(4)'.  The standalone '(N)' TEXT entities are wire/connection
markers with non-uniform spacing (0.25/0.50 alternating), while the actual
terminal boxes are at uniform 0.250 spacing.

This module provides the correct terminal position lookup based on
terminal block INSERT ATTRIB values, with fallback to TEXT labels for drawings
that use that convention instead.

Block names and ATTRIB tags are auto-discovered via DrawingProfile when a
DXF path is provided. The hardcoded defaults below are used as fallback.
"""
import re
from typing import Dict, Optional

try:
    import ezdxf
except ImportError as e:  # pragma: no cover
    raise ImportError("ezdxf is required") from e

# Fallback block names / attrib tags (used when DrawingProfile isn't available
# or doesn't find terminal blocks)
_DEFAULT_TERMINAL_BLOCKS = frozenset(["Wlltermn", "Wetermn1"])
_DEFAULT_ATTRIB_TAG = "TERMNUM"


def discover_terminal_positions(doc, dxf_path: str = None) -> Dict[int, Dict]:
    """Find all terminal positions from terminal block INSERT ATTRIBs.

    Returns {terminal_number: {"y": float, "x": float, "handle": str}}

    Block names and ATTRIB tags are auto-discovered from DrawingProfile when
    dxf_path is provided. Falls back to hardcoded defaults (Wlltermn/TERMNUM)
    and then to standalone TEXT '(N)' labels.
    """
    msp = doc.modelspace()
    terminals: Dict[int, Dict] = {}

    # Discover terminal block names and ATTRIB tag from profile
    terminal_blocks = _DEFAULT_TERMINAL_BLOCKS
    attrib_tag = _DEFAULT_ATTRIB_TAG
    if dxf_path:
        try:
            from cli_anything.qcad.utils.drawing_profile import DrawingProfile
            profile = DrawingProfile.from_dxf(dxf_path)
            if profile.terminal_blocks:
                terminal_blocks = set(profile.terminal_blocks.keys())
                # Use the first terminal block's attrib_tag
                for info in profile.terminal_blocks.values():
                    attrib_tag = info.attrib_tag
                    break
        except Exception:
            pass

    # Primary: terminal block INSERT ATTRIBs
    for ent in msp:
        if ent.dxftype() != "INSERT":
            continue
        if ent.dxf.name not in terminal_blocks:
            continue
        try:
            x = ent.dxf.insert.x
            y = ent.dxf.insert.y
            handle = ent.dxf.handle
        except Exception:
            continue
        for attrib in getattr(ent, "attribs", []):
            if attrib.dxf.tag == attrib_tag:
                val = (attrib.dxf.text or "").strip()
                try:
                    tnum = int(val)
                except ValueError:
                    continue
                if tnum not in terminals:
                    terminals[tnum] = {"y": y, "x": x, "handle": handle}

    # Fallback: standalone TEXT '(N)' entities (if no ATTRIB-based terminals found)
    if not terminals:
        for ent in msp:
            if ent.dxftype() != "TEXT":
                continue
            txt = (ent.dxf.text or "").strip()
            m = re.match(r"^\((\d+)\)$", txt)
            if m:
                tnum = int(m.group(1))
                x, y = ent.dxf.insert.x, ent.dxf.insert.y
                if tnum not in terminals:
                    terminals[tnum] = {"y": y, "x": x, "handle": ent.dxf.handle}

    return terminals


def row_y_center(doc, row_num: int, dxf_path: str = None) -> Optional[float]:
    """Return the Y-coordinate of a terminal row by its number.

    Uses terminal block ATTRIB lookup (correct method) with TEXT fallback.
    """
    terminals = discover_terminal_positions(doc, dxf_path=dxf_path)
    t = terminals.get(row_num)
    if t:
        return t["y"]
    return None


def terminal_block_x(doc, row_nums: list, dxf_path: str = None) -> Optional[float]:
    """Find the x-position of the terminal block from terminal INSERT positions.

    Returns the x of the terminal block inserts, or falls back
    to the rightmost x of standalone TEXT labels.
    """
    terminals = discover_terminal_positions(doc, dxf_path=dxf_path)
    x_positions = []
    for r in row_nums:
        t = terminals.get(r)
        if t:
            x_positions.append(t["x"])
    if x_positions:
        return max(x_positions)

    # Fallback: TEXT labels
    msp = doc.modelspace()
    for r in row_nums:
        pattern = f"({r})"
        for ent in msp:
            if ent.dxftype() != "TEXT":
                continue
            txt = (ent.dxf.text or "").strip()
            if txt == pattern:
                try:
                    x_positions.append(ent.dxf.insert.x)
                except Exception:
                    pass
    if x_positions:
        return max(x_positions)
    return None