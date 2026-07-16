# ezdxf Dynamic Discovery Clone Pattern (Pair 3 V6)

**Date:** 2026-05-24
**Validated on:** Drawing 3 (terminal wire duplication T4/T5/T6 → T7/T8/T9)
**Replaces:** hardcoded-handle QCAD ECMAScript cloning (see `references/pair3-v19-v24-iteration-log.md` for why ECMAScript cloning became unmaintainable across files)

---

## Problem Statement

The original Pair 3 approach used a QCAD ECMAScript (`copy_entities.js`) with **hardcoded handle lists** extracted from a specific DXF. When the same task needed to run on a new `3.dxf` (different drawing or different export), none of the old handles matched. Re-discovery was manual and error-prone.

The user asked: *"check script in the skill as pair 3 has been completed in the past. this time we just want to run same modification again but on the dxf file directly."*

That means: **same semantics, different file, automatic discovery**.

---

## Solution: ezdxf Discovery + `copy()` + `add_entity()`

### Phase 1: Terminal Discovery

Instead of hardcoding handles, discover terminal positions dynamically via landmark INSERTs or TEXT labels:

```python
def discover_terminals(doc):
    """Find terminal rows by Wlltermn INSERT positions or (N) TEXT labels."""
    msp = doc.modelspace()
    terminals = {}
    for e in msp:
        if e.dxftype() == 'INSERT' and e.dxf.name == 'Wlltermn':
            col = e.dxf.insert[0]
            y = e.dxf.insert[1]
            # left column = even terminals, right column = odd
            if col < 12:
                tnum = int(round((20.929 - y) / 0.5)) * 2  # 2,4,6...
            else:
                tnum = int(round((20.929 - y) / 0.5)) * 2 - 1  # 1,3,5...
            terminals[tnum] = {'y': y, 'insert': e.dxf.insert}
    return dict(sorted(terminals.items()))
```

> **Why INSERTs, not TEXTs:** The `(N)` labels are offset ~0.7 units from the actual terminal position. Use `Wlltermn` INSERT insert points for accurate wire y-coordinates.

### Phase 2: Entity Collection by Geometric Band

For each source terminal, collect all entities within a y-tolerance band around that terminal's y-position:

```python
def collect_wire_group(msp, source_y, source_x_range=(0, 13), tol=0.15):
    """Gather all geometry, text, and simple INSERTs near a terminal row."""
    group = []
    for e in msp:
        y = get_entity_y(e)
        x = get_entity_x(e)
        if y is None:
            continue
        if abs(y - source_y) <= tol and source_x_range[0] <= x <= source_x_range[1]:
            # Skip terminal block INSERTs (have ATTRIB/SEQEND sub-entities)
            if e.dxftype() == 'INSERT' and e.dxf.name in ('Wlterm1', 'Wlltermn'):
                continue
            group.append(e)
    return group
```

**Tolerance evolution:**
- V5 used `tol=0.02` → missed right-side label texts (`PLC21`, `CA-1451`) sitting slightly above/below the wire.
- V6 used `tol=0.2` → captured full wire + labels + callout group without leaking into adjacent terminals one row away (~0.5 units).

### Phase 3: Clone with Per-Terminal dy Offset

Each source→target pair gets its own vertical shift, computed from terminal positions:

```python
# Discovery yields actual y positions
# T4 at y≈19.875, T7 at y≈18.625 → dy = -1.250
# T5 at y≈19.375, T8 at y≈18.375 → dy = -1.000
# T6 at y≈19.125, T9 at y≈17.875 → dy = -1.250

def clone_group(doc, entities, dy):
    """Clone a group of entities with dy offset."""
    msp = doc.modelspace()
    for e in entities:
        # Simple geometry: clone by copy() + add_entity()
        cloned = e.copy()
        # Apply dy offset based on entity type
        shift_entity(cloned, dy=dy)
        msp.add_entity(cloned)
    return doc
```

**Why `copy()` + `add_entity()` wins:**
- ezdxf auto-assigns new handles → no manual handle allocation
- Preserves entity type, layer, color, linetype
- Works for LINE, ARC, LWPOLYLINE, TEXT, simple INSERT
- **Does NOT work for INSERTs with ATTRIB** → skip those, target rows already have their own blocks

### Phase 4: Text Replacement on Cloned Entities

After cloning, identify cloned text entities by their y-position and replace content:

```python
def replace_cloned_texts(doc, target_y, replacements, tol=0.15):
    """Replace text content on cloned entities near a target row."""
    msp = doc.modelspace()
    for e in msp:
        if e.dxftype() != 'TEXT':
            continue
        y = e.dxf.insert[1]
        text = e.dxf.text
        if abs(y - target_y) > tol:
            continue
        for old, new in replacements.items():
            if old in text:
                e.dxf.text = text.replace(old, new)
```

**Critical replacements applied (Pair 3 V6):**
- `PLC21 (FUTURE)` → `PLC22 (FUTURE)`
- `CA-1451` → `CA-1452`
- `TO DWG. B-SAR-280-02732` → `02733`

### Phase 5: Programmatic Verification (Primary Quality Gate)

Before any human/VLM review, assert the output DXF contains expected entities:

```python
def verify_pair3(doc, expected):
    """
    expected: dict of {terminal_num: [list of expected text substrings]}
    Returns: (ok: bool, errors: list)
    """
    msp = doc.modelspace()
    errors = []
    for tnum, expected_texts in expected.items():
        y_target = terminals[tnum]['y']
        found = set()
        for e in msp:
            if e.dxftype() == 'TEXT' and abs(e.dxf.insert[1] - y_target) <= 0.15:
                for et in expected_texts:
                    if et in e.dxf.text:
                        found.add(et)
        missing = set(expected_texts) - found
        if missing:
            errors.append(f"T{tnum}: missing {missing}")
    return len(errors) == 0, errors
```

**Why this is the primary gate:** VLM screenshot verification cannot reliably read <10pt terminal text at full-screen resolution. Programmatic DXF inspection is deterministic.

---

## Pitfalls Avoided by This Pattern

| Pitfall | Hardcoded-handle ECMAScript | Dynamic ezdxf clone |
|---------|-----------------------------|----------------------|
| Handles stale across files | ✅ Broken | ✅ Auto-assigned |
| Manual handle allocation error | ✅ Present | ✅ Eliminated |
| INSERTs with ATTRIB sub-entities | ✅ Corrupts DXF | ✅ Skipped explicitly |
| Missed right-side labels (tight tol) | ✅ V5 failure | ✅ V6 recovered with tol=0.2 |
| Uniform dy across all terminals | ✅ Misplaced wires | ✅ Per-terminal dy computed |
| No verification before DWG export | ✅ User rejects after export | ✅ DXF assert before export |

---

## When to Use This Pattern

- **Terminal wire duplication** with consistent row spacing
- Any task where **"run same modification on a different file"** is expected
- Source and target positions can be **discovered by geometry** (not hardcoded)
- Entities to clone are **simple geometry + text** without complex BLOCK hierarchies

## When NOT to Use

- Cloning complex BLOCK INSERTs with ATTRIBs (use QCAD ECMAScript or manual block editing)
- Tasks requiring new entity creation (e.g., drawing a line that didn't exist) — use QCAD ECMAScript
- Tasks where handle stability across round-trips is critical (use text-based entity tracking)

---

## Reference Implementation

See `scripts/pair3_pipeline_v6.py` in the workspace (not in skill repo — it was ephemeral). The key structural pattern is 30 lines of discovery + 40 lines of clone logic. Reproduce by combining the code blocks above.
