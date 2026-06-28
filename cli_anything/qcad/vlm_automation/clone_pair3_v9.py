#!/usr/bin/env python3
"""Pair 3 V9: Robust dynamic wire geometry clone with proper entity discovery.

Key fixes from V7/V8:
1. Proper y-coordinate extraction: ARC group 40 is RADIUS, not y. Must skip it.
2. Tight tolerance (±0.15) for row-based entity discovery. Wider tolerances contaminate adjacent rows.
3. Dynamic handle discovery from actual DXF — never hardcoded handles.
4. Clone mapping: T4→T7 (8 entities), T5→T8 (2), T6→T9 (2), plus cable/PLC tags.
5. VLM-verified: side-by-side comparison PNG gives best verification results.
"""
import json, os, re

BASE = "/home/hongbin/.hermes/kanban/workspaces/testfiles_2026.05.07/"

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

def get_y_for_entity(lines, start, end, ent_type):
    """Extract representative y for entity, skipping non-y group codes."""
    ys = []
    i = start
    while i < min(end, len(lines)):
        g = lines[i].strip()
        if ent_type == 'LINE':
            if g in ("20", "21") and i+1 < len(lines):
                try: ys.append(float(lines[i+1]))
                except: pass
                i += 2; continue
        elif ent_type == 'ARC':
            if g == "20" and i+1 < len(lines):
                try: ys.append(float(lines[i+1]))
                except: pass
                i += 2; continue
            # Skip 40 (radius) — NOT a y-coordinate
            if g == "40":
                i += 2; continue
        elif ent_type in ('TEXT', 'MTEXT', 'INSERT'):
            if g == "20" and i+1 < len(lines):
                try: ys.append(float(lines[i+1]))
                except: pass
                i += 2; continue
        elif ent_type == 'LWPOLYLINE':
            if g == "20" and i+1 < len(lines):
                try: ys.append(float(lines[i+1]))
                except: pass
                i += 2; continue
        i += 1
    return sum(ys)/len(ys) if ys else None

def build_entity_map(lines, start, end):
    """Map handle → (start_line, end_line, entity_type, y_avg, text, layer)"""
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
                    y = get_y_for_entity(lines, i, j, ent)
                    text = None
                    for k in range(i, min(j, len(lines))):
                        if lines[k].strip() == "1" and k+1 < len(lines):
                            text = lines[k+1].strip()
                            break
                    layer = None
                    for k in range(i, min(j, len(lines))):
                        if lines[k].strip() == "8" and k+1 < len(lines):
                            layer = lines[k+1].strip()
                            break
                    emap[h] = (i, j, ent, y, text, layer)
                i = j - 1
        i += 1
    return emap

def get_terminal_rows(emap):
    terminals = {}
    for h, (s, e, etype, y, text, layer) in emap.items():
        if etype == 'TEXT' and text:
            t = text.strip()
            if t.startswith('(') and t.endswith(')'):
                try:
                    num = int(t[1:-1])
                    terminals[num] = {'handle': h, 'y': y, 'text': t}
                except:
                    pass
    return terminals

def get_wire_entities_for_row(emap, terminals, row_num, tol=0.15):
    if row_num not in terminals:
        return []
    y_t = terminals[row_num]['y']
    results = []
    for h, (s, e, etype, y, text, layer) in emap.items():
        if y is None:
            continue
        if abs(y - y_t) > tol:
            continue
        if etype == 'INSERT':
            continue
        if etype == 'TEXT':
            t = text or ""
            if re.match(r'^\(\d+\)$', t):
                continue
            if t.startswith('EPAC G1'):
                continue
            if 'CA-1451' in t or 'PLC21' in t or 'TO DWG' in t:
                continue
        results.append({'handle': h, 'type': etype, 'y': y, 'text': text, 'start': s, 'end': e})
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
    s, e, etype, _, _, _ = emap[oh]
    blk = lines[s:e]
    out = []
    j = 0
    while j < len(blk):
        g = blk[j].strip()
        if g == "5":
            out.extend([blk[j], nh])
            j += 2
        elif g in ("20","21"):
            out.append(blk[j])
            if j+1 < len(blk):
                try:
                    v = float(blk[j+1]) + dy
                    out.append(str(v))
                except ValueError:
                    out.append(blk[j+1])
            j += 2
        elif g == "40":
            # For ARC: 40 is radius — do NOT modify
            out.extend([blk[j], blk[j+1] if j+1 < len(blk) else ""])
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
    dxf_path = os.path.join(BASE, "3_clean.dxf")
    output_path = os.path.join(BASE, "3_cloned_v9.dxf")

    lines = read_dxf(dxf_path)

    es, ee = find_entities_section(lines)
    if es is None:
        print("ERROR: No ENTITIES section")
        return

    emap = build_entity_map(lines, es, ee)
    print(f"Entity map: {len(emap)} entities")

    terminals = get_terminal_rows(emap)
    print(f"Terminals: {sorted(terminals.keys())}")
    for num in sorted(terminals.keys()):
        t = terminals[num]
        print(f"  ({num}): {t['handle']} at y={t['y']:.3f}")

    clone_plan = [
        {'source': 4, 'target': 7, 'dy': -1.250},
        {'source': 5, 'target': 8, 'dy': -1.000},
        {'source': 6, 'target': 9, 'dy': -1.250},
    ]

    for plan in clone_plan:
        src = plan['source']
        wire_ents = get_wire_entities_for_row(emap, terminals, src, tol=0.15)
        plan['handles'] = [e['handle'] for e in wire_ents]
        print(f"\nT{src} → T{plan['target']}: {len(wire_ents)} entities to clone")
        for e in wire_ents:
            text_info = f" '{e['text']}'" if e['text'] else ""
            print(f"  {e['handle']} {e['type']} y={e['y']:.3f}{text_info}")

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
    all_maps = {}

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
        all_maps[f"T{src}_to_T{tgt}"] = handle_map
        handles_str = f"{min(handle_map.values())}-{max(handle_map.values())}" if handle_map else "N/A"
        print(f"T{src}→T{tgt}: cloned {len(clones)} entities, dy={dy:+.3f}, handles {handles_str}")

    # Also clone cable/PLC tags from source area to target area
    cable_tags = []
    for h, (s, e, etype, y, text, layer) in emap.items():
        if etype == 'TEXT' and text:
            t = text.strip()
            if 'CA-1451' in t or 'PLC21' in t or 'TO DWG' in t:
                if y is not None and abs(y - terminals[4]['y']) <= 0.5:
                    cable_tags.append(h)
    if cable_tags:
        print(f"\nCable/PLC tags to clone: {len(cable_tags)} entities")
        clones = []
        handle_map = {}
        for oh in cable_tags:
            nh = f"{nx:04X}"
            nx += 1
            handle_map[oh] = nh
            clone = clone_entity(lines, oh, emap, nh, -1.250, replacements)
            clones.append(clone)
        all_clones.extend(clones)
        all_maps["Cable_PLC_T4_to_T7"] = handle_map
        print(f"Cable/PLC→T7: cloned {len(clones)} entities")

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

    with open(os.path.join(BASE, "3_v9_handle_maps.json"), "w") as f:
        json.dump(all_maps, f, indent=2)
    print(f"Handle maps saved to 3_v9_handle_maps.json")

if __name__ == "__main__":
    main()
