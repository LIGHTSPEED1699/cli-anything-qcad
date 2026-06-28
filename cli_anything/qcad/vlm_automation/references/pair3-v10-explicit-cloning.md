# Pair 3 V10 Explicit Per-Row Cloning Recipe (2026-05-16)

## Problem

Dynamic handle discovery by y-range alone can still fail when:
- Adjacent terminal rows are close (Δy ≈ 0.25)
- Shared vertical/diagonal lines span multiple rows
- ARC radius values contaminate y-extraction
- VLM verification declares "GOOD" while TrueView shows handle-level corruption

V7 used hardcoded handles from a different DXF version and cloned BLOCK DEFINITION entities instead of instantiated wire geometry. V8 switched to dynamic discovery but still had contamination. V9 tightened tolerances. **V10 fixed it by using explicit per-row entity lists derived from the actual DXF**.

## V10 Solution: Explicit Per-Row Entity Lists

After dynamic discovery, manually curate the exact handles per source row. This eliminates contamination and ensures only the intended geometry is cloned.

### Step 1 — Dynamic Discovery (Validation Only)

Use y-range discovery to **find candidate handles**, then inspect each one:

```python
# Terminal label positions (anchors)
terminals = {
    3: {'y': 20.179, 'label': '(3)'},
    4: {'y': 19.929, 'label': '(4)'},
    5: {'y': 19.429, 'label': '(5)'},
    6: {'y': 19.179, 'label': '(6)'},
    7: {'y': 18.679, 'label': '(7)'},
    8: {'y': 18.429, 'label': '(8)'},
    9: {'y': 17.929, 'label': '(9)'},
}

# Discover candidates within ±0.15 of each terminal y
candidates = {n: [] for n in terminals}
for e in msp:
    y = entity_y_center(e)
    for n, t in terminals.items():
        if abs(y - t['y']) <= 0.15:
            candidates[n].append((e.dxf.handle, e.dxftype(), y))
```

### Step 2 — Curate Explicit Lists

Print candidates, inspect, then build **hardcoded lists for THIS DXF**:

```python
# T4 → T7 source handles (explicit, verified)
t4_sources = [
    "9846", "9978",    # ARCs
    "997B",            # vertical LINE
    "963B",            # left horizontal LINE
    "9877",            # INSERT WLGND (no ATTRIBs)
    "9970",            # right horizontal LINE
    "963D", "997A", "9979",  # ARC transitions
    "9852",            # TEXT 'EPAC G1 14 N'
    "9639",            # TEXT '(4)'
    "9972",            # TEXT '(W)'
    "9A77",            # TEXT '2C SPARE'
    # cable / PLC tags that travel with T4 wire
    "998B",            # TEXT 'PLC21 (FUTURE)'
    "963C", "9971",    # LINEs
    "9983",            # INSERT WFEND
    "9A76",            # INSERT WECOIL
    "9638",            # TEXT 'EPAC G1 14 H'
    "9868", "9885",    # ARCs
    "998A",            # TEXT 'TO DWG...'
    "9A79",            # LWPOLYLINE cable line
    "9886",            # LINE horizontal
    "9866",            # LINE horizontal
    "9A81",            # TEXT 'CA-1451'
    "9A80",            # LINE cable tag
    "97A4",            # LINE vertical cable
]

t5_sources = [
    "9647",            # left horizontal LINE
    "9643",            # TEXT 'EPAC G1 15 H'
    "964D",            # TEXT '(5)'
    "9A78",            # TEXT '(RED & BLUE)'
    "9974",            # right horizontal LINE
    "9975",            # TEXT '(GND)'
    "9648",            # ARC
]

t6_sources = [
    "9847",            # ARC
    "9646",            # left horizontal LINE
    "9853",            # TEXT 'EPAC G1 15 N'
    "9644",            # TEXT '(6)'
]
```

### Step 3 — Clone with Per-Group Offsets

```python
clone_specs = [
    {'sources': t4_sources, 'dy': -1.250, 'target_row': 7},
    {'sources': t5_sources, 'dy': -1.000, 'target_row': 8},
    {'sources': t6_sources, 'dy': -1.250, 'target_row': 9},
]

# Also clone labels that live near the source row but belong to the target
t3_b_to_t7 = ["9973"]   # T3 "(B)" label cloned to T7
t4_w_to_t8 = ["9972"]   # T4 "(W)" label cloned to T8
gnd_to_t9  = ["9975"]   # "(GND)" label cloned to T9
```

## Why This Works

| Approach | Risk | V10 Fix |
|----------|------|---------|
| Hardcoded handles from old DXF | BLOCK DEFINITION corruption | Use handles from **this** DXF only |
| Pure dynamic y-range | Adjacent-row contamination | Curate explicit list after discovery |
| Uniform dy for all rows | Wrong target positions | Per-row dy = target_y − source_y |
| VLM alone for verification | Handle-level errors invisible | Pair with programmatic row-zone counts |

## VLM Verification Pattern for Cloned Drawings

Render **side-by-side comparison PNG** (original vs. modified) with zoomed crops:

```python
# Full page for overview
render_side_by_side("3_clean.dxf", "3_cloned.dxf", "/tmp/compare_full.png")

# Zoomed crop for terminal detail
render_zoomed("3_cloned.dxf", "/tmp/compare_zoom.png",
              xlim=(1, 18), ylim=(16.5, 21.5))
```

VLM prompt for side-by-side:
```
LEFT = original drawing. RIGHT = modified drawing.
We cloned wire geometry from terminals 4-6 to new terminals 7-9.

Check terminals 4 through 9 ONLY:
1. Are terminal labels (4)(5)(6)(7)(8)(9) present exactly once each?
2. Do wires at 7-9 look like clones of 4-6?
3. Are there any wires at terminal 10 that shouldn't be there?
4. Is the left wire on T9 still present?

Return: GOOD / NEEDS_WORK / ERROR with specific details.
```

## Pitfall: VLM Timeout on Large Renders

Full-page renders at 200 DPI can exceed 50KB and cause VLM API timeouts. Use:
- Lower DPI (150) for full page
- Smaller figsize for zoomed crops
- `timeout=120` minimum; `timeout=300` for first call (model warmup)

## Pitfall: VLM Cannot Catch Handle Corruption

The VLM analyzes pixels, not DXF handles. It will approve a drawing where:
- Wrong handles were cloned (BLOCK DEFINITION instead of ENTITIES)
- Clone offsets are slightly off but still visually coherent
- Partial deletions leave a mix of old and new

Always pair VLM visual checks with:
1. Entity counts per target row (≥1 wire element expected)
2. Handle uniqueness check (no collisions in cloned set)
3. y-position verification: each clone lands within ±0.15 of target row y

## Files

- `3_cloned_v10.py` — V10 explicit-list clone script
- `3_FINAL_v10.dwg` — 76,561 bytes, verified correct
