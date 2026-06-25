"""Delete DXF entities by handle using raw DXF line editing."""
from pathlib import Path
from typing import Set


def _build_entity_map(lines: list):
    es, ee = None, None
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
            if ent in {"TEXT","MTEXT","LINE","LWPOLYLINE","POLYLINE","INSERT","SOLID","ARC","CIRCLE","ELLIPSE","ATTRIB"}:
                j = i + 2
                h = None
                while j < len(lines) and j < (ee or len(lines)):
                    if lines[j].strip() == "0":
                        break
                    if lines[j].strip() == "5" and j+1 < len(lines):
                        h = lines[j+1].strip()
                    j += 1
                if h:
                    emap[h] = (i, j)
                i = j - 1
        i += 1
    return emap, es, ee


def delete_handles(input_dxf: str, output_dxf: str, handles: Set[str]) -> dict:
    """Remove listed handles from a DXF file."""
    path = Path(input_dxf)
    lines = path.read_text().splitlines(keepends=True)
    emap, es, ee = _build_entity_map(lines)
    skip = set()
    for h in handles:
        if h in emap:
            s, e = emap[h]
            for idx in range(s, e):
                skip.add(idx)
    new_lines = [lines[i] for i in range(len(lines)) if i not in skip]
    Path(output_dxf).write_text("".join(new_lines))
    return {"input": input_dxf, "output": output_dxf, "deleted": len(handles)}
