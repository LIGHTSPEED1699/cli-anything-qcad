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
    i = es + 1 if es else 0
    while i < (ee if ee else len(lines)) - 1:
        # A group-code line is always followed by a value line.
        code = lines[i].strip()
        val = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if code == "0":
            ent = val
            if ent and ent not in {"SECTION", "ENDSEC", "TABLE", "ENDTAB", "BLOCK", "ENDBLK", "CLASS", "ENDCLASS"}:
                j = i + 2
                h = None
                while j + 1 < (ee if ee else len(lines)):
                    c = lines[j].strip()
                    v = lines[j + 1].strip()
                    if c == "0":
                        break
                    if c == "5":
                        h = v
                    j += 2
                end = j  # index of the next entity's "0" group-code line
                if h:
                    emap[h.upper()] = (i, end)
                i = end
                continue
        i += 2
    return emap, es, ee


def delete_handles(input_dxf: str, output_dxf: str, handles: Set[str]) -> dict:
    """Remove listed handles from a DXF file."""
    path = Path(input_dxf)
    lines = path.read_text().splitlines(keepends=True)
    emap, es, ee = _build_entity_map(lines)
    # Normalize target handles to uppercase
    target = {h.upper() for h in handles}
    skip = set()
    actual_deleted = 0
    for h in target:
        if h in emap:
            s, e = emap[h]
            for idx in range(s, e):
                skip.add(idx)
            actual_deleted += 1
    new_lines = [lines[i] for i in range(len(lines)) if i not in skip]
    Path(output_dxf).write_text("".join(new_lines))
    return {"input": input_dxf, "output": output_dxf, "requested": len(handles),
            "deleted": actual_deleted, "not_found": len(target) - actual_deleted}
