#!/usr/bin/env python3
"""Pair 3 V7: clone only wire elements, skip duplicate terminal labels & instrument labels."""
import json, os

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
            if ent in {"TEXT","MTEXT","LINE","LWPOLYLINE","POLYLINE","INSERT","SOLID","ARC","CIRCLE","ELLIPSE","VERTEX","SEQEND"}:
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
    output_path = os.path.join(WD, "3_cloned_v7.dxf")
    
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
    
    # === V7: Wire-only source handles ===
    # Skipped: terminal number texts (9639, 964D, 9644, 9645)
    # Skipped: instrument labels (9852, 9643, 9853, 9638)
    # Keep: wire geometry + connection metadata + PLC + cable tag
    
    source_t4 = [
        # Wire geometry
        "9846", "9978",    # ARCs
        "963D", "997A", "9979",  # ARC transitions
        "963B",            # Left horizontal LINE
        "9970",            # Right horizontal LINE
        "997B",            # Vertical LINE
        "963C",            # Horizontal LINE
        "9971",            # LINE to right
        "9A79",            # LWPOLYLINE cable
        "9886",            # LINE horizontal
        "9866",            # LINE horizontal
        "9A80",            # LINE cable tag
        "97A4",            # LINE vertical cable
        # Symbols
        "9877",            # INSERT WLGND
        "9983",            # INSERT WFEND
        "9A76",            # INSERT WECOIL
        # Connection labels (not terminal/instrument labels)
        "9972",            # TEXT (W)
        "9A77",            # TEXT '2C SPARE'
        "998A",            # TEXT 'TO DWG...'
        "9A81",            # TEXT 'CA-1451'
        "998B",            # TEXT 'PLC21 (FUTURE)'
    ]
    
    source_t5 = [
        "9648",            # ARC
        "9647",            # Left horizontal
        "9974",            # Right horizontal
        "9A78",            # TEXT '(RED & BLUE)'
        "9975",            # TEXT '(GND)'
    ]
    
    source_t6 = [
        "9847",            # ARC
        "9646",            # Left horizontal
        "9853",            # TEXT 'EPAC G1 15 N'
    ]
    
    offsets = {
        't4': -1.250,
        't5': -1.000,
        't6': -1.250,
    }
    
    replacements = {
        'PLC21': 'PLC22',
        'CA-1451': 'CA-1452',
        '02732': '02733',
        'EPAC G1 15 N': 'EPAC G1 18 N',
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
    
    with open("/tmp/v7_handle_maps.json", "w") as f:
        json.dump(all_handle_maps, f, indent=2)

if __name__ == "__main__":
    main()
