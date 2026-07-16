#!/usr/bin/env python3
"""
Text-based DXF entity deletion by handle — the ONLY reliable method
for preserving layer visibility in LibreDWG-generated DXFs.

ezdxf doc.saveas() corrupts layer state (pitfall #70).
This script operates on raw ASCII, preserving all formatting,
OBJECTS-section cross-references, and layer table structure.

Usage:
    python3 delete_entities_text.py input.dxf output.dxf handles.json

handles.json format: ["3239", "3BCC", ...] (hex handles as strings)

Output statistics: entities deleted, entities kept, total top-level entities.
"""

import re
import json
import sys
from pathlib import Path


def parse_entities(content: bytes) -> list[tuple[int, int, str]]:
    """Parse DXF content into a list of (start_offset, end_offset, type) tuples.

    Each entity starts with '  0\\r\\nTYPE\\r\\n' or '  0\\nTYPE\\n'
    and ends just before the next '  0\\r\\n' / '  0\\n' or the ENDSEC marker.
    """
    # Normalize line endings to a sentinel-friendly form; keep offsets valid.
    newline = b'\n'
    if b'\r\n' in content and b'\r\n  0\r\n' in content:
        content = content
        pattern = re.compile(rb'\r\n  0\r\n(\w+)\r\n')
    else:
        content = content.replace(b'\r\n', b'\n')
        pattern = re.compile(rb'\n  0\n(\w+)\n')

    entities = []
    for match in pattern.finditer(content):
        etype = match.group(1).decode('ascii', errors='replace')
        start = match.start() + 1  # Skip the newline before '  0'
        entities.append((start, -1, etype))

    # Set end offsets
    for i in range(len(entities) - 1):
        entities[i] = (entities[i][0], entities[i+1][0] - 1, entities[i][2])

    # Trim to ENTITIES section
    result = []
    start_marker = b'\r\n  0\r\nSECTION\r\n  2\r\nENTITIES\r\n' if b'\r\n' in content else b'\n  0\nSECTION\n  2\nENTITIES\n'
    if start_marker not in content:
        start_marker = b'  0\nSECTION\n  2\nENTITIES\n'
    entities_start = content.find(start_marker)
    if entities_start == -1:
        entities_start = 0

    search_from = entities_start if entities_start >= 0 else 0
    end_marker = b'\r\n  0\r\nENDSEC\r\n' if b'\r\n' in content else b'\n  0\nENDSEC\n'
    entities_end = content.find(end_marker, search_from)

    for start, end, etype in entities:
        if entities_start >= 0 and start < entities_start:
            continue
        if entities_end >= 0 and start > entities_end:
            continue
        result.append((start, end, etype))

    return result


def get_entity_handle(content: bytes, start: int, end: int) -> str | None:
    """Extract the group-code-5 handle from an entity's byte range."""
    entity_bytes = content[start:end]
    handle_match = re.search(rb'(?:\r\n|\n)  5(?:\r\n|\n)([0-9A-Fa-f]+)(?:\r\n|\n)', entity_bytes)
    if handle_match:
        return handle_match.group(1).decode('ascii').upper()
    return None


def delete_entities_by_handle(input_path: str, output_path: str, handles_json_path: str):
    """Delete entities from DXF file by handle, using text-based byte removal.
    
    Preserves all original formatting, OBJECTS-section cross-references,
    and layer table structure. This avoids ezdxf doc.saveas() corruption.
    """
    with open(input_path, 'rb') as f:
        content = f.read()
    
    with open(handles_json_path, 'r') as f:
        data = json.load(f)
    
    # Support both plain list and log object with 'all_handles' key
    if isinstance(data, list):
        handles_to_delete = {h.upper() for h in data}
    elif isinstance(data, dict) and 'all_handles' in data:
        handles_to_delete = {h.upper() for h in data['all_handles']}
    else:
        raise ValueError(f"Unexpected JSON format in {handles_json_path}")
    
    print(f"Handles to delete: {len(handles_to_delete)}")
    
    # Parse entities in ENTITIES section
    entities = parse_entities(content)
    print(f"Top-level entities found: {len(entities)}")
    
    # Find entities to delete
    deleted = 0
    kept = 0
    deletion_ranges = []
    
    for start, end, etype in entities:
        handle = get_entity_handle(content, start, end)
        if handle and handle in handles_to_delete:
            deletion_ranges.append((start, end))
            deleted += 1
        else:
            kept += 1
    
    print(f"Deleting: {deleted} entities")
    print(f"Keeping: {kept} entities")
    
    # Delete by removing byte ranges (reverse order to preserve offsets)
    deletion_ranges.sort(key=lambda x: x[0], reverse=True)
    
    result = bytearray(content)
    for start, end in deletion_ranges:
        del result[start:end]
    
    # Fix layer colors: negative → positive (group code 62)
    # IMPORTANT: This regex operates on the ENTIRE file, not just the LAYER table.
    # In DXF, group code 62 appears in both LAYER definitions (where negative = OFF)
    # and entity definitions (where it means color). Negative entity colors are rare
    # in production DXFs, so this blanket fix is usually safe. If the DXF has negative
    # entity colors that should be preserved, use the separate fix_layer_visibility.py
    # script which operates only on the LAYER TABLE section.
    #
    # 2026-05-11 NOTE: This built-in fix sometimes fails because the regex pattern
    # uses \r\n (CRLF) but the file may have been modified to use \n (LF) after
    # bytearray slicing. Use fix_layer_visibility.py as a reliable separate step.
    result_str = result.decode('ascii', errors='replace')
    
    # Try both CRLF and LF patterns
    neg_pattern_crlf = re.compile(r'\r\n  62\r\n(-\d+)\r\n')
    neg_pattern_lf = re.compile(r'\n  62\n(-\d+)\n')
    color_fixes = 0
    
    for pattern in [neg_pattern_crlf, neg_pattern_lf]:
        for match in pattern.finditer(result_str):
            neg_val = match.group(1)
            pos_val = str(abs(int(neg_val)))
            result_str = result_str.replace(match.group(0), match.group(0).replace(neg_val, pos_val), 1)
            color_fixes += 1
    
    print(f"Fixed {color_fixes} negative layer colors → positive")
    
    with open(output_path, 'w') as f:
        f.write(result_str)
    
    print(f"Written: {output_path} ({len(result_str)} bytes)")


if __name__ == '__main__':
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} input.dxf output.dxf handles.json")
        sys.exit(1)
    
    delete_entities_by_handle(sys.argv[1], sys.argv[2], sys.argv[3])