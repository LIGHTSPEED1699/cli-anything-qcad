# Pair 3 Entity Duplication V1 Rejections & V2 Corrective Spec (2026-05-13)

## Context

Pair 3 (`3.dxf`) required cloning PLC21/CA-1451 wiring from terminals 4/5/6 to terminals 7/8/9 as PLC22/CA-1452. V1 (`3_cloned.dxf` → `3_FINAL.dwg`) was produced via Python text-based bulk cloning and delivered to the user. The user rejected it with **5 defects** and requested V2.

## The 5 Rejection Issues

### 1. Wrong clone range — terminals (2)/(3) cloned instead of only 4/5/6
- **V1 source filter:** `y ∈ [19.0, 20.7]` (bounding box)
- **Result:** Terminal (2) at y=20.68 and terminal (3) at y=20.18 were included. Terminal 4 is at y=19.93.
- **Fix for V2:** Cap source at `y ∈ [19.0, 19.95]`. Verify terminal row labels: (4) y=19.93, (5) y=19.43, (6) y=19.18.

### 2. Missing CA-1452 cable-tag circle
- **Entity:** LWPOLYLINE handle `9A79` — closed circle around `CA-1451` text at (20.53, 20.24)
- **Why missed:** The V1 bounding-box filter did not capture it, or x/y filter excluded it. It's geometry on top of the cable tag.
- **Fix for V2:** Explicitly include handle `9A79` in the source set regardless of position filter (whitelist approach).

### 3. Deleted REV 00 and REV 01 history rows
- **Entity location:** ATTRIB tags inside BLOCK definitions in the `BLOCKS` section (`REV_1`, `REV_DATE_1`, `REV_DESCR_1`, `REV_DRAW_1`, etc.)
- **Why deleted:** V1 script inserted cloned entity blocks before the `ENTITIES` `ENDSEC`, but raw byte insertion at line 39984 corrupted/truncated non-modelspace data. Output file shrank from **730,751 → 675,081 bytes** (55 KB data loss).
- **Fix for V2:** Ensure the output file is **larger than the input**. Implement safe insertion that preserves all sections after `ENTITIES` (`OBJECTS`, `THUMBNAILIMAGE`, etc.).

### 4. Missing REV 01A row (new revision to be added)
- **Request:** User wants a NEW revision row added to title block (REV 01A).
- **Why not done:** V1 was a pure clone with no title-block editing logic.
- **Fix for V2:** After clone, modify the BLOCK-section ATTRIB for the next revision index (e.g., `REV_2`, `REV_DATE_2`, etc.) to add "01A" data.

### 5. Deleted bottom-right "01" revision number — should be "02"
- **Entity:** Bottom-right corner revision number. User said change from "01" to "02".
- **Why deleted:** Same as Issue 3 — non-modelspace data loss.
- **Fix for V2:** Preserve all title-block ATTRIBs and edit the specific field.

---

## Root Cause: 55 KB Data Loss in Text-Based Clone

### Symptom
`3_cloned.dxf` (675,081 bytes) < `3.dxf` (730,751 bytes) despite adding 51 entity blocks to `ENTITIES`.

### Diagnosis
- Modelspace entity count increased from 227 → 278 (+51), confirming the clones were inserted.
- File size shrink indicates **post-insertion truncation** of later sections (`OBJECTS`, `THUMBNAILIMAGE`, `BLOCKS` tail).
- The insertion point was identified as: line 39984 (`0` before `ENDSEC` at line 39985).
- **Why truncation happened:** The insertion logic likely overwrote or re-sliced the raw bytes array incorrectly, losing everything after the insertion point rather than expanding the file.
- **Also:** Duplicate handles (`0x9B3E`–`0x9B70`) were generated because `max_num+1` was computed from modelspace handles only, colliding with existing `OBJECTS`/`BLOCKS` handles.

### V2 Safeguard
```python
assert len(output_bytes) > len(input_bytes), \
    f"Data loss detected: {len(output_bytes)} < {len(input_bytes)}"
```

---

## V2 Corrective Spec

### Clone Script Requirements
1. **Source selection:**
   - Bounding box: `x ∈ [13.5, 21.5], y ∈ [19.0, 19.95]`
   - Whitelist: always include LWPOLYLINE handle `9A79` (CA-1451 cable-tag circle)
   - Blacklist: always exclude label TEXTs matching `^\(\d+\)$` (row labels) unless explicitly kept for destination alignment

2. **Handle uniqueness:**
   - Find the maximum handle across **all sections** (ENTITIES, BLOCKS, OBJECTS) and start new handles at `max + 2`.
   - Alternatively, strip all group-code-5 values from cloned blocks and rely on QCAD ODA reassignment.
   - **Post-clone validation:** Run regex duplicate-handle check across entire file.

3. **Safe insertion:**
   - Find insertion point **before** last `0`/`ENDSEC` pair in ENTITIES.
   - **Expand** the file: `new_raw = raw[:insert_at] + b''.join(cloned_blocks) + raw[insert_at:]`
   - Do NOT overwrite or `.replace` — must be pure concatenation.
   - Verify `ENTITIES` section still ends with proper `  0\r\nENDSEC\r\n` and `EOF` follows later.

4. **Text replacements (destination):**
   - PLC21 → PLC22
   - CA-1451 → CA-1452
   - 02732 → 02733
   - Wire 14 → 16, 15 → 17, 13 → 15
   - Apply via `bytes.replace()` on each cloned block.

5. **Wire-label deduplication at destination:**
   - Original SPARE texts exist at terminals 7/8/9 (y≈17.5–18.7).
   - After cloning, destination region has BOTH original SPARE labels AND cloned wire labels.
   - User must visually inspect, OR script should identify overlapping TEXTs by proximity (<0.1 units) and delete the original SPARE handle, keeping the cloned one.

6. **Title-block preservation:**
   - Do not modify or delete any `BLOCKS` section ATTRIBs.
   - Clone operation is **additive** only (insert into ENTITIES).

### Post-Clone Title-Block Edit (Separate Step)
1. Locate BLOCK section(s) with ATTRIB tags (`REV_*`, `REV_DATE_*`, etc.).
2. Find the highest populated revision index (e.g., `_1` = REV 01, `_2` empty).
3. Set `_2` fields: `REV_2` = `01A`, `REV_DATE_2` = current date, `REV_DESCR_2` = description.
4. Edit bottom-right revision number ATTRIB from "01" → "02".
5. Use text-based search-replace within the BLOCKs section.

---

## User Preference Signal (Boundary-Touching)

User stated during Pair 1 V12: *"When the object is just touching the clouds, it is not meant for deletion as the objects sit on the boundary."*

This is already Pitfall #94 (`v10-v12-iteration-log.md`).

---

## Verification Checklist for V2

- [ ] Output file size ≥ input file size (+ entity block sizes)
- [ ] Modelspace count = 227 + N (N = cloned entities, expected ~25–35 for terminals 4/5/6 only)
- [ ] Duplicate handle count = 0 across entire file
- [ ] CA-1451 cable tag (handle 9A79) present in source, CA-1452 in destination
- [ ] No `(2)` or `(3)` row labels cloned into 7/8/9
- [ ] REV 01A row appears in title block
- [ ] Bottom-right revision number = "02"
- [ ] Original REV 00/01 rows undamaged
- [ ] `dxf2dwg` or QCAD ODA conversion succeeds without "Object handle not found" errors
