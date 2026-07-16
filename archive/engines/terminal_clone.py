"""Clone terminal-row wiring from source to target rows."""
import re
from pathlib import Path
from typing import Dict, List, Tuple


def _read_dxf(path: str) -> List[str]:
    return Path(path).read_bytes().decode().splitlines(keepends=False)


def _write_dxf(path: str, lines: List[str]) -> None:
    with open(path, "wb") as f:
        for line in lines:
            f.write(line.encode() + b"\r\n")


def _build_entity_map(lines: List[str]) -> Tuple[Dict, int, int]:
    es = ee = None
    for i, line in enumerate(lines):
        if line.strip() == "ENTITIES":
            es = i
        if es is not None and line.strip() == "ENDSEC":
            ee = i
            break
    emap = {}
    i = es if es else 0
    while i < (ee if ee else len(lines)) - 1:
        if lines[i].strip() == "0":
            ent = lines[i+1].strip() if i+1 < len(lines) else ""
            if ent in {"TEXT","MTEXT","LINE","LWPOLYLINE","POLYLINE","INSERT","SOLID","ARC","CIRCLE","ELLIPSE","VERTEX","SEQEND","ATTRIB"}:
                j = i + 2
                h = None
                while j < len(lines) and j < (ee or len(lines)):
                    if lines[j].strip() == "0":
                        break
                    if lines[j].strip() == "5" and j+1 < len(lines):
                        h = lines[j+1].strip()
                    j += 1
                if h and ent not in {"VERTEX","SEQEND"}:
                    emap[h] = (i, j, ent)
                i = j - 1
        i += 1
    return emap, es, ee


def _get_terminals(doc) -> Dict[int, Dict]:
    msp = doc.modelspace()
    inserts = []
    for e in msp:
        if e.dxftype() == "INSERT" and e.dxf.name == "Wlltermn":
            inserts.append((e.dxf.insert[1], e.dxf.handle))
    inserts.sort(reverse=True)
    return {i+1: {"y": y, "handle": h} for i, (y, h) in enumerate(inserts)}


def _get_text_info(doc) -> Dict[str, Dict]:
    texts = {}
    for e in doc.modelspace():
        etype = e.dxftype()
        if etype not in ("TEXT", "MTEXT"):
            continue
        try:
            texts[e.dxf.handle] = {
                "x": e.dxf.insert[0], "y": e.dxf.insert[1],
                "text": (getattr(e.dxf, "text", "") or "").strip(),
                "etype": etype,
            }
        except Exception:
            pass
    return texts


def _source_handles(texts: Dict, terminals: Dict, src_num: int, tol: float = 0.15) -> List[str]:
    src_y = terminals[src_num]["y"]
    results = []
    for h, info in texts.items():
        distances = {tnum: abs(info["y"] - terminals[tnum]["y"]) for tnum in terminals}
        nearest_t = min(distances, key=distances.get)
        if nearest_t != src_num or distances[nearest_t] > tol:
            continue
        txt = info["text"]
        if re.match(r"^\(\d+\)$", txt):
            continue
        if txt.startswith("EPAC G1"):
            continue
        results.append(h)
    return results


def _clone_block(lines: List[str], s: int, e: int, new_handle: str,
                 dy: float, replacements: Dict[str, str]) -> List[str]:
    blk = lines[s:e]
    out = []
    j = 0
    while j < len(blk):
        g = blk[j].strip()
        if g == "5":
            out.extend([blk[j], new_handle])
            j += 2
        elif g in ("20","21","23","24","25","26"):
            out.append(blk[j])
            if j+1 < len(blk):
                try:
                    v = float(blk[j+1]) + dy
                    out.append(str(v))
                except:
                    out.append(blk[j+1])
            j += 2
        elif g in ("1", "3"):
            out.append(blk[j])
            if j+1 < len(blk):
                txt = blk[j+1]
                for old, new in replacements.items():
                    txt = txt.replace(old, new)
                out.append(txt)
            j += 2
        else:
            out.append(blk[j])
            j += 1
    return out


def _remove_overlaps(raw_lines: List[str], emap: Dict, texts: Dict,
                     terminals: Dict, clone_pairs: List[Tuple]) -> List[str]:
    remove_handles = set()
    for _, tgt, _, _ in clone_pairs:
        tgt_y = terminals[tgt]["y"]
        for h, info in texts.items():
            if abs(info["y"] - tgt_y) <= 0.15:
                txt = info["text"]
                if re.match(r"^\(\d+\)$", txt) or txt.startswith("EPAC G1"):
                    remove_handles.add(h)
    skip = set()
    for h in remove_handles:
        if h in emap:
            s, e, _ = emap[h]
            for idx in range(s, e):
                skip.add(idx)
    return [raw_lines[i] for i in range(len(raw_lines)) if i not in skip]


class TerminalCloneEngine:
    """Duplicate terminal wiring rows."""

    def run(self, dxf_path: str, out_dxf: str,
            clone_pairs: List[Tuple[int, int, float, Dict[str, str]]],
            drawing_number_replacement: Tuple[str, str] = None) -> Dict:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        raw_lines = _read_dxf(dxf_path)
        emap, es, ee = _build_entity_map(raw_lines)
        terminals = _get_terminals(doc)
        texts = _get_text_info(doc)

        filtered_lines = _remove_overlaps(raw_lines, emap, texts, terminals, clone_pairs)
        emap2, es2, ee2 = _build_entity_map(filtered_lines)

        next_handle = 0x5454
        all_clones: List[str] = []
        for src, tgt, dy, replacements in clone_pairs:
            src_y = terminals[src]["y"]
            src_hs = _source_handles(texts, terminals, src)
            for h in src_hs:
                nh = f"{next_handle:04X}"
                next_handle += 1
                if h not in emap:
                    continue
                s, e, _ = emap[h]
                all_clones.extend(_clone_block(raw_lines, s, e, nh, dy, replacements))

        insert_idx = ee2
        for i in range(ee2 - 1, es2, -1):
            if filtered_lines[i].strip() == "0":
                insert_idx = i
                break

        new_lines = filtered_lines[:insert_idx]
        new_lines.extend(all_clones)
        new_lines.extend(filtered_lines[insert_idx:])

        if drawing_number_replacement:
            old_num, new_num = drawing_number_replacement
            for i, line in enumerate(new_lines):
                if line.strip() == "5" and i+1 < len(new_lines) and new_lines[i+1].strip() == "97B8":
                    j = i + 2
                    while j < len(new_lines) - 1:
                        if new_lines[j].strip() == "1":
                            if j+1 < len(new_lines):
                                new_lines[j+1] = new_lines[j+1].replace(old_num, new_num)
                            break
                        if new_lines[j].strip() == "0":
                            break
                        j += 1
                    break

        for i in range(len(new_lines) - 1, -1, -1):
            if new_lines[i].strip() == "EOF":
                new_lines = new_lines[:i+1]
                break
        else:
            while new_lines and new_lines[-1].strip() == "":
                new_lines.pop()
            if not new_lines or new_lines[-1].strip() != "EOF":
                new_lines.append("EOF")

        _write_dxf(out_dxf, new_lines)
        return {"pairs": len(clone_pairs), "cloned_entities": len(all_clones), "dxf": out_dxf}
