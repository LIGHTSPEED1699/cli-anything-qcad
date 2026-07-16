#!/usr/bin/env python3
"""
Safe DXF text-clearing tool — entity-parsing approach (legacy).

DEPRECATED for LibreDWG DXFs — use `safe_dxf_text_clear_v2.py` (raw handle-based)
instead. Kept for ezdxf-generated or hand-edited DXFs without OBJECTS-section
handle tables.

Usage: python3 safe_dxf_text_clear.py input.dxf output.dxf [handle1 handle2 ...]
"""
import sys
from pathlib import Path

def find_section(lines, name):
    for i in range(1, len(lines)):
        if lines[i].strip().upper() == name.upper() and lines[i-1].strip() == '2':
            return i
    raise ValueError(f"Section {name} not found")

def clear_text_for_handles(lines, handles_to_clear):
    """Return new line list with group-code-1 values set to space for target handles."""
    handles_to_clear = {h.upper() for h in handles_to_clear}
    entities_start = find_section(lines, "ENTITIES")
    entities_end = next((i for i in range(entities_start+1, len(lines)) if lines[i].strip()=='ENDSEC'), None)

    before = lines[:entities_start+1]
    after = lines[entities_end+1:]
    new_entities = []

    i = entities_start + 1
    while i < entities_end:
        if lines[i].strip() == '0' and i+1 < entities_end and lines[i+1].strip() == 'ENDSEC':
            new_entities.extend([lines[i], lines[i+1]])
            break

        block = []
        while i < entities_end:
            block.append(lines[i])
            if i+1 < entities_end:
                block.append(lines[i+1])
            i += 2
            if i+1 < entities_end and lines[i].strip() == '0':
                if lines[i+1].strip() == 'ENDSEC':
                    new_entities.extend(block)
                    new_entities.extend([lines[i], lines[i+1]])
                    i += 2
                    i = entities_end + 999
                    break
                break

        if i >= entities_end + 999:
            break

        this_handle = None
        for j in range(0, len(block)-1, 2):
            if block[j].strip() == '5':
                this_handle = block[j+1].strip().upper()
                break

        if this_handle in handles_to_clear:
            new_block = []
            j = 0
            while j < len(block):
                if block[j].strip() == '1':
                    new_block.append(block[j])
                    new_block.append(' \r\n')  # single space — invisible but valid
                    j += 2
                else:
                    new_block.append(block[j])
                    j += 1
            new_entities.extend(new_block)
        else:
            new_entities.extend(block)

    return before + new_entities + after

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 safe_dxf_text_clear.py input.dxf output.dxf [handle1 handle2 ...]")
        sys.exit(1)

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    handles = sys.argv[3:] if len(sys.argv) > 3 else []

    with open(in_path, 'r', encoding='utf-8', errors='replace', newline='') as f:
        lines = f.readlines()

    new_lines = clear_text_for_handles(lines, handles)

    with open(out_path, 'wb') as f:
        for line in new_lines:
            f.write(line.encode('utf-8', errors='replace'))

    print(f"Cleared text for {len(handles)} handles -> {out_path} ({out_path.stat().st_size} bytes)")
