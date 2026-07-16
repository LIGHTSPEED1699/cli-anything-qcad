# Pair 3 V8 Rebuild — Correct Handle Discovery (2026-05-16)

## Critical Discovery: V7 Cloned Wrong Handles

The V7 clone script used a **hardcoded handle list** that worked for a previous DXF version (`3.dxf` from May 7) but was **completely wrong** for `3_clean.dxf` (regenerated clean DXF). The handles pointed to **BLOCK DEFINITION** entities in the BLOCKS section (y≈10.0), not **instantiated wire geometry** in the ENTITIES section.

### Why V7 Looked "Good" to VLM
The VLM declared V7 correct because:
1. Terminal labels (7), (8), (9) were present and not duplicated
2. Wire routing appeared to exist at T7-T9 positions
3. The ezdxf renderer showed coherent wires

But TrueView showed five errors because the cloned wires were **BLOCK DEFINITION geometries** that happened to render visually but were in wrong positions (8-10 instead of 7-9), with wrong source mapping.

## Correct Approach: Dynamic Handle Discovery

### Step 1: Identify Terminal Labels
Use TEXT entities with terminal number format `(N)` to find each row's y-position:
```
(3): y=20.179  (4): y=19.929  (5): y=19.429  (6): y=19.179
(7): y=18.679  (8): y=18.429  (9): y=17.929
```

### Step 2: Discover Wire Geometry per Row
For each terminal row, find all non-INSERT entities within ±0.15 of the label y-position. This produces the **actual instantiated geometry** for that row:

**T4 (y=19.929) actual wire geometry:**
- `9886` LINE (horizontal, y=20.000)
- `997C` LINE (horizontal, y=19.906)
- `9972` TEXT '(W)' at y=19.929

**T5 (y=19.429) actual wire geometry:**
- `9647` LINE (horizontal, y=19.375)
- `9A78` TEXT '(RED & BLUE)' at y=19.547
- `9974` LINE (horizontal, y=19.021) — NOTE: extends far down!

**T6 (y=19.179) actual wire geometry:**
- `9644` TEXT '(6)' at y=19.179
- `9853` TEXT 'EPAC G1 15 N' at y=19.172

### Step 3: Handle Overlapping Entities
Some LINE entities span multiple terminal rows (e.g., `9974` has y_range=[17.812, 19.625], covering T5, T6, T7). These are **vertical or diagonal connection lines** shared across terminals.

For cloning, assign shared lines to the **nearest source terminal** by comparing the line's centroid y to each terminal's y-position.

### Step 4: Clone with Correct Offsets
Use per-terminal dy offsets computed from actual positions:
```
T4 → T7: dy = 18.679 - 19.929 = -1.250
T5 → T8: dy = 18.429 - 19.429 = -1.000
T6 → T9: dy = 17.929 - 19.179 = -1.250
```

## Key Pitfall: Hardcoded Handles Across DXF Versions

**Never use hardcoded handle lists** when the DXF may have been regenerated or cleaned. Handles are **file-specific identifiers** — they are not stable across DXF→DWG→DXF round-trips or between different export tools.

**Mitigation:**
1. Always **dynamically discover** entity handles by position (y-zone), type, and text content
2. Use terminal label TEXTs as **anchors** to define row zones
3. Verify discovered handles exist in the **ENTITIES section**, not BLOCKS section
4. After discovery, print each handle with its entity type and y-position for human verification

## Key Pitfall: BLOCKS vs ENTITIES Section Confusion

The DXF format has two sections that can contain entities with handles:
- **BLOCKS section**: Template geometry for block definitions (y-positions often near 0 or 10)
- **ENTITIES section**: Actual instantiated geometry in the drawing (y-positions match the drawing)

A handle like `9846` might exist in BOTH sections — the BLOCKS one is the template, the ENTITIES one is the actual wire. Always verify which section your handle points to.

## Key Pitfall: VLM Cannot Catch Handle-Level Corruption

The VLM analyzes rendered screenshots, not DXF data structures. It will happily approve a drawing where:
- Wrong handles were cloned (BLOCK DEFINITION instead of ENTITIES)
- Clone offsets produced wires in wrong positions
- Partial deletions left a mix of old and new geometry

**Mitigation:** Always pair VLM visual checks with programmatic verification:
1. Entity counts per terminal row before and after clone
2. Handle uniqueness check (no collisions)
3. y-position mapping: does each cloned entity land in the correct target zone?
4. Text content verification: are terminal labels present exactly once?

## V8 Verification Script Pattern

```python
# After cloning, verify:
for row_num in [7, 8, 9]:
    y_target = terminal_labels[row_num]['y']
    zone_ents = [e for e in entities 
                 if abs(e['y_avg'] - y_target) <= 0.15]
    
    # Must have at least one wire element
    wire_count = len([e for e in zone_ents if e['type'] in ('LINE', 'ARC', 'LWPOLYLINE')])
    text_count = len([e for e in zone_ents if e['type'] == 'TEXT'])
    
    print(f"T{row_num}: {wire_count} wires, {text_count} texts")
    assert wire_count >= 1, f"T{row_num} has no wire geometry!"
    assert text_count >= 2, f"T{row_num} missing labels!"
```
