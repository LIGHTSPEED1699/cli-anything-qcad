#!/usr/bin/env python3
"""Pair 3 V3 clone: clean DXF cloning with CRLF, safe handles, correct Y-only offset, proper insertion point."""
import json, os

WD = "/home/hongbin/.hermes/kanban/workspaces/testfiles_2026.05.07/"

def read_dxf(path):
    with open(path, 'rb') as f:
        raw = f.read()
    # Keep line endings as-is; split on any newline but preserve original for write
    return raw.decode().splitlines(keepends=False)

def write_dxf(path, lines):
    with open(path, 'wb') as f:
        for line in lines:
            f.write(line.encode() + b'\r\n')

def find_entities_section(lines):
    """Return (start_line, end_line) for ENTITIES section."""
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "ENTITIES":
            start = i
        if start is not None and line.strip() == "ENDSEC":
            return (start, i)
    return (None, None)

def build_entity_map(lines, start, end):
    """Map handle -> (start_index, end_index) within ENTITIES section."""
    emap = {}
    i = start
    while i < end - 1:
        if lines[i].strip() == "0":
            ent = lines[i+1].strip()
            if ent in {"TEXT","MTEXT","LINE","LWPOLYLINE","POLYLINE","INSERT",
                       "ATTRIB","SOLID","ARC","CIRCLE","ELLIPSE","VERTEX","SEQEND"}:
                h = None
                j = i + 2
                while j < end - 1:
                    if lines[j].strip() == "0" and j > i:
                        break
                    if lines[j].strip() == "5":
                        h = lines[j+1].strip() if j+1 < len(lines) else None
                    j += 1
                if h:
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
    """Find contiguous handle gaps below max_handle."""
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
    return gaps, max(all_h) if all_h else 0

def clone_entities(lines, source_handles, dy, replacements, ent_map, start_handle):
    """Clone entities with new handles and coordinate/text replacements."""
    nx = start_handle
    clones = []
    handle_map = {}
    for oh in source_handles:
        if oh not in ent_map:
            print(f"  SKIP {oh} not found")
            continue
        s, e = ent_map[oh]
        blk = lines[s:e]
        nh = f"{nx:04X}"
        nx += 1
        handle_map[oh] = nh

        out = []
        j = 0
        while j < len(blk):
            g = blk[j].strip()
            if g == "5":
                out.extend([blk[j], nh])
                j += 2
            elif g in ("20", "21"):
                # Y coordinate: add dy
                out.append(blk[j])
                if j+1 < len(blk):
                    try:
                        v = float(blk[j+1]) + dy
                        out.append(str(v))
                    except ValueError:
                        out.append(blk[j+1])
                j += 2
            elif g in ("30", "31"):
                # Z coordinate: keep as-is (do NOT add dy)
                out.extend([blk[j], blk[j+1] if j+1 < len(blk) else ""])
                j += 2
            elif g in ("10", "11", "13", "14", "15", "16", "38", "40", "50", "51"):
                # Other coordinates: keep as-is
                out.extend([blk[j], blk[j+1] if j+1 < len(blk) else ""])
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
        clones.append(out)
    return clones, handle_map

def fix_drawing_number(lines):
    """Change drawing number text from -01 to -02."""
    for i, line in enumerate(lines):
        if line.strip() == "5" and i+1 < len(lines) and lines[i+1].strip() == "97B8":
            j = i + 2
            while j < len(lines) - 1:
                if lines[j].strip() == "1":
                    if j+1 < len(lines):
                        lines[j+1] = lines[j+1].replace("022-122-97024-00002-01", "022-122-97024-00002-02")
                        print(f"  Fixed drawing number at line {j+1}: '{lines[j+1]}'")
                    break
                if lines[j].strip() == "0":
                    break
                j += 1
            break
    return lines

def main():
    dxf_path = os.path.join(WD, "3.dxf")
    output_path = os.path.join(WD, "3_cloned_v3.dxf")

    # Same source handles as V2 (wire elements for T4-T6 + cable tag + end symbols)
    source_handles = [
        # T6 wire elements
        "9847", "9646", "9853", "9644", "9648", "9647",
        "9643", "964D", "9A78", "9974", "9975", "9A77",
        "9846", "9978",
        # T5 wire elements
        "997B", "9970", "963B", "9852", "9639", "9972",
        "963D", "997A", "9979", "9867",
        # T4 wire elements
        "998A", "963C", "9971", "9638", "9868", "9885",
        # Cable/PLC area
        "9886", "9866", "9A81", "9A80", "97A4", "9A79",
        "9877", "9983", "9A76"
    ]

    replacements = {
        "PLC21": "PLC22",
        "CA-1451": "CA-1452",
        "02732": "02733",
        " 14 H": " 16 H",
        " 14 N": " 17 N",
        " 15 H": " 17 H",
        " 15 N": " 17 N",
        "(4)": "(7)",
        "(5)": "(8)",
        "(6)": "(9)",
    }

    lines = read_dxf(dxf_path)
    original_lines = lines[:]

    # Find safe handle gap
    gaps, mx = find_gaps(lines)
    if not gaps:
        print(f"ERROR: No handle gap >=39 found below max {mx:04X}")
        return
    
    # Pick the largest gap
    best_gap = max(gaps, key=lambda g: g[2])
    start_handle = best_gap[0]
    print(f"Using handle gap {best_gap[0]:04X}-{best_gap[1]:04X} ({best_gap[2]} free), start={start_handle:04X}")

    # Find ENTITIES section
    es, ee = find_entities_section(lines)
    if es is None:
        print("ERROR: No ENTITIES section found")
        return
    print(f"ENTITIES section: {es}-{ee}")

    # Build entity map
    emap = build_entity_map(lines, es, ee)
    print(f"Entity map: {len(emap)} entities")

    # Fix drawing number first
    lines = fix_drawing_number(lines)

    # Clone
    clones, handle_map = clone_entities(lines, source_handles, -0.75, replacements, emap, start_handle)
    print(f"Cloned {len(clones)} entities, new handles {start_handle:04X}-{start_handle+len(clones)-1:04X}")

    # Find insertion point: right before the "0" + "ENDSEC" pair
    insert_idx = ee  # ee is the ENDSEC line
    # But we need to insert BEFORE the "0" group code of the ENDSEC pair
    # The ENDSEC pair is (ee-1, ee) if ee-1 is the "0" line
    if ee - 1 >= 0 and lines[ee-1].strip() == "0":
        insert_idx = ee - 1  # Insert before the "0" line
    else:
        # Fallback: find the "0" that precedes ENDSEC
        for i in range(ee-1, es, -1):
            if lines[i].strip() == "0":
                insert_idx = i
                break

    print(f"Inserting {len(clones)} clones at line {insert_idx} (before ENDSEC pair)")

    # Assemble
    new_lines = lines[:insert_idx] + []
    for c in clones:
        new_lines.extend(c)
    new_lines.extend(lines[insert_idx:])

    print(f"Original: {len(original_lines)} lines, Output: {len(new_lines)} lines")

    write_dxf(output_path, new_lines)
    print(f"Wrote: {output_path}")

    with open("/tmp/v3_handle_map.json", "w") as f:
        json.dump(handle_map, f, indent=2)
    print(f"Handle map saved to /tmp/v3_handle_map.json")

    # Verify: check for consecutive 0s in output
    c0_count = 0
    for i in range(len(new_lines)-1):
        if new_lines[i].strip() == "0" and new_lines[i+1].strip() == "0":
            c0_count += 1
            if c0_count <= 3:
                print(f"  WARNING: consecutive 0s at {i}: |{new_lines[i]}| |{new_lines[i+1]}|")
    print(f"Consecutive 0 pairs in output: {c0_count}")

    # Verify: check CRLF was written
    with open(output_path, 'rb') as f:
        data = f.read()
    crlf = data.count(b'\r\n')
    lf_only = data.count(b'\n') - crlf
    print(f"CRLF: {crlf}, LF-only: {lf_only}")

if __name__ == "__main__":
    main()
