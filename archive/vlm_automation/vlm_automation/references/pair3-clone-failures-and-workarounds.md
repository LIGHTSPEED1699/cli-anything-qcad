# Pair 3 Clone Failures & Workarounds (2026-05-14)

## Goal
Clone T4/T5/T6 wire elements (terminals, cables, PLCs) to T7/T8/T9 in drawing `3.dwg`.

## What Failed

### 1. Text-Based Clone Script (`clone_v2_final.py`)
- **Approach**: Read DXF as text, clone entities by copying group-code blocks, reassign handles starting at `0x9C00`, apply dy=-1.25 offset, text replacements `(4)→(7)`, etc.
- **Result**: Generated DXF (65K lines, 738KB) appeared structurally valid but **TrueView 2020 hung indefinitely** trying to open it.
- **Root causes**:
  - **Empty text strings**: 121 instances of group code `1` followed by empty line → TrueView chokes on empty string values (group code 1 = text content cannot be zero-length in R15)
  - **Binary data blocks**: 596 group-code-310 blocks (XRECORD binary data) from handle collision corruption — these are invisible to text parsers but break strict DXF readers
  - **Handle range collision**: Cloned handles at `B000+` (45056+) fell outside QCAD ODA's 16-bit handle space; QCAD silently dropped entities during any intermediate processing
  - **Broken cross-references**: INSERT entities reference BLOCK handles that weren't cloned; ATTRIB sub-entities point to parent INSERT handles that changed; LWPOLYLINE vertex sequences broken

### 2. QCAD ODA DXF→DWG Export
- **Approach**: Fix layer colors → convert DXF→DWG via QCAD headless ECMAScript
- **Result**: All cloned LINE/ARC/INSERT/TEXT entities silently dropped; only simple LWPOLYLINE cable tag survived
- **Root cause**: QCAD's ODA importer reassigns handles not in original file's space, often picking values that collide with existing entities → originals overwritten, clones lost

### 3. CRLF Fix Alone Was Insufficient
- Converting LF→CRLF fixed line endings but not the underlying structural corruption.
- TrueView still hung because empty text strings and broken entity references remained.

## What Worked (Partially)

| Fix | Applied To | Result |
|-----|-----------|--------|
| Empty text → single space | `3_cloned_v2_clean.dxf` | TrueView still hung (other corruption) |
| Remove binary data blocks | `3_cloned_v2_clean2.dxf` | TrueView still hung (handle cross-reference breakage) |
| CRLF line endings | All variants | Necessary but not sufficient |

## Correct Approach (Determined Post-Mortem)

Use a **proper DXF library** (ezdxf, dxfgrabber) for any operation involving:
- Entity cloning with handle management
- INSERT/ATTRIB/BLOCK cross-reference preservation
- LWPOLYLINE vertex sequence cloning
- HATCH boundary path cloning

Text-based editing is ONLY safe for:
- Simple entity deletion by handle (group code 5)
- Text content replacement (group code 1, 3, 4) on non-cloned entities
- Layer color fixes (group code 62)
- Coordinate math on known entity types

## Key Pitfall

**#100: Text-based DXF cloning is unsafe for complex drawings.**
Any drawing with INSERT/ATTRIB blocks, LWPOLYLINEs with multiple vertices, or HATCH entities requires a library that understands DXF object graphs. Text copy-paste breaks handle references, parent-child links, and sequential data groupings.

## Files Generated
- `3_cloned_v2.dxf` — original clone output (corrupted)
- `3_cloned_v2_fixed.dxf` — CRLF only (still broken)
- `3_cloned_v2_clean.dxf` — empty text + binary removed (still hung)
- `3_cloned_v2_clean2.dxf` — all empty values fixed (still hung)
- `3.dxf` — source file (730KB, valid, opens in TrueView)

## Lesson
When a DXF file is produced by a clone script and a CAD viewer hangs, the problem is almost always **structural corruption** (broken references, empty required values, handle collisions) — not just line endings. Use `hermes dxf_diagnose <file>` or a library-based validator before attempting viewer open.
