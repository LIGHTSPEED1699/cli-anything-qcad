"""Clone terminal wiring rows without duplicating terminal INSERT blocks."""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re
import math

try:
    import ezdxf
except ImportError as e:  # pragma: no cover
    raise ImportError("ezdxf is required") from e


_WIRE_ENTITY_TYPES = {"LINE", "LWPOLYLINE", "ARC", "CIRCLE", "SPLINE", "ELLIPSE", "LEADER"}
_LABEL_TYPES = {"TEXT", "MTEXT", "DIMENSION"}


def _row_y_band(doc, row_text: str, tol: float = 0.15) -> Optional[Tuple[float, float, float]]:
    """Find y-center of a row by terminal number text like '(4)' or 'T4'."""
    msp = doc.modelspace()
    candidates = []
    for ent in msp:
        if ent.dxftype() != "TEXT":
            continue
        txt = (ent.dxf.text or "").strip()
        if txt == row_text:
            candidates.append(ent.dxf.insert.y)
    if not candidates:
        return None
    yc = sum(candidates) / len(candidates)
    return (yc - tol, yc + tol, yc)


def _sample_points(ent) -> List[Tuple[float, float]]:
    etype = ent.dxftype()
    if etype == "LINE":
        return [(ent.dxf.start.x, ent.dxf.start.y), (ent.dxf.end.x, ent.dxf.end.y)]
    if etype == "LWPOLYLINE":
        return [(p[0], p[1]) for p in ent.get_points("xy")]
    if etype == "ARC":
        cx, cy = ent.dxf.center.x, ent.dxf.center.y
        r = ent.dxf.radius
        sa = math.radians(ent.dxf.start_angle)
        ea = math.radians(ent.dxf.end_angle)
        return [(cx + r * math.cos(a), cy + r * math.sin(a))
                for a in [sa + i * (ea - sa) / 16 for i in range(17)]]
    if etype == "CIRCLE":
        cx, cy = ent.dxf.center.x, ent.dxf.center.y
        r = ent.dxf.radius
        return [(cx + r * math.cos(a), cy + r * math.sin(a))
                for a in [i * math.pi / 8 for i in range(16)]]
    if etype in ("TEXT", "MTEXT"):
        return [(ent.dxf.insert.x, ent.dxf.insert.y)]
    if etype == "INSERT":
        return [(ent.dxf.insert.x, ent.dxf.insert.y)]
    if etype == "DIMENSION":
        try:
            return [(ent.dxf.text_midpoint.x, ent.dxf.text_midpoint.y)]
        except Exception:
            return []
    return []


def _entities_in_band(doc, y0: float, y1: float) -> List[Any]:
    msp = doc.modelspace()
    result = []
    for ent in msp:
        pts = _sample_points(ent)
        if any(y0 <= y <= y1 for _, y in pts):
            result.append(ent)
    return result


def _entity_text(ent) -> str:
    etype = ent.dxftype()
    if etype == "TEXT":
        return ent.dxf.text or ""
    if etype == "MTEXT":
        return ent.text or ""
    if etype == "ATTRIB":
        return ent.dxf.text or ""
    return ""


def _parse_row_list(s: str) -> List[int]:
    """Extract small integers (likely terminal/wire row numbers) from a clause.
    Avoid expanding large cable/drawing numbers like 02733 into ranges."""
    # Only consider numbers up to 3 digits as row numbers
    rows = []
    for m in re.finditer(r"\b(\d{1,3})\b", s):
        n = int(m.group(1))
        if n not in rows:
            rows.append(n)
    # Detect explicit ranges like "4-6" or "4/6" among small numbers
    for m in re.finditer(r"(\d{1,3})\s*[/-]\s*(\d{1,3})", s):
        a, b = int(m.group(1)), int(m.group(2))
        for n in range(a, b + 1):
            if n not in rows:
                rows.append(n)
    rows.sort()
    return rows


def _extract_clone_clause(desc: str) -> Tuple[str, str]:
    """Return (source_clause, target_clause) from a clone description.
    Splits at the 'to' that separates source and target row lists, stopping
    before update/change clauses."""
    # Strip trailing update clauses
    desc = re.split(r"\s+(?:and|then|update|change)\s+", desc, flags=re.I)[0]
    # Find last 'to' that is surrounded by digits/commas/spaces
    matches = list(re.finditer(r"\bto\b", desc, re.I))
    if not matches:
        return (desc, "")
    # Use the 'to' that sits between two digit groups
    for m in reversed(matches):
        pos = m.start()
        left = desc[:pos]
        right = desc[pos + 2:]
        if re.search(r"\d", left) and re.search(r"\d", right):
            return (left, right)
    # Fallback: first 'to'
    pos = matches[0].start()
    return (desc[:pos], desc[pos + 2:])


def _infer_rows_from_description(desc: str) -> Tuple[List[int], List[int]]:
    src_clause, tgt_clause = _extract_clone_clause(desc)
    return _parse_row_list(src_clause), _parse_row_list(tgt_clause)


def _copy_entity(msp, ent, dy: float, text_replacements: Dict[str, str]) -> Optional[Any]:
    """Deep-copy an entity via ezdxf, translate by dy, apply text replacements."""
    etype = ent.dxftype()
    doc = msp.doc
    if etype == "INSERT":
        return None  # Do not clone terminal blocks

    # Use ezdxf's copy_entity helper or manual deep-copy through factory
    try:
        new = doc.entitydb.duplicate_entity(ent)  # type: ignore
    except Exception:
        try:
            new = ent.copy()
        except Exception:
            return None

    if new is None:
        return None

    # Translate geometry
    try:
        new.translate(0, dy, 0)
    except Exception:
        pass

    # Apply text replacements
    txt = _entity_text(new)
    if txt:
        for old, new_val in text_replacements.items():
            if old in txt:
                new_txt = txt.replace(old, new_val)
                if new.dxftype() == "TEXT":
                    new.dxf.text = new_txt
                elif new.dxftype() == "MTEXT":
                    new.text = new_txt
                break

    # Ensure new handle and add to modelspace
    try:
        new.dxf.handle = None
        doc.entitydb.add(new)
        msp.add_entity(new)
    except Exception:
        msp.add_entity(new)
    return new


class CloneTerminalWiresEngine:
    """Clone wire geometry + labels from source rows to target rows."""

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        source_rows = parameters.get("source_rows", [])
        target_rows = parameters.get("target_rows", [])
        text_replacements = parameters.get("text_replacements", {})
        if not source_rows or not target_rows:
            desc = parameters.get("target_description", "")
            source_rows, target_rows = _infer_rows_from_description(desc)

        if not source_rows or not target_rows:
            return {"engine": "clone_terminal_wires", "success": False,
                    "error": "source_rows and target_rows required"}

        if len(source_rows) != len(target_rows):
            return {"engine": "clone_terminal_wires", "success": False,
                    "error": f"source_rows {source_rows} and target_rows {target_rows} must have same length"}

        # If text_replacements not provided, try to parse from new_value string
        text_replacements = parameters.get("text_replacements", {})
        if not text_replacements:
            nv = parameters.get("new_value", "")
            # Look for "X to Y" patterns in new_value
            for m in re.finditer(r"([A-Z0-9\-]+)\s*(?:to|→|-\u003e)\s*([A-Z0-9\-]+)", nv, re.I):
                old, new = m.group(1), m.group(2)
                if old != new:
                    text_replacements[old] = new
            # Also allow comma-separated pairs
            if not text_replacements and ',' in nv:
                parts = [p.strip() for p in nv.split(',')]
                for p in parts:
                    mm = re.match(r"([A-Z0-9\-]+)\s*(?:to|→|-\u003e)\s*([A-Z0-9\-]+)", p, re.I)
                    if mm:
                        text_replacements[mm.group(1)] = mm.group(2)
            # If still empty and nv contains things like PLC21, CA-1451, 02732, infer from context
            if not text_replacements:
                # Find PLC/CA/number patterns and increment them
                plc = re.findall(r"PLC(\d+)", nv, re.I)
                if len(plc) == 2:
                    text_replacements[f"PLC{plc[0]}"] = f"PLC{plc[1]}"
                ca = re.findall(r"CA-([A-Z0-9]+)", nv, re.I)
                if len(ca) == 2:
                    text_replacements[f"CA-{ca[0]}"] = f"CA-{ca[1]}"
                num = re.findall(r"(\d{5})", nv)
                if len(num) == 2:
                    text_replacements[num[0]] = num[1]
                # Also map the full DWG cable string if present
                if "DWG B-SAR-280-" in nv:
                    old_full = re.search(r"B-SAR-280-(\d+)", nv)
                    new_full = re.findall(r"B-SAR-280-(\d+)", nv)
                    if old_full and len(new_full) == 2:
                        text_replacements[f"027{old_full.group(1)}"] = f"027{new_full[1]}"

        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        cloned = 0

        for src_num, tgt_num in zip(source_rows, target_rows):
            src_band = _row_y_band(doc, f"({src_num})")
            tgt_band = _row_y_band(doc, f"({tgt_num})")
            if src_band is None or tgt_band is None:
                continue
            dy = tgt_band[2] - src_band[2]
            entities = _entities_in_band(doc, src_band[0], src_band[1])
            for ent in entities:
                if ent.dxftype() == "INSERT":
                    continue
                if _copy_entity(msp, ent, dy, text_replacements):
                    cloned += 1

        doc.saveas(out_dxf)
        return {
            "engine": "clone_terminal_wires",
            "source_rows": source_rows,
            "target_rows": target_rows,
            "dy": [tgt - src for src, tgt in zip(
                [_row_y_band(doc, f'({n})')[2] if _row_y_band(doc, f'({n})') else 0 for n in source_rows],
                [_row_y_band(doc, f'({n})')[2] if _row_y_band(doc, f'({n})') else 0 for n in target_rows])],
            "cloned": cloned,
            "output_dxf": out_dxf,
        }
