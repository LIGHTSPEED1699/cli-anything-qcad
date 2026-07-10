"""Clone DXF entities at the raw byte level, producing entities visible in QCAD.

The proven Variant D approach: read the DXF as raw ASCII bytes, extract entity
blocks by handle, assign new handles in a safe gap below the original max handle,
preserve the owner (group 330 = modelspace block record), offset Y coordinates,
apply text replacements, and insert before ENDSEC.

This produces a DXF that QCAD's ODA importer can load correctly — unlike
ezdxf's duplicate_entity() which creates entities that exist in the data model
but are invisible in QCAD's DWG rendering pipeline.

IMPORTANT: The output DXF must be converted to DWG via QCAD headless
(qcad_convert_dxf2dwg.js) — dwg2bmp and other bitmap renderers may not
show cloned entities even though QCAD GUI renders them correctly.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import ezdxf

from cli_anything.qcad.utils.terminal_positions import (
    discover_terminal_positions as _discover_terminal_positions_impl,
    row_y_center as _term_row_y_center,
)
from cli_anything.qcad.utils.layer_fix import fix_layer_visibility


# ── Terminal discovery helpers (shared with cloud_clone.py) ──────────────────


def _parse_row_list(s: str) -> List[int]:
    """Extract small integers (likely terminal/wire row numbers) from a clause."""
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


def _discover_terminal_positions(doc) -> Dict[int, Dict]:
    """Find all terminal positions from Wlltermn INSERT ATTRIB (TERMNUM).

    Uses the shared terminal_positions module which correctly reads ATTRIB
    values from Wlltermn block inserts (uniform 0.250 spacing) rather than
    standalone TEXT '(N)' entities (non-uniform 0.25/0.50 spacing).

    Returns {terminal_number: {"y": float, "x": float, "handle": str}}
    """
    return _discover_terminal_positions_impl(doc)


def _row_y_center(doc, row_num: int) -> Optional[float]:
    """Return the Y-coordinate of a terminal row by its number.

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
    return ""


def _is_terminal_label(ent) -> bool:
    if ent.dxftype() not in ("TEXT", "MTEXT"):
        return False
    txt = _entity_text(ent).strip()
    return bool(re.match(r"^\(\s*\d+\s*\)$", txt))


def _parse_text_replacements(desc: str, params: Dict[str, Any]) -> Dict[str, str]:
    """Extract text rename mappings from parameters or annotation description."""
    text_replacements = params.get("text_replacements", {})
    if text_replacements:
        return text_replacements

    nv = params.get("new_value", "") or desc

    for m in re.finditer(r"\b(PLC\d+)\s*(?:to|→|->)\s*(PLC\d+)", nv, re.I):
        old, new = m.group(1), m.group(2)
        if old != new:
            text_replacements[old] = new
    for m in re.finditer(r"\b(CA-?\w+)\s*(?:to|→|->)\s*(CA-?\w+)", nv, re.I):
        old, new = m.group(1), m.group(2)
        if old != new:
            text_replacements[old] = new
    for m in re.finditer(r"\b(\d{5})\s*(?:to|→|->)\s*(\d{5})\b", nv):
        old, new = m.group(1), m.group(2)
        if old != new:
            text_replacements[old] = new
    if "DWG B-SAR-280-" in nv:
        nums = re.findall(r"B-SAR-280-(\d+)", nv)
        if len(nums) == 2:
            text_replacements[f"027{nums[0]}"] = f"027{nums[1]}"

    src_rows = params.get("source_rows", [])
    tgt_rows = params.get("target_rows", [])
    for s, t in zip(src_rows, tgt_rows):
        if s != t:
            text_replacements[f"({s})"] = f"({t})"

    return text_replacements


def _extract_target_text_values(desc: str) -> List[str]:
    m = re.search(
        r"\bchange\s+related\s+texts?\s+as\s+([A-Z0-9\-,\s\.]+?)(?:$|\.|\s+and\s+)",
        desc, re.I,
    )
    if not m:
        m = re.search(
            r"\btexts?\s+(?:as|to)\s+([A-Z][A-Z0-9\-,\s\.]+?)(?:$|\.|\s+and\s+)",
            desc, re.I,
        )
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


def _discover_source_texts(doc, source_y: float, dy: float,
                           source_rows: List[int] = None) -> Dict[str, str]:
    """Scan entities near source y-band for PLC/CA/cable identifiers."""
    result: Dict[str, str] = {}
    msp = doc.modelspace()

    source_y_min = source_y - 0.6
    source_y_max = source_y + 0.6

    for ent in msp:
        if ent.dxftype() not in ("TEXT", "MTEXT"):
            continue
        txt = _entity_text(ent).strip()
        if not txt:
            continue
        try:
            y = ent.dxf.insert.y
        except Exception:
            continue
        if y < source_y_min or y > source_y_max:
            continue

        m = re.match(r"^(PLC\d+)", txt, re.I)
        if m and "PLC" not in result:
            result["PLC"] = m.group(1)
        m = re.match(r"^(CA-?\w+)", txt, re.I)
        if m and "CA" not in result:
            result["CA"] = m.group(1)
        m = re.search(r"(\d{5})", txt)
        if m and "CABLE" not in result:
            result["CABLE"] = m.group(1)

    return result


# ── Safe handle range selection ──────────────────────────────────────────────


SAFE_BASE = 0x5458
"""Proven safe handle base: sits in a gap below the original max handle (0x9BD4)
so QCAD's ODA importer preserves them during DXF→DWG conversion.
The V3 reference (2026-05-15) validated 0x5458-0x547E works correctly."""


def _find_safe_handle_gap(raw: bytes) -> int:
    """Scan the DXF for handle gaps and return a safe base handle."""
    handles = set()
    for m in re.finditer(rb'\n  5\n([0-9A-Fa-f]+)\n', raw):
        handles.add(int(m.group(1), 16))
    for h in range(SAFE_BASE, 0, -1):
        if h not in handles:
            return h
    return SAFE_BASE


TERMINAL_BLOCK_NAMES = frozenset(["Wlterm1", "Wlltermn"])


# ── Core engine ──────────────────────────────────────────────────────────────


class TextBasedCloneEngine:
    """Clone all wire entities near source terminal rows to target terminal rows.

    Uses raw DXF byte insertion (Variant D) — the only proven method for
    producing entities that QCAD renders correctly.  Parameters match the
    CloudCloneEngine interface so this can be a drop-in replacement.

    Parameters:
        - source_rows: [4, 5, 6]                    source terminal row numbers
        - target_rows: [7, 8, 9]                    destination row numbers
        - text_replacements: {"PLC21": "PLC22", …}  optional explicit map
        - text: annotation text like "copy wires connected to 4,5,6 to 7,8,9…
                      and change related texts as PLC22, CA-1452, 02733"
                      (used to infer rows + replacements when not provided)
        - target_description: same as text, fallback field
        - source_handles: optional override — dict of {terminal_num: [handles]}
                          Explicit handle lists for each source terminal.
                          Auto-discovered by y-band when omitted.
        - handle_base: optional override — starting handle for cloning.
                       Auto-selected from safe gap when omitted.
        - tolerance: y-band tolerance for entity discovery (default 0.20)
    """

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:

        # ── Parse parameters ────────────────────────────────────────────
        source_rows = parameters.get("source_rows", [])
        target_rows = parameters.get("target_rows", [])
        if not source_rows or not target_rows:
            desc = parameters.get("target_description", "") or parameters.get("text", "")
            source_rows, target_rows = _infer_rows_from_description(desc)

        if not source_rows or not target_rows:
            return {"engine": "text_based_clone", "success": False,
                    "error": "cannot infer source/target rows from parameters"}

        if len(source_rows) != len(target_rows):
            return {"engine": "text_based_clone", "success": False,
                    "error": f"source_rows {source_rows} and target_rows {target_rows} "
                             f"must have same length"}

        text_replacements = _parse_text_replacements(
            parameters.get("text", ""),
            {**parameters, "source_rows": source_rows, "target_rows": target_rows})

        tolerance = parameters.get("tolerance", 0.20)
        handle_base = parameters.get("handle_base")

        # ── Read DXF with ezdxf (read-only) for terminal discovery ──────
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        terminals = _discover_terminal_positions(doc)

        # Compute dy per terminal pair
        dy_per_pair: List[float] = []
        for src_num, tgt_num in zip(source_rows, target_rows):
            src_y = terminals.get(src_num, {}).get("y")
            tgt_y = terminals.get(tgt_num, {}).get("y")
            if src_y is None or tgt_y is None:
                return {"engine": "text_based_clone", "success": False,
                        "error": f"cannot find terminal row labels ({src_num}) "
                                 f"or ({tgt_num}) in DXF"}
            dy_per_pair.append(tgt_y - src_y)

        # ── Discover source entity handles ──────────────────────────────
        # Two modes:
        #   A. Explicit handles from parameters (for known/hardcoded lists)
        #   B. Auto-discovery by y-band around each source terminal
        explicit_handles = parameters.get("source_handles", {})
        if isinstance(explicit_handles, dict) and len(explicit_handles) > 0:
            # Mode A: use explicit handle lists per terminal
            source_groups = {}
            for tnum in source_rows:
                handles = explicit_handles.get(tnum, explicit_handles.get(str(tnum), []))
                source_groups[tnum] = handles
        else:
            # Mode B: auto-discover by y-band
            raw_groups = self._discover_source_entities(
                doc, terminals, source_rows, tolerance)
            source_groups = self._deduplicate_entity_groups(
                raw_groups, doc, terminals)

        # ── Read raw DXF bytes ──────────────────────────────────────────
        raw = Path(dxf_path).read_bytes()

        # Locate ENTITIES section
        ent_start = raw.find(b'\n  0\nSECTION\n  2\nENTITIES\n')
        if ent_start == -1:
            return {"engine": "text_based_clone", "success": False,
                    "error": "cannot find ENTITIES section in DXF"}
        ent_end = raw.find(b'\n  0\nENDSEC\n', ent_start)
        if ent_end == -1:
            return {"engine": "text_based_clone", "success": False,
                    "error": "cannot find ENDSEC after ENTITIES section"}

        # Find modelspace owner handle
        ms_owner = self._find_ms_owner(raw, doc)

        # Find safe handle base
        if handle_base is None:
            handle_base = _find_safe_handle_gap(raw)

        # ── Clone entities ──────────────────────────────────────────────
        new_blocks: List[bytes] = []
        handle_counter = handle_base
        clone_details: List[Dict] = []
        unique_handles_cloned: Set[str] = set()

        for i, (src_num, tgt_num) in enumerate(zip(source_rows, target_rows)):
            dy = dy_per_pair[i]
            replacements = dict(text_replacements)

            # Also derive per-terminal replacements from wire labels
            src_y = terminals[src_num]["y"]
            discovered = _discover_source_texts(doc, src_y, dy, source_rows)
            target_values = _extract_target_text_values(
                parameters.get("text", "") or parameters.get("target_description", ""))
            type_order = ["PLC", "CA", "CABLE"]
            for j, tv in enumerate(target_values):
                if j < len(type_order) and type_order[j] in discovered:
                    src_val = discovered[type_order[j]]
                    if src_val != tv:
                        replacements[src_val] = tv

            handles = source_groups.get(src_num, source_groups.get(str(src_num), []))
            if isinstance(handles, str):
                handles = [handles]
            if isinstance(handles, set):
                handles = sorted(handles)
            if not isinstance(handles, (list, tuple)):
                handles = [handles]

            for h in handles:
                h = str(h).strip()
                if not h:
                    continue

                # Skip duplicate handles across terminal groups
                if h in unique_handles_cloned:
                    continue
                unique_handles_cloned.add(h)

                pattern = f'\n  5\n{h}\n'.encode()
                hpos = raw.find(pattern, ent_start, ent_end)
                if hpos == -1:
                    continue

                estart = raw.rfind(b'\n  0\n', ent_start, hpos)
                eend = raw.find(b'\n  0\n', hpos + 5, ent_end)
                if eend == -1:
                    eend = ent_end

                block = raw[estart:eend]

                # Skip terminal block INSERTs (with ATTRIB sub-entities)
                if b'\n 66\n     1\n' in block:
                    continue

                # Skip terminal block INSERTs by block name (Wlterm1, Wlltermn)
                # These may not have group 66 but are still terminal blocks
                etype = block[5:block.find(b'\n', 5)].decode('ascii', errors='replace')
                if etype == 'INSERT':
                    bname_match = re.search(rb'\n  2\n([^\n]+)', block)
                    if bname_match:
                        bname = bname_match.group(1).decode('ascii', errors='replace')
                        if bname in TERMINAL_BLOCK_NAMES:
                            continue

                if etype in ('ATTRIB', 'SEQEND'):
                    continue

                # Skip terminal row labels like (4), (5)
                if etype in ('TEXT', 'MTEXT'):
                    txt_match = re.search(rb'\n  1\n([^\n]+)', block)
                    if txt_match:
                        txt = txt_match.group(1).decode('ascii', errors='replace').strip()
                        if re.match(r'^\(\d+\)$', txt):
                            continue

                # Assign new handle
                new_handle = f'{handle_counter:04X}'
                handle_counter += 1

                block = block.replace(pattern, f'\n  5\n{new_handle}\n'.encode())

                # Set owner (330) to modelspace
                block = self._set_owner(block, ms_owner)

                # Offset Y coordinates (group 20 and 21)
                block = self._offset_y(block, dy)

                # Apply text replacements
                for old_text, new_text in replacements.items():
                    block = block.replace(old_text.encode(), new_text.encode())

                new_blocks.append(block)
                clone_details.append({
                    "source_terminal": src_num,
                    "target_terminal": tgt_num,
                    "type": etype,
                    "source_handle": h,
                    "new_handle": new_handle,
                    "dy": round(dy, 4),
                })

        # ── Insert cloned blocks before ENDSEC ──────────────────────────
        if not new_blocks:
            return {"engine": "text_based_clone", "success": False,
                    "error": "no entities found to clone"}

        insert_point = ent_end
        new_raw = raw[:insert_point] + b''.join(new_blocks) + raw[insert_point:]

        # Sanity checks
        assert b"b'" not in new_raw, \
            "Bytes repr artifact detected (new_handle was bytes not str)"
        assert len(new_raw) > len(raw), \
            f"Data loss: {len(new_raw)} < {len(raw)}"

        Path(out_dxf).write_bytes(new_raw)

        # Fix layer visibility: flip negative colors to positive (layers ON)
        fixed_dxf = out_dxf + ".fixed.dxf"
        fix_layer_visibility(out_dxf, fixed_dxf)
        import shutil as _shutil
        _shutil.move(fixed_dxf, out_dxf)

        # Verify with ezdxf
        try:
            doc2 = ezdxf.readfile(out_dxf)
            msp2 = doc2.modelspace()
            final_count = len(list(msp2))
        except Exception:
            final_count = -1

        return {
            "engine": "text_based_clone",
            "source_rows": source_rows,
            "target_rows": target_rows,
            "dy_per_pair": [round(dy, 4) for dy in dy_per_pair],
            "text_replacements": text_replacements,
            "source_entity_groups": {
                str(k): len(v) for k, v in source_groups.items()
            },
            "cloned": len(new_blocks),
            "clone_details": clone_details[:30],
            "handle_range": f"0x{handle_base:04X}-0x{handle_counter-1:04X}",
            "output_dxf": out_dxf,
            "final_entity_count": final_count,
        }

    # ── Internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _discover_source_entities(doc, terminals: Dict[int, Dict],
                                   source_rows: List[int],
                                   tolerance: float) -> Dict[int, Set[str]]:
        """Discover entity handles by y-band around each source terminal position.

        Three-phase approach:
        Phase 1: Tight y-band (±tolerance) for main wire group, x > 14.0 for all types.
        Phase 2: Cable tag TEXT discovery in wider y-band (±0.6).
        Phase 3: Non-TEXT geometry associated with cable tags in wider y-band.

        All phases enforce x > 14.0 to exclude left-side wiring.
        """
        from collections import defaultdict
        groups: Dict[int, Set[str]] = defaultdict(set)
        msp = doc.modelspace()
        RIGHT_X = 14.0

        # Phase 1: Tight y-band wire group
        for tnum in source_rows:
            src_y = terminals[tnum]["y"]
            y_min = src_y - tolerance
            y_max = src_y + tolerance

            for ent in msp:
                etype = ent.dxftype()
                if etype in ('ATTRIB', 'SEQEND'):
                    continue
                if etype == 'INSERT' and getattr(ent.dxf, 'attribs_follow', False):
                    continue
                if _is_terminal_label(ent):
                    continue

                y = TextBasedCloneEngine._entity_y(ent)
                if y is None:
                    continue

                if y_min <= y <= y_max:
                    sx = TextBasedCloneEngine._entity_x(ent)
                    if sx is not None and sx > RIGHT_X:
                        handle = ent.dxf.handle
                        if handle:
                            groups[tnum].add(handle)

        # Phase 2: Cable tag TEXT discovery
        for tnum in source_rows:
            src_y = terminals[tnum]["y"]
            y_min = src_y - 0.6
            y_max = src_y + 0.6

            for ent in msp:
                if ent.dxftype() not in ('TEXT', 'MTEXT'):
                    continue
                txt = _entity_text(ent).strip()
                if not txt:
                    continue

                if not (re.match(r'^PLC\d+', txt, re.I) or
                        re.match(r'^CA-?\w+', txt, re.I) or
                        re.search(r'B-SAR-280-\d+', txt) or
                        txt.startswith('TO DWG')):
                    continue

                try:
                    y = ent.dxf.insert.y
                except Exception:
                    continue
                if y < y_min or y > y_max:
                    continue

                handle = ent.dxf.handle
                if handle:
                    groups[tnum].add(handle)

        # Phase 3: Geometry for cable tags (non-TEXT, non-INSERT entities)
        for tnum in source_rows:
            src_y = terminals[tnum]["y"]
            y_min = src_y - 0.6
            y_max = src_y + 0.6

            for ent in msp:
                etype = ent.dxftype()
                if etype in ('ATTRIB', 'SEQEND', 'TEXT', 'MTEXT', 'INSERT'):
                    continue
                y = TextBasedCloneEngine._entity_y(ent)
                if y is None or y < y_min or y > y_max:
                    continue
                sx = TextBasedCloneEngine._entity_x(ent)
                if sx is not None and sx > RIGHT_X:
                    handle = ent.dxf.handle
                    if handle:
                        groups[tnum].add(handle)

        return dict(groups)

    @staticmethod
    def _deduplicate_entity_groups(groups: Dict[int, Set[str]],
                                    doc, terminals) -> Dict[int, List[str]]:
        """Reassign each handle to its closest source terminal by y-distance.

        After multi-phase discovery, a single entity may appear in multiple
        terminal groups (e.g. an ARC that spans two rows or a cable tag
        discovered in two terminals' Phase 2/3 ranges).  This ensures each
        entity is assigned to exactly one terminal — the one with the closest
        y-coordinate.
        """
        from collections import defaultdict
        term_ys = {tnum: info["y"] for tnum, info in terminals.items()}

        handle_terminals: Dict[str, List[int]] = defaultdict(list)
        for tnum, handles in groups.items():
            for h in handles:
                handle_terminals[h].append(tnum)

        result: Dict[int, Set[str]] = defaultdict(set)
        for h, term_list in handle_terminals.items():
            if len(term_list) == 1:
                result[term_list[0]].add(h)
            else:
                best_tnum = min(term_list, key=lambda t: abs(
                    term_ys.get(t, 0) - _find_entity_y_by_handle(doc, h)))
                result[best_tnum].add(h)

        return {k: sorted(v) for k, v in result.items()}

    @staticmethod
    def _entity_y(ent) -> Optional[float]:
        """Extract primary y-position from any entity type."""
        for attr in ('insert', 'center', 'start', 'end'):
            try:
                val = getattr(ent.dxf, attr).y
                return val
            except Exception:
                continue
        return None

    @staticmethod
    def _entity_x(ent) -> Optional[float]:
        """Extract primary x-position from any entity type."""
        for attr in ('insert', 'center', 'start', 'end'):
            try:
                val = getattr(ent.dxf, attr).x
                return val
            except Exception:
                continue
        return None

    @staticmethod
    def _find_ms_owner(raw: bytes, doc=None) -> str:
        """Find the modelspace block record handle.

        Scans the BLOCKS section for *Model_Space and returns its handle.
        Falls back to common known value 9AA0.
        """
        blocks_start = raw.find(b'\n  0\nSECTION\n  2\nBLOCKS\n')
        if blocks_start < 0:
            return '9AA0'
        blocks_end = raw.find(b'\n  0\nENDSEC\n', blocks_start)
        if blocks_end < 0:
            return '9AA0'

        ms_pos = raw.find(b'*Model_Space', blocks_start, blocks_end)
        if ms_pos < 0:
            return '9AA0'

        chunk = raw[:ms_pos]
        matches = re.findall(rb'\n  5\n([0-9A-Fa-f]+)\n', chunk)
        if matches:
            return matches[-1].decode('ascii')
        return '9AA0'

    @staticmethod
    def _set_owner(block: bytes, owner_handle: str) -> bytes:
        """Ensure the entity block has group 330 set to the modelspace owner."""
        owner_match = re.search(rb'\n330\n([0-9A-Fa-f]+)\n', block)
        owner_bytes = f'\n330\n{owner_handle}\n'.encode()
        if owner_match:
            return block.replace(owner_match.group(0), owner_bytes)
        else:
            return block.replace(
                b'\n  5\n',
                b'\n  5\n' + owner_bytes, 1)

    @staticmethod
    def _offset_y(block: bytes, dy: float) -> bytes:
        """Offset all Y coordinates (group codes 20 and 21) in the entity block."""
        def offset_one(match):
            val = float(match.group(2))
            return match.group(1) + f'{val + dy:.6f}'.encode() + match.group(3)

        block = re.sub(rb'(\n 20\n)([\-0-9.]+)(\n)', offset_one, block)
        block = re.sub(rb'(\n 21\n)([\-0-9.]+)(\n)', offset_one, block)
        return block


# ── Module-level helper needed by dedup ├────────────────────────────────────


def _find_entity_y_by_handle(doc, handle: str) -> float:
    """Find an entity's y-coordinate by its DXF handle.

    Used by TextBasedCloneEngine._deduplicate_entity_groups.
    """
    try:
        ent = doc.entitydb.get(handle)
        if ent is None:
            return 0.0
        return TextBasedCloneEngine._entity_y(ent) or 0.0
    except Exception:
        return 0.0
