# Pair 2 Pipeline Results (2026-05-12)

## Drawing Pair

- **Source:** `2.dwg` (converted to `/tmp/pair2/2.dxf` via LibreDWG `dwg2dxf`)
- **PDF:** `2.pdf` (page 1224×792, 1 cloud annotation + 3 FreeText instructions)

## Annotations

| Type | Content | Action |
|------|---------|--------|
| Cloud (1) | Polygon at x=[2.14,4.67] y=[3.51,5.03] DXF | Delete circled objects |
| FreeText | "add 'BLK'" | Add text entity |
| FreeText | "Change to TB-21" | Rename TB-19 → TB-21 |
| FreeText | "remove circled objects; then, make the RELAY 15 box smaller" | Delete + resize |

## Pipeline Run

### Phase 1-4: Extraction + Matching
- 1 cloud detected: C0 (LEFT-BOTTOM), height=1.51
- 85 entities indexed (LINE:32, TEXT:26, INSERT:24, LWPOLYLINE:3)
- 17 deletion candidates, 0 boundary-touching
- **100% automated match** (all T1 strict PIP, no overrides needed)

### Phase 5: Deletion
- `delete_entities_text.py` removed 17 entities
- **Deleted:** 6 INSERT blocks (relay components), 7 LINEs (connecting wires), 4 TEXT labels (21, 22, 24, 24V)
- **171 entities remaining**

### Phase 6: Layer Fix
- 15 negative layer colors fixed → positive

### Phase 7: DWG Export
- QCAD Pro ODA R32 (2018) export: 43.8 KB

## Non-Deletion Instructions (separate from pipeline)

User decided the pipeline should handle deletion only (same pattern as Pair 1). Remaining instructions processed manually:

| Instruction | Method | Result |
|------------|--------|--------|
| TB-19 → TB-21 | Raw byte search-replace in DXF | ✓ |
| RELAY 15 box height shrink | Targeted pattern `\r\n 20\r\n7.90625\r\n` → `6.90625` in LWPOLYLINE 4396 block only | ✓ |
| Add "BLK" text | Clone RELAY 15 TEXT entity, strip handle, insert before ENDSEC | ✓ |

## Key Pitfalls Discovered

1. **Anchored coordinate replacement (Pitfall #96):** Naive string replacement of `3.53125` also matched `13.53125` in a different LWPOLYLINE. Must use full group-code patterns: `b'\r\n 10\r\nVALUE\r\n'`.

2. **Axis correction:** Box resize was applied to width (group code 10) instead of height (group code 20). User must confirm which axis.

3. **ENDEC-only insertion (Pitfall #98):** Inserting between entity boundaries concatenates type names (TEXT + 330 = TEXT330). Only append before ENDSEC marker.

4. **OOM from single process (Pitfall #97):** Full pipeline in one invocation SIGKILLs. Split Phases 1-4 and 5-7.

## Entity Counts

| Stage | Entities | Notes |
|-------|----------|-------|
| Original DXF | 188 | Before any processing |
| After deletion | 171 | 17 removed by pipeline |
| After all edits | 173 | +BLK TEXT, +1 entity from QCAD reassign |
