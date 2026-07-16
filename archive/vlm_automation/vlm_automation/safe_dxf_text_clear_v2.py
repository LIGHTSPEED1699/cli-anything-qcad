#!/usr/bin/env python3
"""
Safe DXF text-clearing tool v2 — handle-based raw replacement.

Recommended over entity-parsing (v1) because it:
  • Avoids the layer-name "0" / group-code-0 ambiguity trap
  • Skips entity-parsing altogether (no state machine needed)
  • Handles nested {ACAD_REACTORS} blocks correctly
  • Works on any DXF format that uses CRLF line endings

Usage:
    python3 safe_dxf_text_clear_v2.py input.dxf output.dxf [handle1 handle2 ...]

Replacement value: a single dot (minimal visual noise, valid for all parsers).
Do NOT use empty string ("\r\n") — AutoCAD TrueView rejects it.
Do NOT use empty string or whitespace-only — AutoCAD TrueView rejects it.

Pitfall fixes (2026-05-08):
  1. STRICT group-code-5 matching — require handle to appear after b'\r\n  5\r\n'.
     Prevents accidental match inside coordinate values (e.g., 13.83878324078082).
  2. FIRST match only — handles appear again in OBJECTS section as xrefs.
     Modifying OBJECTS xrefs corrupts the DWG.
"""
import sys
from pathlib import Path


def replace_text_after_handle(raw_bytes: bytes, handle: str, replacement: bytes = b'.') -> bytes:
    """Find handle as a group-code-5 value in raw DXF (first match only), replace its text."""
    # Strict: require b'\r\n  5\r\n' prefix so we don't match handle inside coordinates.
    handle_pat = b'\r\n  5\r\n' + handle.upper().encode() + b'\r\n'
    h_pos = raw_bytes.find(handle_pat)
    if h_pos == -1:
        raise ValueError(f"Handle '{handle}' not found as group-code-5 entity handle in DXF")

    search_start = h_pos + len(handle_pat)
    gc1_pos = raw_bytes.find(b'\r\n  1\r\n', search_start)
    if gc1_pos == -1:
        raise ValueError(f"Group code 1 not found after handle '{handle}'")

    val_start = gc1_pos + len(b'\r\n  1\r\n')
    val_end = raw_bytes.find(b'\r\n', val_start)
    if val_end == -1:
        raise ValueError(f"Malformed group code 1 value after handle '{handle}'")

    return raw_bytes[:val_start] + replacement + raw_bytes[val_end:]


def process_file(in_path: Path, out_path: Path, handles: list[str]) -> None:
    with open(in_path, 'rb') as f:
        raw = f.read()

    for h in handles:
        raw = replace_text_after_handle(raw, h)

    with open(out_path, 'wb') as f:
        f.write(raw)

    print(f"Cleared text for {len(handles)} handles → {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 safe_dxf_text_clear_v2.py input.dxf output.dxf [handle1 handle2 ...]")
        sys.exit(1)

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    handles = sys.argv[3:] if len(sys.argv) > 3 else []

    if not handles:
        print("Warning: no handles provided — file copied unchanged")

    process_file(in_path, out_path, handles)
