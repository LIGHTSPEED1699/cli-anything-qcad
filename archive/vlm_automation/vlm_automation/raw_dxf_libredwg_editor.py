#!/usr/bin/env python3
"""
Raw-text DXF editor for LibreDWG-generated DXFs.
Performs ATTRIB text updates and entity invisibility flagging (group code 60=1).
Validated on Pair 4 (2026-06-12): all edits survive dxf2dwg -> dwg2dxf round-trip.

Usage:
    python3 raw_dxf_editor.py input.dxf output.dxf

Configure by editing the ATTRIB_EDITS and INVISIBLE_HANDLES dicts below.
"""

import sys
import os

# --- Configuration: edit these for your drawing ---
ATTRIB_EDITS = {
    '10A0': '4',           # REVNO
    '10B6': '4',           # REV4
    '10B7': 'P302D REMOVAL',  # REVDESCR
    '10B8': 'HL',          # BY
    '10B9': '2026-06-10',  # DATE
    '10BA': 'HL',          # CHKD
    '10BB': 'HL',          # APP
}

INVISIBLE_HANDLES = {
    'AEF5B', 'AEF86', 'AEFA2', 'AEFAC', 'AEFB2', 'AEFB3',
    'AEFE1', 'AEFE2', 'AEFF8', 'AF43E',  # cloud HATCHes + green circle
    'AF43F', 'AF440',                      # edge LINEs
    'AF8AC', 'AF8C5', 'AF8C8',             # "0v" TEXTs
}
# --------------------------------------------------


def edit_dxf(src_path: str, dst_path: str) -> dict:
    """
    Edit a raw DXF file: update ATTRIB texts and mark entities invisible.

    Returns a dict with counts and status.
    """
    with open(src_path, 'r') as f:
        lines = f.readlines()

    total_attrib = 0
    total_invisible = 0

    for i in range(len(lines) - 1):
        # Entity start detection: MUST use startswith('  0'), NOT strip() == '0'
        # strip() == '0' matches data values like layer name "0", causing
        # premature entity-boundary detection and missing coordinates.
        if not lines[i].startswith('  0'):
            continue

        entity_type = lines[i + 1].strip() if i + 1 < len(lines) else None
        if entity_type not in ('ATTRIB', 'HATCH', 'LINE', 'TEXT'):
            continue

        # Find handle within this entity boundary
        j = i + 2
        handle = None
        entity_end = len(lines)
        while j < len(lines) - 1 and j < i + 30:
            if lines[j].startswith('  0'):
                entity_end = j
                break
            if lines[j].strip() == '5' and handle is None:
                handle = lines[j + 1].strip().upper()
            j += 1

        if handle is None:
            continue

        # --- ATTRIB text edit: find group code 1 within this entity ---
        if handle in ATTRIB_EDITS and entity_type == 'ATTRIB':
            k = i + 2
            while k < entity_end - 1:
                if lines[k].strip() == '1':
                    lines[k + 1] = ATTRIB_EDITS[handle] + '\n'
                    total_attrib += 1
                    break
                k += 1

        # --- Invisibility flag: insert AFTER group code 8 (layer) ---
        # and BEFORE the next 100 subclass marker (e.g., AcDbHatch).
        # This places the visibility flag in the AcDbEntity subclass.
        if handle in INVISIBLE_HANDLES:
            insert_pos = None
            k = i + 2
            while k < entity_end - 1:
                if lines[k].strip() == '8':
                    insert_pos = k + 2  # after layer value line
                if lines[k].strip() == '100' and insert_pos is not None:
                    insert_pos = k  # before subclass marker
                    break
                k += 1

            if insert_pos is not None:
                lines.insert(insert_pos, ' 60\n')
                lines.insert(insert_pos + 1, '     1\n')
                total_invisible += 1

    with open(dst_path, 'w') as f:
        f.writelines(lines)

    return {
        'attrib_edits': total_attrib,
        'invisible_entities': total_invisible,
        'output_size': os.path.getsize(dst_path),
    }


def verify_dxf(path: str) -> dict:
    """
    Verify that ATTRIB edits and invisible flags survived in a DXF.
    Only scans entity sections (not table entries) to avoid false negatives.
    """
    with open(path, 'r') as f:
        lines = f.readlines()

    attrib_ok = {}
    invisible_ok = {}

    for i in range(len(lines) - 1):
        if not lines[i].startswith('  0'):
            continue
        entity_type = lines[i + 1].strip()

        # ATTRIB verification
        if entity_type == 'ATTRIB':
            j = i + 2
            handle = None
            text_val = None
            while j < len(lines) - 1:
                if lines[j].startswith('  0'):
                    break
                if lines[j].strip() == '5':
                    handle = lines[j + 1].strip()
                if lines[j].strip() == '1':
                    text_val = lines[j + 1].strip()
                    break
                j += 1
            if handle in ATTRIB_EDITS:
                attrib_ok[handle] = (text_val == ATTRIB_EDITS[handle])

        # Invisibility verification (HATCH, LINE, TEXT)
        if entity_type in ('HATCH', 'LINE', 'TEXT'):
            j = i + 2
            handle = None
            has_60 = False
            while j < len(lines) - 1:
                if lines[j].startswith('  0'):
                    break
                if lines[j].strip() == '5':
                    handle = lines[j + 1].strip()
                if lines[j].strip() == '60' and lines[j + 1].strip() == '1':
                    has_60 = True
                j += 1
            if handle in INVISIBLE_HANDLES:
                invisible_ok[handle] = has_60

    return {
        'attrib': attrib_ok,
        'invisible': invisible_ok,
        'all_attrib_ok': all(attrib_ok.get(h, False) for h in ATTRIB_EDITS),
        'all_invisible_ok': all(invisible_ok.get(h, False) for h in INVISIBLE_HANDLES),
    }


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} input.dxf output.dxf")
        sys.exit(1)

    src = sys.argv[1]
    dst = sys.argv[2]

    print(f"Editing {src} -> {dst}")
    result = edit_dxf(src, dst)
    print(f"  ATTRIB edits: {result['attrib_edits']}")
    print(f"  Invisible entities: {result['invisible_entities']}")
    print(f"  Output size: {result['output_size']} bytes")

    print(f"\nVerifying {dst}")
    v = verify_dxf(dst)
    print(f"  ATTRIB verification:")
    for h, ok in v['attrib'].items():
        print(f"    {h}: {'OK' if ok else 'FAIL'}")
    print(f"  Invisible verification:")
    for h, ok in v['invisible'].items():
        print(f"    {h}: {'OK' if ok else 'FAIL'}")
    print(f"\nAll ATTRIBs OK: {v['all_attrib_ok']}")
    print(f"All invisible OK: {v['all_invisible_ok']}")
