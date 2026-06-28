#!/usr/bin/env python3
"""V4 Terminal Wire Clone Script — Clone T4/T5/T6 wires to T7/T8/T9 positions.

Usage: python3 clone_pair3_v4.py <input.dxf> <output.dxf>

Hardcoded for Pair 3 geometry. Clones 39 wire entities from T4-T6 region
(y ~20.078 to 20.922) down by dy = -1.25 to T7-T9 region.
Also clones PLC21 (FUTURE) text and shifts cable tag right by dx = +1.0.

Key fixes applied:
- Insert at ee-1 (before ENDSEC), not ee
- dy applied to 20/21 only, never 30/31
- SAFE_BASE = 0x9800 < original max 0x9B3D
- CRLF enforced throughout
"""
import os, re, json

# Handle map (V4: 40 clones)
CLONE_HANDLES = [
    "9847", "9646", "9853", "9644", "9648", "9647", "9643", "964D",
    "9A78", "9974", "9975", "9A77", "9846", "9978", "997B", "9970",
    "963B", "9852", "9639", "9972", "963D", "997A", "9979", "9867",
    "998A", "963C", "9971", "9638", "9868", "9885", "9886", "9866",
    "9A81", "9A80", "97A4", "9A79", "9877", "9983", "9A76", "998B"
]

SAFE_BASE = 0x9800

def clone_dxf(input_path, output_path, dy=-1.25, dx_cable=1.0):
    with open(input_path, 'rb') as f:
        data = f.read()
    lines = data.split(b'\r\n')
    text_lines = [l.decode('latin-1', errors='replace') for l in lines]

    # Find ENTITIES section
    ss = text_lines.index('ENTITIES')
    ee = text_lines.index('ENDSEC', ss)

    # Collect all existing handles
    all_handles = []
    for i, line in enumerate(text_lines):
        if line == '  5':
            try:
                h = int(text_lines[i+1].strip(), 16)
                all_handles.append(h)
            except:
                pass

    max_existing = max(all_handles) if all_handles else 0
    print(f"Max existing handle: 0x{max_existing:04X}")

    # Generate new handles
    counter = SAFE_BASE
    handle_map = {}
    for h in CLONE_HANDLES:
        while counter in all_handles:
            counter += 1
        handle_map[h] = counter
        all_handles.append(counter)
        counter += 1

    # Save handle map
    with open('/tmp/v4_handle_map.json', 'w') as f:
        json.dump(handle_map, f, indent=2)

    # Find entities to clone
    entities = {}
    for h in CLONE_HANDLES:
        pattern = re.compile(rf'^\s*{h}\s*$')
        for i in range(ss, ee):
            if pattern.match(text_lines[i]):
                start = i
                while start > ss and text_lines[start] != '  0':
                    start -= 1
                end = i + 1
                while end < ee and text_lines[end] != '  0':
                    end += 1
                entities[h] = (start, end)
                break

    print(f"Found {len(entities)} entities to clone")

    # Build clones with offset and handle replacement
    clones = []
    for h in CLONE_HANDLES:
        if h not in entities:
            print(f"  WARNING: Handle {h} not found!")
            continue
        start, end = entities[h]
        new_handle = f"{handle_map[h]:04X}"

        clone_lines = []
        i = start
        while i < end:
            line = text_lines[i]

            if line == '  5':
                clone_lines.append(line)
                clone_lines.append(new_handle)
                i += 2
                continue

            if line == '330':
                clone_lines.append(line)
                clone_lines.append('2')
                i += 2
                continue

            if line in ('360', '102'):
                clone_lines.append(line)
                i += 1
                if i < end:
                    clone_lines.append(text_lines[i])
                    i += 1
                continue

            # Apply dy offset to Y coordinates
            if line in (' 20', ' 21'):
                clone_lines.append(line)
                i += 1
                if i < end:
                    try:
                        old_y = float(text_lines[i])
                        new_y = old_y + dy
                        clone_lines.append(f"{new_y:.10g}")
                    except:
                        clone_lines.append(text_lines[i])
                    i += 1
                continue

            clone_lines.append(line)
            i += 1

        clones.append(clone_lines)

    # Insert before ENDSEC
    insert_pos = ee - 1
    for clone_lines in clones:
        for line in reversed(clone_lines):
            lines.insert(insert_pos, line.encode('latin-1'))

    # Rebuild text_lines
    text_lines = [l.decode('latin-1', errors='replace') for l in lines]

    # Apply dx shift to cable tag clones
    CABLE_SOURCES = ["9A81", "9971", "998A", "9A80"]
    cable_handles = {f"{handle_map[h]:04X}" for h in CABLE_SOURCES if h in handle_map}

    for i, line in enumerate(text_lines):
        if line == '  5' and i+1 < len(text_lines):
            h = text_lines[i+1].strip()
            if h in cable_handles:
                j = i + 2
                entity_end = j
                while entity_end < len(text_lines) and text_lines[entity_end] != '  0':
                    entity_end += 1
                while j < entity_end:
                    if text_lines[j] in (' 10', ' 11', ' 12', ' 13'):
                        if j+1 < entity_end:
                            try:
                                old_x = float(text_lines[j+1])
                                text_lines[j+1] = f"{old_x + dx_cable:.10g}"
                            except:
                                pass
                    j += 1

    # Replace CA-1451 -> CA-1452
    for i, line in enumerate(text_lines):
        if line.strip() == 'CA-1451':
            j = i
            while j > 0 and text_lines[j] != '  5':
                j -= 1
            if j > 0 and j+1 < len(text_lines):
                h = text_lines[j+1].strip()
                if h in cable_handles:
                    text_lines[i] = 'CA-1452'

    # Write output with CRLF
    output = []
    for line in text_lines:
        line = line.rstrip('\r\n')
        output.append(line.encode('latin-1') + b'\r\n')

    with open(output_path, 'wb') as f:
        f.writelines(output)

    print(f"Done: {output_path} ({len(output)} bytes)")

if __name__ == '__main__':
    import sys
    if len(sys.argv) >= 3:
        clone_dxf(sys.argv[1], sys.argv[2])
    else:
        print("Usage: python3 clone_pair3_v4.py <input.dxf> <output.dxf>")
