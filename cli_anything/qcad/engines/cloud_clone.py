"""Clone DXF entities inside cloud polygons to target terminal rows.

Selects entities inside cloud polygon + bounded y-band supplement, assigns them
to specific source rows by nearest text proximity, clones each to its matched
target row once. EPAC texts, LEFT-side-only entities, and target-row entities
are excluded.
"""

import math, re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import ezdxf
except ImportError as e:
    raise ImportError("ezdxf is required") from e
try:
    from matplotlib.path import Path as MplPath
except ImportError as e:
    raise ImportError("matplotlib is required") from e

from cli_anything.qcad.engines.delete_clouded_entities import (
    _entity_inside_polygon,
    _entity_geometry_points,
    _point_in_polygon,
    _segment_intersects_polygon,
)
from cli_anything.qcad.utils.terminal_positions import (
    discover_terminal_positions,
    row_y_center as _term_row_y_center,
    terminal_block_x as _term_block_x,
)
from cli_anything.qcad.utils.layer_fix import fix_layer_visibility


def _terminal_block_x(doc, row_nums: list) -> float | None:
    return _term_block_x(doc, row_nums)


def _entity_right_of_line(ent, x_line: float) -> bool:
    etype = ent.dxftype()
    try:
        if etype in ("TEXT", "MTEXT"):
            return ent.dxf.insert.x > x_line
        elif etype == "LINE":
            return ent.dxf.start.x > x_line or ent.dxf.end.x > x_line
        elif etype == "LWPOLYLINE":
            pts = list(ent.get_points("xy"))
            return any(p[0] > x_line for p in pts)
        elif etype in ("ARC", "CIRCLE"):
            return ent.dxf.center.x + ent.dxf.radius > x_line
        elif etype == "INSERT":
            return ent.dxf.insert.x > x_line
        else:
            try:
                bbox = ent.bbox()
                return bbox.extmin.x > x_line if hasattr(bbox, 'extmin') else True
            except Exception:
                return True
    except Exception:
        return True


def _safe_saveas(doc, out_dxf: str) -> None:
    materials = doc.materials
    original_get = materials.get
    def _safe_get(name):
        result = original_get(name)
        if isinstance(result, str):
            class _DummyMat:
                class dxf:
                    handle = result
            return _DummyMat()
        return result
    materials.get = _safe_get
    try:
        doc.saveas(out_dxf)
    finally:
        materials.get = original_get


def _parse_row_list(s: str) -> List[int]:
    rows = []
    for m in re.finditer(r"\b(\d{1,3})\b", s):
        n = int(m.group(1))
        if n not in rows:
            rows.append(n)
    for m in re.finditer(r"(\d{1,3})\s*[/-]\s*(\d{1,3})", s):
        a, b = int(m.group(1)), int(m.group(2))
        for n in range(a, b + 1):
            if n not in rows:
                rows.append(n)
    rows.sort()
    return rows


def _extract_clone_clause(desc: str) -> Tuple[str, str]:
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
    """Find y-center of a terminal row by its number.

    Uses Wlltermn ATTRIB (TERMNUM) lookup — the correct method.
    Falls back to TEXT '(N)' labels for drawings without block attributes.
    """
    return _term_row_y_center(doc, row_num)


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
    if ent.dxftype() not in ("TEXT", "MTEXT"):
        return False
    txt = _entity_text(ent).strip()
    if re.match(r"^\(\s*\d+\s*\)$", txt):
        return True
    if re.match(r"^T\d+$", txt):
        return True
    return False


def _parse_text_replacements(desc: str, params: Dict[str, Any]) -> Dict[str, str]:
    tr = params.get("text_replacements", {})
    if tr:
        return tr
    nv = params.get("new_value", "") or desc
    for m in re.finditer(r"\b(PLC\d+)\s*(?:to|→|->)\s*(PLC\d+)", nv, re.I):
        old, new = m.group(1), m.group(2)
        if old != new:
            tr[old] = new
    for m in re.finditer(r"\b(CA-?\w+)\s*(?:to|→|->)\s*(CA-?\w+)", nv, re.I):
        old, new = m.group(1), m.group(2)
        if old != new:
            tr[old] = new
    for m in re.finditer(r"\b(\d{5})\s*(?:to|→|->)\s*(\d{5})\b", nv):
        old, new = m.group(1), m.group(2)
        if old != new:
            tr[old] = new
    if "DWG B-SAR-280-" in nv:
        nums = re.findall(r"B-SAR-280-(\d+)", nv)
        if len(nums) == 2:
            tr[f"027{nums[0]}"] = f"027{nums[1]}"
    src_rows = params.get("source_rows", [])
    tgt_rows = params.get("target_rows", [])
    for s, t in zip(src_rows, tgt_rows):
        if s != t:
            tr[f"({s})"] = f"({t})"
    return tr


def _extract_target_text_values(desc: str) -> List[str]:
    m = re.search(r"\bchange\s+related\s+texts?\s+as\s+([A-Z0-9\-,\. ]+?)(?:$|\.|\s+and\s+)", desc, re.I)
    if not m:
        m = re.search(r"\btexts?\s+(?:as|to)\s+([A-Z][A-Z0-9\-,\. ]+?)(?:$|\.|\s+and\s+)", desc, re.I)
    if not m:
        return []
    clause = m.group(1).strip()
    values = [v.strip() for v in clause.split(",")]
    result = []
    for v in values:
        v = v.strip()
        if not v:
            continue
        m2 = re.search(r"(\d{5})$", v)
        if m2:
            result.append(m2.group(1))
        elif re.match(r"^[A-Z]+-?\w*$", v, re.I):
            result.append(v)
    return result


def _discover_source_texts(doc, polygon, source_rows=None):
    from cli_anything.qcad.engines.delete_clouded_entities import _entity_inside_polygon
    msp = doc.modelspace()
    result = {}
    for ent in msp:
        if ent.dxftype() not in ("TEXT", "MTEXT", "ATTRIB"):
            continue
        if not _entity_inside_polygon(ent, polygon):
            continue
        _try_match_text(_entity_text(ent).strip(), result)
    if len(result) >= 3:
        return result
    if source_rows:
        y_min, y_max = float('inf'), float('-inf')
        for row_num in source_rows:
            y = _row_y_center(doc, row_num)
            if y is not None:
                y_min = min(y_min, y - 1.5)
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
    if not txt:
        return
    m = re.match(r"^(PLC\d+)", txt, re.I)
    if m and "PLC" not in result:
        result["PLC"] = m.group(1)
        return
    m = re.match(r"^(CA-?\w+)", txt, re.I)
    if m and "CA" not in result:
        result["CA"] = m.group(1)
        return
    m = re.search(r"(\d{5})", txt)
    if m and "CABLE" not in result:
        result["CABLE"] = m.group(1)


def _is_left_only(ent) -> bool:
    """True if entity is entirely on the left side (all sample points x < 15.0).
    This catches terminal pin arcs (x=14.375) and left-side-only text."""
    pts = _entity_geometry_points(ent)
    if not pts:
        return True
    return all(p[0] < 15.0 for p in pts)


# Block names that are terminal strip infrastructure — never clone these
TERMINAL_BLOCK_NAMES = frozenset(["Wlterm1", "Wlltermn", "Wetermn1"])


def _copy_entity(msp, ent, dy: float, text_replacements: Dict[str, str],
                 skip_insert: bool = True) -> Optional[Any]:
    etype = ent.dxftype()
    doc = msp.doc
    if skip_insert and etype == "INSERT":
        # Skip terminal block INSERTs but allow other blocks (WFEND, WECOIL, etc.)
        if ent.dxf.name in TERMINAL_BLOCK_NAMES:
            return None
        # Also skip INSERTs with ATTRIBs (terminal blocks with number attributes)
        if hasattr(ent, "attribs") and ent.attribs:
            return None
        # Non-terminal INSERTs (WFEND, WECOIL) — clone them
        pass
    if _is_terminal_label(ent):
        return None
    txt = _entity_text(ent).strip()
    if txt.startswith("EPAC"):
        return None
    if _is_left_only(ent):
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
    try:
        new.translate(0, dy, 0)
    except Exception:
        pass
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
    try:
        new.dxf.handle = None
        doc.entitydb.add(new)
        msp.add_entity(new)
    except Exception:
        msp.add_entity(new)
    return new


def _parse_feedback_exclusions(constraints: list) -> dict:
    """Parse USER FEEDBACK constraints for exclusion hints.

    Returns a dict with optional keys:
        "exclude_source_rows": set of source row numbers to skip
        "exclude_terminal_nums": set of terminal numbers to exclude from selection
    """
    result = {
        "exclude_source_rows": set(),
        "exclude_terminal_nums": set(),
    }
    if not constraints:
        return result
    for c in constraints:
        if not isinstance(c, str) or "USER FEEDBACK" not in c:
            continue
        # Pattern: "wire on terminal #3 was mistakenly copied to wire 6"
        # → exclude terminal 3 from source selection
        m = re.search(r"terminal\s*#?\s*(\d+)\s+was\s+(?:mistakenly\s+)?copied",
                       c, re.I)
        if m:
            result["exclude_terminal_nums"].add(int(m.group(1)))
        # Pattern: "do not copy terminal 3" / "exclude terminal 3"
        for m in re.finditer(r"(?:do\s+not\s+\w+\s+|exclude\s+|skip\s+|don'?t\s+\w+\s+)terminal\s*#?\s*(\d+)",
                              c, re.I):
            result["exclude_terminal_nums"].add(int(m.group(1)))
        # Pattern: "do not copy row 4" / "skip row 4"
        for m in re.finditer(r"(?:do\s+not\s+\w+\s+|exclude\s+|skip\s+|don'?t\s+\w+\s+)row\s+(\d+)",
                              c, re.I):
            result["exclude_source_rows"].add(int(m.group(1)))
    return result


def _entity_at_terminal(ent, doc, terminal_num: int, row_y_center_fn) -> bool:
    """Check if an entity is geometrically near a specific terminal row."""
    pts = _entity_geometry_points(ent)
    if not pts:
        return False
    y = row_y_center_fn(doc, terminal_num)
    if y is None:
        return False
    avg_y = sum(p[1] for p in pts) / len(pts)
    return abs(avg_y - y) <= 0.5


class CloudCloneEngine:
    def run(self, dxf_path: str, parameters: Dict[str, Any], out_dxf: str) -> Dict[str, Any]:
        regions = parameters.get("regions", [])
        if isinstance(regions, dict):
            regions = [regions]
        if not regions:
            return {"engine": "cloud_clone", "success": False, "error": "no regions"}

        source_rows = parameters.get("source_rows", [])
        target_rows = parameters.get("target_rows", [])
        if not source_rows or not target_rows:
            desc = parameters.get("target_description", "") or parameters.get("text", "")
            source_rows, target_rows = _infer_rows_from_description(desc)
        if not source_rows or not target_rows:
            return {"engine": "cloud_clone", "success": False,
                    "error": f"cannot infer rows from: {parameters.get('text','(empty)')}"}
        if len(source_rows) != len(target_rows):
            return {"engine": "cloud_clone", "success": False,
                    "error": f"source/target row count mismatch"}

        text_replacements = _parse_text_replacements(
            parameters.get("text", ""), {**parameters, "source_rows": source_rows, "target_rows": target_rows})

        # Parse USER FEEDBACK constraints for exclusion hints (redo comments).
        constraints = parameters.get("constraints", [])
        feedback_excl = _parse_feedback_exclusions(constraints)
        if feedback_excl["exclude_terminal_nums"] or feedback_excl["exclude_source_rows"]:
            print(f"[cloud_clone] feedback exclusions: "
                  f"terminals={feedback_excl['exclude_terminal_nums'] or 'none'}, "
                  f"rows={feedback_excl['exclude_source_rows'] or 'none'}",
                  flush=True)

        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        # Compute dy offsets
        dy_per_pair = []
        for sn, tn in zip(source_rows, target_rows):
            sy = _row_y_center(doc, sn)
            ty = _row_y_center(doc, tn)
            if sy is None or ty is None:
                return {"engine": "cloud_clone", "success": False,
                        "error": f"cannot find row ({sn}) or ({tn})"}
            dy_per_pair.append(ty - sy)

        # Build polygon
        source_polygon = []
        for region in regions:
            verts = region.get("verts", [])
            if len(verts) >= 3:
                source_polygon.extend(verts)
        if not source_polygon:
            for region in regions:
                bbox = region.get("bbox") or region.get("coords")
                if bbox and len(bbox) == 4:
                    source_polygon = [(bbox[0], bbox[2]), (bbox[1], bbox[2]),
                                      (bbox[1], bbox[3]), (bbox[0], bbox[3])]
                    break
        if len(source_polygon) < 3:
            return {"engine": "cloud_clone", "success": False, "error": "insufficient polygon"}

        # Discover text values
        source_texts = _discover_source_texts(doc, source_polygon, source_rows)
        target_values = _extract_target_text_values(
            parameters.get("text", "") or parameters.get("target_description", ""))
        for i, tv in enumerate(target_values):
            if i < 3 and ["PLC", "CA", "CABLE"][i] in source_texts:
                sv = source_texts[["PLC", "CA", "CABLE"][i]]
                if sv != tv:
                    text_replacements[sv] = tv

        # === SELECTION ===
        selected_entities = []
        seen_handles = set()

        row_centers_map = {r: _row_y_center(doc, r) for r in source_rows}
        row_centers_map = {r: y for r, y in row_centers_map.items() if y is not None}
        if not row_centers_map:
            return {"engine": "cloud_clone", "success": False, "error": "no source row centers"}

        src_vals = sorted(row_centers_map.values())
        half_sp = max(abs(src_vals[1] - src_vals[0]) / 2.0 if len(src_vals) > 1 else 0.25, 0.12)
        sup_y_min = min(src_vals) - half_sp
        sup_y_max = max(src_vals) + half_sp
        terminal_x = _terminal_block_x(doc, source_rows)
        x_bound = (terminal_x + 0.2) if terminal_x is not None else float("-inf")

        for ent in list(msp):
            if _is_terminal_label(ent):
                continue
            # Skip terminal block INSERTs but allow non-terminal blocks (WFEND, WECOIL)
            if ent.dxftype() == "INSERT":
                if ent.dxf.name in TERMINAL_BLOCK_NAMES:
                    continue
                if hasattr(ent, "attribs") and ent.attribs:
                    continue
            pts = _entity_geometry_points(ent)
            if not pts:
                continue
            handle = ent.dxf.handle if hasattr(ent.dxf, 'handle') else id(ent)
            if handle in seen_handles:
                continue
            in_cloud = _entity_inside_polygon(ent, source_polygon)
            in_sup = (any(sup_y_min <= p[1] <= sup_y_max for p in pts) and
                      (terminal_x is None or _entity_right_of_line(ent, x_bound)))
            if in_cloud or in_sup:
                selected_entities.append(ent)
                seen_handles.add(handle)

        print(f"[cloud_clone] selected {len(selected_entities)}")
        if not selected_entities:
            return {"engine": "cloud_clone", "success": False, "error": "no entities found"}

        # === FEEDBACK EXCLUSION (redo comments) ===
        # Remove entities at excluded terminal positions from the selection.
        # E.g. "wire on terminal #3 was mistakenly copied to wire 6" →
        # strip entities at terminal 3's y-band so they don't get cloned.
        if feedback_excl["exclude_terminal_nums"]:
            before = len(selected_entities)
            kept = []
            for ent in selected_entities:
                skip = False
                for tnum in feedback_excl["exclude_terminal_nums"]:
                    if tnum not in source_rows:
                        # Only exclude if the terminal isn't a legitimate source row
                        if _entity_at_terminal(ent, doc, tnum, _row_y_center):
                            skip = True
                            break
                if not skip:
                    kept.append(ent)
            if len(kept) < before:
                print(f"[cloud_clone] feedback excluded {before - len(kept)} "
                      f"entities at terminal(s) {feedback_excl['exclude_terminal_nums']}",
                      flush=True)
            selected_entities = kept

        # === ROW ASSIGNMENT (text by content + fallback; geometry by nearest text) ===
        text_by_row: Dict[int, list] = {r: [] for r in source_rows}

        # Step 1: TEXT assignment
        for ent in selected_entities:
            if ent.dxftype() not in ("TEXT", "MTEXT"):
                continue
            txt = _entity_text(ent) or ""
            assigned = False
            for src_row in source_rows:
                if f" G1 {src_row + 10:02d}" in txt or f" G1 {src_row + 10:d}" in txt:
                    try:
                        ent_y = ent.dxf.insert.y
                        src_y = _row_y_center(doc, src_row)
                        if src_y is not None and abs(ent_y - src_y) <= 0.35:
                            text_by_row[src_row].append(ent)
                            assigned = True
                            break
                    except Exception:
                        text_by_row[src_row].append(ent)
                        assigned = True
                        break
                elif f"({src_row})" in txt:
                    text_by_row[src_row].append(ent)
                    assigned = True
                    break
            if not assigned:
                try:
                    ent_y = ent.dxf.insert.y
                except Exception:
                    continue
                nearest = min(source_rows, key=lambda r: abs(_row_y_center(doc, r) or 0 - ent_y))
                nd = abs((_row_y_center(doc, nearest) or 0) - ent_y)
                if nd <= half_sp * 3:
                    text_by_row[nearest].append(ent)

        # Step 2: Geometry assignment by nearest text proximity
        row_entities: Dict[int, list] = {r: [] for r in source_rows}
        for ent in selected_entities:
            if ent.dxftype() in ("TEXT", "MTEXT"):
                continue
            pts = _entity_geometry_points(ent)
            if not pts:
                continue
            ent_y = sum(p[1] for p in pts) / len(pts)
            nr, nd = None, float('inf')
            for src_row, texts in text_by_row.items():
                for te in texts:
                    try:
                        ty = te.dxf.insert.y
                    except Exception:
                        continue
                    d = abs(ty - ent_y)
                    if d < nd:
                        nd = d
                        nr = src_row
            if nr is not None:
                row_entities[nr].append(ent)

        # Merge texts into row_entities
        for r in source_rows:
            row_entities[r].extend(text_by_row[r])

        for r in sorted(source_rows):
            print(f"[cloud_clone] row {r}: {len(row_entities[r])} entities")

        # === TARGET ROW BANDS (for exclusion) ===
        tgt_half = max(abs(_row_y_center(doc, target_rows[1]) - _row_y_center(doc, target_rows[0])) / 2.0
                       if len(target_rows) > 1 else 0.25, 0.15)
        target_bands = {}
        for r in target_rows:
            yc = _row_y_center(doc, r)
            if yc is not None:
                target_bands[r] = (yc - tgt_half, yc + tgt_half)

        # === CLONE (one clone per entity, to its assigned row) ===
        row_to_dy = {source_rows[i]: dy_per_pair[i] for i in range(len(source_rows))}
        row_to_target = {source_rows[i]: target_rows[i] for i in range(len(source_rows))}
        cloned = 0
        clone_details = []
        for src_row, ents in row_entities.items():
            if not ents:
                continue
            dy = row_to_dy[src_row]
            tgt_row = row_to_target[src_row]
            for ent in ents:
                # Check target band exclusion
                pts = _entity_geometry_points(ent)
                if pts:
                    avg_y = sum(p[1] for p in pts) / len(pts)
                    if any(low <= avg_y <= high for low, high in target_bands.values()):
                        continue
                new_ent = _copy_entity(msp, ent, dy, text_replacements, skip_insert=True)
                if new_ent is not None:
                    cloned += 1
                    clone_details.append({
                        "type": ent.dxftype(),
                        "src_row": src_row,
                        "tgt_row": tgt_row,
                        "dy": round(dy, 4),
                        "text": _entity_text(ent)[:50] if _entity_text(ent) else "",
                    })

        _safe_saveas(doc, out_dxf)
        # Fix layer visibility: flip negative colors to positive (layers ON)
        fixed_dxf = out_dxf + ".fixed.dxf"
        fix_layer_visibility(out_dxf, fixed_dxf)
        import shutil as _shutil
        _shutil.move(fixed_dxf, out_dxf)
        return {
            "engine": "cloud_clone",
            "source_rows": source_rows,
            "target_rows": target_rows,
            "dy_per_pair": [round(dy, 4) for dy in dy_per_pair],
            "text_replacements": text_replacements,
            "source_entities_selected": len(selected_entities),
            "cloned": cloned,
            "clone_details": clone_details[:20],
            "polygon_vertex_count": len(source_polygon),
            "output_dxf": out_dxf,
        }
