#!/usr/bin/env python3
"""
Verify DXF handle-based text replacement integrity.

Checks that every target handle appears exactly once as a group-code-5 entity handle
and that its next group-code-1 value is the expected replacement.

Usage:
    python3 verify_dxf_handle_integrity.py \
        --dxf 1_MODIFIED.dxf \
        --log 1_deletion_log.json \
        [--expected '.']

Exits 0 if all handles verified, 1 if any mismatch.

This is especially critical when LibreDWG DXFs are edited by raw-byte replacement,
because handles appear as substrings inside coordinates and in OBJECTS-section xrefs.
A naive search corrupts the wrong entities.
"""
import argparse
import json
import sys
from pathlib import Path


def verify(dxf_path: Path, log_path: Path, expected: str = '.'):
    with open(log_path) as f:
        log = json.load(f)

    targets = [d['handle'] for d in log.get('cleared', log.get('cleared_entities', []))]

    with open(dxf_path, 'rb') as f:
        raw = f.read()

    failures = 0
    expected_bytes = expected.encode()

    for h in targets:
        # Strict: only match group-code-5 entity handles (b'\r\n  5\r\nHANDLE\r\n')
        handle_pat = b'\r\n  5\r\n' + h.encode() + b'\r\n'
        pos = raw.find(handle_pat)

        if pos == -1:
            print(f"FAIL {h}: not found as group-code-5 entity handle")
            failures += 1
            continue

        # Find next code-1 after this handle
        search_start = pos + len(handle_pat)
        gc1_pos = raw.find(b'\r\n  1\r\n', search_start)
        if gc1_pos == -1:
            print(f"FAIL {h}: no group code 1 found after handle")
            failures += 1
            continue

        val_start = gc1_pos + len(b'\r\n  1\r\n')
        val_end = raw.find(b'\r\n', val_start)
        val = raw[val_start:val_end]

        if val != expected_bytes:
            print(f"FAIL {h}: value='{val.decode()}' (expected '{expected}')")
            failures += 1
        else:
            # Only verbose on pass if requested; default silent for clean output
            pass

    total = len(targets)
    if failures == 0:
        print(f"PASS: All {total} handles verified with value='{expected}'")
        return 0
    else:
        print(f"FAIL: {failures}/{total} handles incorrect")
        return 1


def verify_all_handles_unique(dxf_path: Path, log_path: Path):
    """Extra check: ensure no handle appears more than once as a group-code-5 handle."""
    with open(log_path) as f:
        log = json.load(f)
    targets = [d['handle'] for d in log.get('cleared', log.get('cleared_entities', []))]

    with open(dxf_path, 'rb') as f:
        raw = f.read()

    multi = 0
    for h in targets:
        handle_pat = b'\r\n  5\r\n' + h.encode() + b'\r\n'
        count = raw.count(handle_pat)
        if count != 1:
            print(f"WARN {h}: appears {count} times as group-code-5 (expected 1)")
            multi += 1

    if multi == 0:
        print("PASS: All handles appear exactly once as group-code-5")
    else:
        print(f"WARN: {multi} handles have non-unique group-code-5 occurrences")
    return multi


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--expected", default=".")
    parser.add_argument("--unique-check", action="store_true", help="Also verify handles appear exactly once as group-code-5")
    args = parser.parse_args()

    rc = verify(args.dxf, args.log, args.expected)
    if args.unique_check:
        rc = max(rc, verify_all_handles_unique(args.dxf, args.log))
    sys.exit(rc)
