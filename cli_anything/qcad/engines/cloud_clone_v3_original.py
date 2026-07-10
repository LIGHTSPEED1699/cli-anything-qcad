"""Clone DXF entities inside cloud polygons to target terminal rows.

This engine combines cloud-based entity selection (like DeleteCloudedEntitiesEngine)
with terminal-row clone logic (like CloneTerminalWiresEngine) into a single routine:

1. Extract cloud polygon from PDF annotation (already mapped to DXF by planner).
2. Select all entities inside the cloud polygon (wires, labels, arcs, etc.).
3. Parse source/target terminal row numbers from annotation text.
4. Compute dy offset from terminal row label positions in DXF.
5. Clone matching entities with offset + text renames.
6. Exclude terminal INSERT blocks (they already exist at target rows).
7. Exclude terminal row labels like (4), (5) to avoid duplicate numbering.

This is the first cloud-based duplication engine — the old VLM-CAD-automation repo
attempted Variant C (cloud polygon selection) but failed due to coordinate mismatch
(wrong 3.pdf).  With the correct PDF, this engine makes cloud-based clone work.
"""
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import ezdxf
except ImportError as e:  # pragma: no cover
    raise ImportError("ezdxf is required") from e

try:
    from matplotlib.path import Path as MplPath
except ImportError as e:  # pragma: no cover
    raise ImportError("matplotlib is required") from e

# Reuse entity-selection logic from delete_clouded_entities
from cli_anything.qcad.engines.delete_clouded_entities import (
    _entity_inside_polygon,
    _entity_geometry_points,
    _point_in_polygon,
    _segment_intersects_polygon,
)


# --- Row parsing (shared with clone_terminal_wires) ---

def _parse_row_list(s: str) -> List[int]:
    """Extract small integers (likely terminal/wire row numbers) from a clause."""
    rows = []
    for m in re.finditer(r"\b(\d{1,3})\b", s):
        n = int(m.group(1))
        if n not in rows:
            rows.append(n)
    # Detect explicit ranges like "4-6" or "4/6"
    for m in re.finditer(r"(\d{1,3})\s*[/-]\s*(\d{1,3})", s):
        a, b = int(m.group(1)), int(m.group(2))
        for n in range(a, b + 1):
            if n not in rows:
                rows.append(n)
    rows.sort()
    return rows


def _extract_clone_clause(desc: str) -> Tuple[str, str]:
    """Return (source_clause, target_clause) from a clone description."""
    desc = re.split(r"\s+(?:and|then|update|change)\s+", desc, flags=re.I)[0]
    matches = list(re.finditer(r"\bto\b", desc, re.I))
    if not matches:
        return (desc, "")
    for m in reversed(matches):
        pos = m.start()
        left = desc[:pos]
        right = desc[pos + 2:]
        if re.search(r"\d", left) and re.search(r"\d", right):
            return (left, right)
    pos = matches[0].start()
    return (desc[:pos], desc[pos + 2:])


def _infer_rows_from_description(desc: str) -> Tuple[List[int], List[int]]:
    src_clause, tgt_clause = _extract_clone_clause(desc)
    return _parse_row_list(src_clause), _parse_row_list(tgt_clause)


def _row_y_center(doc, row_num: int, tol: float = 0.5) -> Optional[float]:
    """Find y-center of a terminal row by its label text like '(4)' or 'T4'.

    Searches for TEXT entities matching the row label pattern and returns the
    mean y-coordinate.  Falls back to searching for bare number text.
    """
    msp = doc.modelspace()
    candidates = []
    patterns = [f"({row_num})", f"( {row_num} )", f"T{row_num}", f"T{row_num:02d}"]
    for ent in msp:
        if ent.dxftype() != "TEXT":
            continue
        txt = (ent.dxf.text or "").strip()
        if txt in patterns:
            candidates.append(ent.dxf.insert.y)
    if not candidates:
        # Fallback: look for standalone number text in typical terminal range
        for ent in msp:
            if ent.dxftype() != "TEXT":
                continue
            txt = (ent.dxf.text or "").strip()
            if txt == str(row_num):
                candidates.append(ent.dxf.insert.y)
    if not candidates:
        return None
    return sum(candidates) / len(candidates)


def _entity_text(ent) -> str:
    etype = ent.dxftype()
    if etype == "TEXT":
        return ent.dxf.text or ""
    if etype == "MTEXT":
        return ent.text or ""
    if etype == "ATTRIB":
        return ent.dxf.text or ""
    return ""


def _is_terminal_label(ent) -> bool:
    """Return True if entity is a terminal row label like (4), T4, etc."""
    if ent.dxftype() not in ("TEXT", "MTEXT"):
        return False
    txt = _entity_text(ent).strip()
    if re.match(r"^\(\s*\d+\s*\)$", txt):
        return True
    if re.match(r"^T\d+$", txt):
        return True
    return False


def _parse_text_replacements(desc: str, params: Dict[str, Any]) -> Dict[str, str]:
    """Extract text rename mappings from parameters or annotation description.

    Only matches meaningful identifiers (PLC, CA, cable numbers, etc.) —
    not arbitrary words that happen to precede 'to'.
    """
    text_replacements = params.get("text_replacements", {})
    if text_replacements:
        return text_replacements

    nv = params.get("new_value", "") or desc

    # Only match specific identifier patterns, not arbitrary words
    # PLC21 → PLC22
    for m in re.finditer(r"\b(PLC\d+)\s*(?:to|→|->)\s*(PLC\d+)", nv, re.I):
        old, new = m.group(1), m.group(2)
        if old != new:
            text_replacements[old] = new
    # CA-1451 → CA-1452
    for m in re.finditer(r"\b(CA-?\w+)\s*(?:to|→|->)\s*(CA-?\w+)", nv, re.I):
        old, new = m.group(1), m.group(2)
        if old != new:
            text_replacements[old] = new
    # 5-digit cable numbers: 02732 → 02733
    for m in re.finditer(r"\b(\d{5})\s*(?:to|→|->)\s*(\d{5})\b", nv):
        old, new = m.group(1), m.group(2)
        if old != new:
            text_replacements[old] = new
    # DWG B-SAR-280-XXXXX pattern
    if "DWG B-SAR-280-" in nv:
        nums = re.findall(r"B-SAR-280-(\d+)", nv)
        if len(nums) == 2:
            text_replacements[f"027{nums[0]}"] = f"027{nums[1]}"

    # Also derive from source/target row numbers: labels like (4)→(7)
    src_rows = params.get("source_rows", [])
    tgt_rows = params.get("target_rows", [])
    for s, t in zip(src_rows, tgt_rows):
        if s != t:
            text_replacements[f"({s})"] = f"({t})"

    # Extract "as XXX" target values from annotation text.
    # Pattern: "change related texts as PLC22, CA-1452, DWG B-SAR-280-02733"
    # These are the TARGET values. The SOURCE values must be discovered from
    # the DXF entities inside the cloud (e.g., PLC21, CA-1451, 02732).
    # We pair them by extracting source values from DXF at runtime in the engine.
    return text_replacements


def _extract_target_text_values(desc: str) -> List[str]:
    """Extract target text values from 'change related texts as X, Y, Z' clause.

    Returns a list of target strings like ['PLC22', 'CA-1452', '02733'].
    The source values (PLC21, CA-1451, 02732) are discovered from the DXF
    entities inside the cloud at engine runtime.
    """
    # Look specifically for "as" followed by comma-separated identifier values.
    # The annotation pattern is: "copy ... to ... and change related texts as PLC22, CA-1452, DWG B-SAR-280-02733"
    m = re.search(r"\bchange\s+related\s+texts?\s+as\s+([A-Z0-9\-,\s\.]+?)(?:$|\.|\s+and\s+)",
                  desc, re.I)
    if not m:
        # Fallback: look for "texts as" or "texts to" followed by values
        m = re.search(r"\btexts?\s+(?:as|to)\s+([A-Z][A-Z0-9\-,\s\.]+?)(?:$|\.|\s+and\s+)",
                      desc, re.I)
    if not m:
        return []
    clause = m.group(1).strip()
    # Split by comma, clean up
    values = [v.strip() for v in clause.split(",")]
    # Extract meaningful identifiers
    result = []
    for v in values:
        v = v.strip()
        if not v:
            continue
        # Extract trailing 5-digit number from "DWG B-SAR-280-02733" → "02733"
        m2 = re.search(r"(\d{5})$", v)
        if m2:
            result.append(m2.group(1))
        elif re.match(r"^[A-Z]+-?\w*$", v, re.I):
            result.append(v)
    return result


def _discover_source_texts(doc, polygon: List[Tuple[float, float]],
                          source_rows: List[int] = None) -> Dict[str, str]:
    """Scan entities inside the cloud polygon and extract identifiable text values.

    Also searches the y-band of source terminal rows if the cloud polygon
    doesn't contain PLC/CA/cable texts (the cloud may only cover the wire
    connection area, not the cable reference labels further away).

    Returns a dict mapping text type → source value, e.g.:
        {"PLC": "PLC21", "CA": "CA-1451", "CABLE": "02732"}
    """
    from cli_anything.qcad.engines.delete_clouded_entities import _entity_inside_polygon
    msp = doc.modelspace()
    result = {}

    # First pass: search inside the cloud polygon
    for ent in msp:
        if ent.dxftype() not in ("TEXT", "MTEXT", "ATTRIB"):
            continue
        if not _entity_inside_polygon(ent, polygon):
            continue
        _try_match_text(_entity_text(ent).strip(), result)

    # If we found PLC, CA, and CABLE, we're done
    if len(result) >= 3:
        return result

    # Second pass: search in the y-band of source rows
    # The cloud may only cover the wire area, not the cable reference labels
    if source_rows:
        # Compute y-band from source row labels
        y_min, y_max = float('inf'), float('-inf')
        for row_num in source_rows:
            y = _row_y_center(doc, row_num)
            if y is not None:
                y_min = min(y_min, y - 1.5)  # expand to catch nearby cable refs
                y_max = max(y_max, y + 1.5)
        if y_min < y_max:
            for ent in msp:
                if ent.dxftype() not in ("TEXT", "MTEXT", "ATTRIB"):
                    continue
                y = ent.dxf.insert.y
                if y < y_min or y > y_max:
                    continue
                _try_match_text(_entity_text(ent).strip(), result)

    return result


def _try_match_text(txt: str, result: Dict[str, str]) -> None:
    """Try to match a text string against known patterns and add to result."""
    if not txt:
        return
    # PLC pattern
    m = re.match(r"^(PLC\d+)", txt, re.I)
    if m and "PLC" not in result:
        result["PLC"] = m.group(1)
        return
    # CA pattern
    m = re.match(r"^(CA-?\w+)", txt, re.I)
    if m and "CA" not in result:
        result["CA"] = m.group(1)
        return
    # 5-digit cable number
    m = re.search(r"(\d{5})", txt)
    if m and "CABLE" not in result:
        result["CABLE"] = m.group(1)
        return


def _copy_entity(msp, ent, dy: float, text_replacements: Dict[str, str],
                 skip_insert: bool = True) -> Optional[Any]:
    """Deep-copy an entity, translate by dy, apply text replacements.

    Args:
        skip_insert: if True, skip INSERT (block reference) entities to avoid
                      duplicating terminal blocks that already exist at target rows.
    """
    etype = ent.dxftype()
    doc = msp.doc

    if skip_insert and etype == "INSERT":
        return None

    # Skip terminal row labels to avoid duplicate numbering at destination
    if _is_terminal_label(ent):
        return None

    try:
        new = doc.entitydb.duplicate_entity(ent)
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


class CloudCloneEngine:
    """Clone all entities inside cloud polygons to target terminal rows.

    Uses the same polygon-based entity selection as DeleteCloudedEntitiesEngine,
    but instead of deleting, clones the selected entities with a y-offset
    derived from source/target terminal row positions.

    Parameters (in task.parameters or task.dxf_region):
        - regions: list of {type: "polygon", verts: [(x,y),...], bbox: (xmin,xmax,ymin,ymax)}
          (same format as DeleteCloudedEntitiesEngine)
        - source_rows: [4, 5, 6]  (terminal row numbers inside the cloud)
        - target_rows: [7, 8, 9]  (destination terminal rows)
        - text_replacements: {"PLC21": "PLC22", ...}
        - target_description: "copy wires connected to 4, 5, 6 to 7, 8, 9"
          (used to infer source/target rows if not provided)

    If source_rows/target_rows are not provided, they are inferred from
    target_description or the annotation text.
    """

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        regions = parameters.get("regions", [])
        if isinstance(regions, dict):
            regions = [regions]

        if not regions:
            return {"engine": "cloud_clone", "success": False,
                    "error": "no cloud regions provided"}

        # Parse source/target rows
        source_rows = parameters.get("source_rows", [])
        target_rows = parameters.get("target_rows", [])
        if not source_rows or not target_rows:
            desc = parameters.get("target_description", "") or parameters.get("text", "")
            source_rows, target_rows = _infer_rows_from_description(desc)

        if not source_rows or not target_rows:
            return {"engine": "cloud_clone", "success": False,
                    "error": f"cannot infer source/target rows from: {desc or '(empty)'}"}

        if len(source_rows) != len(target_rows):
            return {"engine": "cloud_clone", "success": False,
                    "error": f"source_rows {source_rows} and target_rows {target_rows} must have same length"}

        # Parse text replacements
        text_replacements = _parse_text_replacements(
            parameters.get("text", ""), {**parameters,
                                         "source_rows": source_rows,
                                         "target_rows": target_rows})

        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        # Compute dy offsets from terminal row positions
        dy_per_pair = []
        for src_num, tgt_num in zip(source_rows, target_rows):
            src_y = _row_y_center(doc, src_num)
            tgt_y = _row_y_center(doc, tgt_num)
            if src_y is None or tgt_y is None:
                return {"engine": "cloud_clone", "success": False,
                        "error": f"cannot find terminal row labels ({src_num}) or ({tgt_num}) in DXF"}
            dy_per_pair.append(tgt_y - src_y)

        # Use the first cloud region as the source selection polygon.
        # All regions should cover the same source area (rows 4,5,6).
        # If multiple regions exist, merge them into one selection polygon.
        source_polygon: List[Tuple[float, float]] = []
        for region in regions:
            verts = region.get("verts", [])
            if len(verts) >= 3:
                source_polygon.extend(verts)

        if not source_polygon:
            # Fallback: use bbox-based rectangle
            for region in regions:
                bbox = region.get("bbox") or region.get("coords")
                if bbox and len(bbox) == 4:
                    xmin, xmax, ymin, ymax = bbox
                    source_polygon = [(xmin, ymin), (xmax, ymin),
                                      (xmax, ymax), (xmin, ymax)]
                    break

        if len(source_polygon) < 3:
            return {"engine": "cloud_clone", "success": False,
                    "error": "cloud polygon has insufficient vertices"}

        # Discover source text values from DXF entities inside the cloud
        # and pair them with target values from the annotation text.
        source_texts = _discover_source_texts(doc, source_polygon, source_rows)
        target_values = _extract_target_text_values(
            parameters.get("text", "") or parameters.get("target_description", ""))

        # Build text replacements from source→target pairing
        # Map by type: PLC→PLC, CA→CA, CABLE→CABLE
        type_order = ["PLC", "CA", "CABLE"]
        for i, tv in enumerate(target_values):
            if i < len(type_order) and type_order[i] in source_texts:
                src_val = source_texts[type_order[i]]
                if src_val != tv:
                    text_replacements[src_val] = tv

        # Select entities inside the cloud polygon
        selected_entities = []
        for ent in list(msp):
            if _is_terminal_label(ent):
                continue  # Skip row labels like (4), (5)
            if ent.dxftype() == "INSERT":
                # Skip terminal block INSERTs — they already exist at targets
                continue
            if _entity_inside_polygon(ent, source_polygon):
                selected_entities.append(ent)

        if not selected_entities:
            return {"engine": "cloud_clone", "success": False,
                    "error": "no entities found inside cloud polygon",
                    "polygon_vertex_count": len(source_polygon)}

        # Clone each selected entity to each target row
        cloned = 0
        clone_details = []
        for ent in selected_entities:
            etype = ent.dxftype()
            for dy in dy_per_pair:
                new_ent = _copy_entity(msp, ent, dy, text_replacements, skip_insert=True)
                if new_ent is not None:
                    cloned += 1
                    clone_details.append({
                        "type": etype,
                        "dy": round(dy, 4),
                        "text": _entity_text(ent)[:50] if _entity_text(ent) else "",
                    })

        doc.saveas(out_dxf)
        return {
            "engine": "cloud_clone",
            "source_rows": source_rows,
            "target_rows": target_rows,
            "dy_per_pair": [round(dy, 4) for dy in dy_per_pair],
            "text_replacements": text_replacements,
            "source_entities_selected": len(selected_entities),
            "cloned": cloned,
            "clone_details": clone_details[:20],  # cap for report
            "polygon_vertex_count": len(source_polygon),
            "output_dxf": out_dxf,
        }