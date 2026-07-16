# Text-Based DXF Editing Patterns (2026-05-12)

Reusable patterns for LibreDWG DXF editing when ezdxf `saveas()`/`write()` crashes and QCAD ECMAScript is unreliable. All operations edit the raw DXF bytes directly.

## Why Text-Based?

| Tool | Can Read? | Can Write? | Reliable? |
|------|-----------|------------|-----------|
| ezdxf | Yes | **No** — `saveas()` crashes on material table (pitfall #61) | For reading/indexing only |
| QCAD ECMAScript | Partial | **Unreliable** — `getVertices()`, `addVertex()` not functions | Proven APIs only: `RAddObjectsOperation`, layer freeze/off |
| Raw text editing | N/A | **Best** for replace/rename/shrink | Must follow anchored patterns (pitfall #96) |

## Pattern 1: Text Content Rename

**Use case:** "TB-19 → TB-21", "RELAY 15 → RELAY 16"

```python
# SINGLE-OCCURRENCE rename — safe and simple
raw = raw.replace(b'TB-19', b'TB-21', 1)

# Verify
assert b'TB-19' not in raw
assert b'TB-21' in raw
```

**Requirements:** The text appears EXACTLY once. If multiple matches, narrow by entity handle first.

## Pattern 2: Coordinate Value Replacement (Anchored)

**Use case:** "shrink RELAY 15 box right edge by 1.0 DXF unit"

```python
import re

# 1. Find the entity by handle
hpos = raw.find(b'\r\n  5\r\n4396\r\n', es, ee)  # handle 4396 in entities section

# 2. Get entity boundaries
bstart = raw.rfind(b'\r\n  0\r\nLWPOLYLINE\r\n', es, hpos)  # entity start marker
bend = raw.find(b'\r\n  0\r\n', hpos + 10, ee)  # next entity start

block = raw[bstart:bend]

# 3. Anchored pattern — group code + value + CRLF
old = b'\r\n 20\r\n7.90625\r\n'   # group-code-20 (y-coordinate)
new = b'\r\n 20\r\n6.90625\r\n'

if block.count(old) >= 2:  # verify expected count
    block = block.replace(old, new, 2)
    assert len(block) == (bend - bstart)  # size MUST NOT change
    raw[bstart:bend] = block
```

**CRITICAL RULES:**
- Always include `\r\n GC\r\n` prefix — never match bare numbers
- Always replace within the entity block, not globally
- Always assert block size didn't change
- Always verify the CORRECT AXIS with user (x vs y, width vs height)
- Beware: `3.53125` matches inside `13.53125` if not properly anchored

## Pattern 3: Add New TEXT Entity (Handle-Stripped Clone)

**Use case:** "add 'BLK' text near RELAY 15"

```python
# 1. Find source entity to clone for style
for m in re.finditer(rb'\r\n  0\r\nTEXT\r\n', raw[es:ee]):
    tstart = es + m.start()
    tend = raw.find(b'\r\n  0\r\n', tstart + 10, ee)
    block = raw[tstart:tend]

    if b'RELAY 15' in block:
        # 2. Clone and modify
        new_block = block
        new_block = new_block.replace(b'RELAY 15', b'BLK')
        new_block = re.sub(rb'(\r\n 10\r\n)[\d.]+(\r\n)', rb'\g<1>2.60\g<2>', new_block, count=1)
        new_block = re.sub(rb'(\r\n 20\r\n)[\d.]+(\r\n)', rb'\g<1>8.30\g<2>', new_block, count=1)
        
        # 3. STRIP handle — QCAD ODA reassigns on import
        new_block = re.sub(rb'\r\n  5\r\n[0-9A-Fa-f]+\r\n', b'', new_block)
        
        # 4. Strip alignment point (may conflict)
        new_block = re.sub(rb'\r\n 11\r\n[\d.]+\r\n', b'', new_block)
        new_block = re.sub(rb'\r\n 21\r\n[\d.]+\r\n', b'', new_block)

        # 5. Insert BEFORE ENDSEC (safe insertion point)
        insert_at = ee  # ENDSEC start
        raw[insert_at:insert_at] = new_block
        break
```

**CRITICAL RULES:**
- **Never insert between entity boundaries** — concatenates type names (TEXT330)
- **Always strip group code 5** (handle) — QCAD ODA import reassigns
- **Only insert before ENDSEC** — `\r\n  0\r\nENDSEC\r\n`
- **Verify no corruption markers** after: `b'TEXT330'`, `b'LINE330'`, etc.

## Verification Checklist

After any text-based edit, run these assertions:

```python
# 1. No corruption markers
for marker in [b'TEXT330', b'LINE330', b'LWPOLYLINE330', b'INSERT330', b'ATTRIB330']:
    assert marker not in raw, f"CORRUPTED: {marker.decode()}"

# 2. Expected value present/absent
assert b'TB-21' in raw, "Rename missing"
assert b'TB-19' not in raw, "Old text still present"

# 3. Layer colors fixed
neg_colors = re.findall(rb'\r\n 62\r\n(-\d+)\r\n', raw)
assert len(neg_colors) == 0, f"Layer colors still negative: {neg_colors}"
```

## QCAD Export (Final Step)

ALWAYS use the proven `qcad_convert_v9_simple.js` — NEVER run custom ECMAScript:

```bash
python3 fix_layer_visibility.py edited.dxf edited_fixed.dxf

qcad-bin -platform offscreen -autostart /tmp/qcad_convert_v9_simple.js \
    edited_fixed.dxf output.dwg
```

Verify via LibreDWG roundtrip:

```bash
dwg2dxf output.dwg -o output_rt.dxf
# Check output_rt.dxf for BLK, TB-21, shrunk coordinates, no corruption markers
```
