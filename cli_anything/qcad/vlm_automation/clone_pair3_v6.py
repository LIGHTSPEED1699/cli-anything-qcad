#!/usr/bin/env python3
"""Pair 3 V6 clone: correct source groups, no terminal blocks (already exist at targets), proper offsets."""
import json, os, re

WD = "/home/hongbin/.hermes/kanban/workspaces/testfiles_2026.05.07/"

def read_dxf(path):
    with open(path, 'rb') as f:
        return f.read().decode().splitlines(keepends=False)

def write_dxf(path, lines):
    with open(path, 'wb') as f:
        for line in lines:
            f.write(line.encode() + b'\r\n')

def find_entities_section(lines):
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "ENTITIES":
            start = i
        if start is not None and line.strip() == "ENDSEC":
            return (start, i)
    return (None, None)

def build_entity_map(lines, start, end):
    emap = {}
    i = start
    while i < end - 1:
        if lines[i].strip() == "0":
            ent = lines[i+1].strip() if i+1 < len(lines) else ""
            if ent in {"TEXT","MTEXT","LINE","LWPOLYLINE","POLYLINE","INSERT",
                       "ATTRIB","SOLID","ARC","CIRCLE","ELLIPSE","VERTEX","SEQEND"}:
                h = None
                j = i + 2
                while j < end and j < len(lines):
                    if lines[j].strip() == "0":
                        break
                    if lines[j].strip() == "5" and j+1 < len(lines):
                        h = lines[j+1].strip()
                    j += 1
                if h and ent not in {"VERTEX","SEQEND","ATTRIB"}:
                    emap[h] = (i, j)
                i = j - 1
        i += 1
    return emap

def max_handle(lines):
    mx = 0
    for i, line in enumerate(lines):
        if line.strip() == "5" and i+1 < len(lines):
            try:
                n = int(lines[i+1].strip(), 16)
                if n > mx: mx = n
            except ValueError:
                pass
    return mx

def find_gaps(lines, min_gap=39):
    all_h = set()
    for i, line in enumerate(lines):
        if line.strip() == "5" and i+1 < len(lines):
            try:
                all_h.add(int(lines[i+1].strip(), 16))
            except ValueError:
                pass
    sorted_h = sorted(all_h)
    gaps = []
    for i in range(len(sorted_h)-1):
        gap_len = sorted_h[i+1] - sorted_h[i] - 1
        if gap_len >= min_gap:
            gaps.append((sorted_h[i]+1, sorted_h[i+1]-1, gap_len))
    return gaps

def clone_entity(lines, oh, emap, nh, dy, replacements):
    s, e = emap[oh]
    blk = lines[s:e]
    out = []
    j = 0
    while j < len(blk):
        g = blk[j].strip()
        if g == "5":
            out.extend([blk[j], nh])
            j += 2
        elif g in ("20","21","23","24","25","26"):
            out.append(blk[j])
            if j+1 < len(blk):
                try:
                    v = float(blk[j+1]) + dy
                    out.append(str(v))
                except ValueError:
                    out.append(blk[j+1])
            j += 2
        elif g in ("30","31","32"):
            out.extend([blk[j], blk[j+1] if j+1 < len(blk) else ""])
            j += 2
        elif g in ("10","11","13","14","15","16"):
            out.extend([blk[j], blk[j+1] if j+1 < len(blk) else ""])
            j += 2
        elif g in ("1","3"):
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

def main():
    dxf_path = os.path.join(WD, "3_clean.dxf")
    output_path = os.path.join(WD, "3_cloned_v6.dxf")
    
    lines = read_dxf(dxf_path)
    
    es, ee = find_entities_section(lines)
    if es is None:
        print("ERROR: No ENTITIES section")
        return
    
    emap = build_entity_map(lines, es, ee)
    print(f"Entity map: {len(emap)} entities")
    
    mx = max_handle(lines)
    gaps = find_gaps(lines)
    if gaps:
        best = max(gaps, key=lambda g: g[2])
        start_handle = best[0]
    else:
        start_handle = mx + 1
    
    print(f"Start handle: {start_handle:04X} (max existing: {mx:04X})")
    
    # === CORRECTED SOURCE HANDLES ===
    # Removed terminal block INSERTs (they already exist at T7-T9)
    # Kept only wire elements: LINEs, ARCs, TEXTs, simple INSERTs (no ATTRIBs)
    
    source_t4 = [
        # T4 wire elements (around y=19.875, terminal (4))
        "9846", "9978",    # ARCs top-left
        "997B",            # Vertical LINE
        "963B",            # Left horizontal LINE
        "9877",            # INSERT WLGND (no ATTRIBs)
        "9970",            # Right horizontal LINE
        "963D", "997A", "9979",  # ARC transitions
        "9852",            # TEXT 'EPAC G1 14 N'
        "9639",            # TEXT '(4)' terminal number
        "9972",            # TEXT '(W)' wire type
        "9A77",            # TEXT '2C SPARE'
        # T3-associated elements that belong to T4 wire path
        "998B",            # TEXT 'PLC21 (FUTURE)' — was missing in V5
        "963C",            # LINE (horizontal, above T4)
        "9971",            # LINE (to right)
        "9983",            # INSERT WFEND (no ATTRIBs)
        "9A76",            # INSERT WECOIL (no ATTRIBs)
        "9638",            # TEXT 'EPAC G1 14 H'
        "9868", "9885",    # ARCs
        "998A",            # TEXT 'TO DWG. B-SAR-280-02732'
        "9A79",            # LWPOLYLINE cable line
        "9886",            # LINE horizontal above
        "9866",            # LINE horizontal above
        "9A81",            # TEXT 'CA-1451'
        "9A80",            # LINE cable tag underline
        "97A4",            # LINE vertical cable
    ]
    
    source_t5 = [
        # T5 wire elements (around y=19.375, terminal (5))
        "9647",            # Left horizontal LINE
        "9643",            # TEXT 'EPAC G1 15 H'
        "964D",            # TEXT '(5)' terminal number
        "9A78",            # TEXT '(RED & BLUE)'
        "9974",            # Right horizontal LINE
        "9975",            # TEXT '(GND)'
        "9648",            # ARC
    ]
    
    source_t6 = [
        # T6 wire elements (around y=19.125, terminal (6))
        "9847",            # ARC
        "9646",            # Left horizontal LINE
        "9648",            # ARC (shared between T5/T6 visually)
        "9853",            # TEXT 'EPAC G1 15 N'
        "9644",            # TEXT '(6)' terminal number
    ]
    
    # Offsets: target_y - source_y
    # T4 wire center ~19.875 → T7 at y=18.625: dy = -1.250
    # T5 wire center ~19.375 → T8 at y=18.375: dy = -1.000  
    # T6 wire center ~19.125 → T9 at y=17.875: dy = -1.250
    offsets = {
        't4': -1.250,
        't5': -1.000,
        't6': -1.250,
    }
    
    replacements = {
        'PLC21': 'PLC22',
        'CA-1451': 'CA-1452',
        '02732': '02733',
        'EPAC G1 14 H': 'EPAC G1 16 H',
        'EPAC G1 14 N': 'EPAC G1 17 N',
        'EPAC G1 15 H': 'EPAC G1 17 H',
        'EPAC G1 15 N': 'EPAC G1 18 N',
        '(4)': '(7)',
        '(5)': '(8)',
        '(6)': '(9)',
    }
    
    nx = start_handle
    all_clones = []
    all_handle_maps = {}
    
    for group_name, handles in [('t4', source_t4), ('t5', source_t5), ('t6', source_t6)]:
        dy = offsets[group_name]
        clones = []
        handle_map = {}
        for oh in handles:
            if oh not in emap:
                print(f"  SKIP {oh} not found")
                continue
            nh = f"{nx:04X}"
            nx += 1
            handle_map[oh] = nh
            clone = clone_entity(lines, oh, emap, nh, dy, replacements)
            clones.append(clone)
        all_clones.extend(clones)
        all_handle_maps[group_name] = handle_map
        handles_str = f"{min(handle_map.values())}-{max(handle_map.values())}" if handle_map else "N/A"
        print(f"{group_name}: cloned {len(clones)} entities, dy={dy:+.3f}, handles {handles_str}")
    
    # Insert before ENDSEC
    insert_idx = ee
    if ee - 1 >= 0 and lines[ee-1].strip() == "0":
        insert_idx = ee - 1
    else:
        for i in range(ee-1, es, -1):
            if lines[i].strip() == "0":
                insert_idx = i
                break
    
    print(f"Inserting {len(all_clones)} clones at line {insert_idx}")
    
    new_lines = lines[:insert_idx]
    for c in all_clones:
        new_lines.extend(c)
    new_lines.extend(lines[insert_idx:])
    
    # Fix drawing number
    for i, line in enumerate(new_lines):
        if line.strip() == "5" and i+1 < len(new_lines) and new_lines[i+1].strip() == "97B8":
            j = i + 2
            while j < len(new_lines) - 1:
                if new_lines[j].strip() == "1":
                    if j+1 < len(new_lines):
                        new_lines[j+1] = new_lines[j+1].replace("022-122-97024-00002-01", "022-122-97024-00002-02")
                    break
                if new_lines[j].strip() == "0":
                    break
                j += 1
            break
    
    # Ensure EOF at end
    for i in range(len(new_lines)-1, -1, -1):
        if new_lines[i].strip() == "EOF":
            new_lines = new_lines[:i+1]
            break
    else:
        while new_lines and new_lines[-1].strip() == "":
            new_lines.pop()
        if not new_lines or new_lines[-1].strip() != "EOF":
            new_lines.append("EOF")
    
    print(f"Original: {len(lines)} lines, Output: {len(new_lines)} lines")
    
    write_dxf(output_path, new_lines)
    print(f"Wrote: {output_path}")
    
    with open("/tmp/v6_handle_maps.json", "w") as f:
        json.dump(all_handle_maps, f, indent=2)

if __name__ == "__main__":
    main()
