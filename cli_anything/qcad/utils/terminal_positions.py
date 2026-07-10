"""Terminal position discovery for DXF electrical drawings.

Terminal numbers in electrical CAD drawings are stored as ATTRIB values
(TERMNUM) inside Wlltermn block INSERT entities — NOT as standalone TEXT
entities like '(4)'.  The standalone '(N)' TEXT entities are wire/connection
markers with non-uniform spacing (0.25/0.50 alternating), while the actual
terminal boxes are at uniform 0.250 spacing.

This module provides the correct terminal position lookup based on
Wlltermn INSERT ATTRIB values, with fallback to TEXT labels for drawings
that use that convention instead.
"""
import re
from typing import Dict, Optional

try:
    import ezdxf
except ImportError as e:  # pragma: no cover
    raise ImportError("ezdxf is required") from e

# Block names that carry terminal number ATTRIBs
TERMINAL_BLOCK_NAMES = frozenset(["Wlltermn", "Wetermn1"])


def discover_terminal_positions(doc) -> Dict[int, Dict]:
    """Find all terminal positions from Wlltermn INSERT ATTRIB (TERMNUM).

    Returns {terminal_number: {"y": float, "x": float, "handle": str}}

    Primary source: ATTRIB tag='TERMNUM' inside Wlltermn INSERT entities.
    Fallback: standalone TEXT entities matching '(N)' pattern (for drawings
    that use TEXT labels instead of block attributes).
    """
    msp = doc.modelspace()
    terminals: Dict[int, Dict] = {}

    # Primary: Wlltermn INSERT ATTRIBs
    for ent in msp:
        if ent.dxftype() != "INSERT":
            continue
        if ent.dxf.name not in TERMINAL_BLOCK_NAMES:
            continue
        try:
            x = ent.dxf.insert.x
            y = ent.dxf.insert.y
            handle = ent.dxf.handle
        except Exception:
            continue
        for attrib in getattr(ent, "attribs", []):
            if attrib.dxf.tag == "TERMNUM":
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


def row_y_center(doc, row_num: int) -> Optional[float]:
    """Return the Y-coordinate of a terminal row by its number.

    Uses Wlltermn ATTRIB lookup (correct method) with TEXT fallback.
    """
    terminals = discover_terminal_positions(doc)
    t = terminals.get(row_num)
    if t:
        return t["y"]
    return None


def terminal_block_x(doc, row_nums: list) -> Optional[float]:
    """Find the x-position of the terminal block from terminal INSERT positions.

    Returns the x of the terminal block inserts (Wlltermn), or falls back
    to the rightmost x of standalone TEXT labels.
    """
    terminals = discover_terminal_positions(doc)
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