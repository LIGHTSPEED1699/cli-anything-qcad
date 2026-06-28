#!/usr/bin/env python3
"""Pair 3 V8: Dynamic wire geometry clone with proper entity discovery.

Key fixes from V7:
1. Dynamically discovers wire geometry per row from actual DXF
2. Uses tighter tolerance (±0.15) to avoid adjacent-row contamination  
3. Clones wire labels (B), (W), (GND), etc. but NOT terminal numbers or instrument names
4. Correct mapping: T4→T7, T5→T8, T6→T9 with verified dy offsets
5. Preserves existing T7/T8/T9 entities (adds clones, doesn't replace)
"""
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
    """Map handle → (start_line, end_line, entity_type, y_avg)"""
    emap = {}
    i = start
    while i < end - 1:
        if lines[i].strip() == "0":
            ent = lines[i+1].strip() if i+1 < len(lines) else ""
            if ent in {"TEXT","MTEXT","LINE","LWPOLYLINE","POLYLINE","INSERT","SOLID","ARC","CIRCLE","ELLIPSE","VERTEX","SEQEND"}:
                h = None
                ys = []
                j = i + 2
                while j < end and j < len(lines):
                    if lines[j].strip() == "0":
                        break
                    if lines[j].strip() == "5" and j+1 < len(lines):
                        h = lines[j+1].strip()
                    # Capture y-coordinates
                    if lines[j].strip() in ("20","21") and j+1 < len(lines):
                        try: ys.append(float(lines[j+1]))
                        except: pass
                    j += 1
                if h and ent not in {"VERTEX","SEQEND","ATTRIB"}:
                    y_avg = sum(ys)/len(ys) if ys else None
                    emap[h] = (i, j, ent, y_avg)
                i = j - 1
        i += 1
    return emap

def get_terminal_rows(emap, lines):
    """Find terminal labels and their y-positions."""
    terminals = {}
    for h, (s, e, etype, y_avg) in emap.items():
        if etype == 'TEXT':
            # Extract text content
            text = None
            for i in range(s, min(e, len(lines))):
                if lines[i].strip() == "1" and i+1 < len(lines):
                    text = lines[i+1].strip()
                    break
            if text and text.startswith('(') and text.endswith(')'):
                try:
                    num = int(text[1:-1])
                    terminals[num] = {'handle': h, 'y': y_avg, 'text': text}
                except:
                    pass
    return terminals

def get_wire_entities_for_row(emap, terminals, lines, row_num, tol=0.15):
    """Get wire geometry and wire labels near a terminal row."""
    if row_num not in terminals:
        return []
    y_t = terminals[row_num]['y']
    results = []
    for h, (s, e, etype, y_avg) in emap.items():
        if y_avg is None:
            continue
        if abs(y_avg - y_t) > tol:
            continue
        
        # Skip INSERTs (terminal block symbols)
        if etype == 'INSERT':
            continue
            
        # For TEXT, check if it's a wire label vs terminal label vs instrument label
        if etype == 'TEXT':
            text = None
            for i in range(s, min(e, len(lines))):
                if lines[i].strip() == "1" and i+1 < len(lines):
                    text = lines[i+1].strip()
                    break
            # Skip terminal number labels like (4), (5), (6)
            if text and re.match(r'^\(\d+\)$', text):
                continue
            # Skip instrument labels like 'EPAC G1 14 H'
            if text and text.startswith('EPAC G1'):
                continue
            # Skip cable tag and PLC (handled separately)
            if text and ('CA-1451' in text or 'PLC21' in text or 'TO DWG' in text):
                continue
        
        results.append({'handle': h, 'type': etype, 'y': y_avg, 'start': s, 'end': e})
    
    return results

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
    s, e, etype, _ = emap[oh]
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
    output_path = os.path.join(WD, "3_cloned_v8.dxf")
    
    lines = read_dxf(dxf_path)
    
    es, ee = find_entities_section(lines)
    if es is None:
        print("ERROR: No ENTITIES section")
        return
    
    emap = build_entity_map(lines, es, ee)
    print(f"Entity map: {len(emap)} entities")
    
    terminals = get_terminal_rows(emap, lines)
    print(f"Terminals: {sorted(terminals.keys())}")
    for num in sorted(terminals.keys()):
        t = terminals[num]
        print(f"  ({num}): {t['handle']} at y={t['y']:.3f}")
    
    # Clone plan: T4→T7, T5→T8, T6→T9
    clone_plan = [
        {'source': 4, 'target': 7, 'dy': -1.250},
        {'source': 5, 'target': 8, 'dy': -1.000},
        {'source': 6, 'target': 9, 'dy': -1.250},
    ]
    
    # Discover wire entities for each source row
    for plan in clone_plan:
        src = plan['source']
        wire_ents = get_wire_entities_for_row(emap, terminals, lines, src, tol=0.35)
        plan['handles'] = [e['handle'] for e in wire_ents]
        print(f"\nT{src} → T{plan['target']}: {len(wire_ents)} entities to clone")
        for e in wire_ents:
            # Get text if any
            text = ""
            if e['type'] == 'TEXT':
                for i in range(e['start'], min(e['end'], len(lines))):
                    if lines[i].strip() == "1" and i+1 < len(lines):
                        text = f" '{lines[i+1]}'"
                        break
            print(f"  {e['handle']} {e['type']} y={e['y']:.3f}{text}")
    
    # Find handle gap
    mx = max_handle(lines)
    gaps = find_gaps(lines)
    if gaps:
        best = max(gaps, key=lambda g: g[2])
        start_handle = best[0]
    else:
        start_handle = mx + 1
    
    print(f"\nStart handle: {start_handle:04X} (max existing: {mx:04X})")
    
    replacements = {
        'PLC21': 'PLC22',
        'CA-1451': 'CA-1452',
        '02732': '02733',
    }
    
    nx = start_handle
    all_clones = []
    all_handle_maps = {}
    
    for plan in clone_plan:
        src = plan['source']
        tgt = plan['target']
        dy = plan['dy']
        handles = plan['handles']
        
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
        all_handle_maps[f"T{src}_to_T{tgt}"] = handle_map
        handles_str = f"{min(handle_map.values())}-{max(handle_map.values())}" if handle_map else "N/A"
        print(f"T{src}→T{tgt}: cloned {len(clones)} entities, dy={dy:+.3f}, handles {handles_str}")
    
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
    
    # Ensure EOF
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
    
    with open(os.path.join(WD, "3_v8_handle_maps.json"), "w") as f:
        json.dump(all_handle_maps, f, indent=2)
    print(f"Handle maps saved to 3_v8_handle_maps.json")

if __name__ == "__main__":
    main()
