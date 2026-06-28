# Pair 3 V6 Correction Notes

## Bugs Fixed from V5

### 1. Four wires cloned instead of three
**Root cause:** T3 wire elements (y≈20.125) were mixed into the T4 source group.
- T3 terminal text: `(3)` at y=20.179
- T4 terminal text: `(4)` at y=19.929
- T5 terminal text: `(5)` at y=19.429
- T6 terminal text: `(6)` at y=19.179

**V5 behavior:** Used uniform dy=-0.750 for ALL source handles, which shifted T3 elements down to T5 position (20.179 - 0.750 = 19.429), causing overlap and 4 apparent wires.

**V6 fix:** Removed T3-only handles from source: `9867`, `998A` (TO DWG), `9642` ((3)), `9973` ((B)), `97A4` (vertical line), `9A80` (line), `9A79` (LWPOLYLINE).

### 2. Wires placed at wrong terminals (T8-T11 instead of T7-T9)
**Root cause:** Uniform dy=-0.750 applied to all wires regardless of source terminal position.

| Source | Source Y | Target | Target Y | Correct dy | V5 dy | V5 result |
|--------|----------|--------|----------|------------|-------|-----------|
| T4 | 19.875 | T7 | 18.625 | -1.250 | -0.750 | y=19.125 ≈ T6 |
| T5 | 19.375 | T8 | 18.375 | -1.000 | -0.750 | y=18.625 ≈ T7 |
| T6 | 19.125 | T9 | 17.875 | -1.250 | -0.750 | y=18.375 ≈ T8 |

**V6 fix:** Three separate source groups, each with its own dy:
- T4 group: dy = -1.250 → T7
- T5 group: dy = -1.000 → T8
- T6 group: dy = -1.250 → T9

### 3. PLC21 (FUTURE) text missing
**Root cause:** Handle `998B` ('PLC21 (FUTURE)') was not in the V5 source handles list.

**V6 fix:** Added `998B` to T4 source group with replacement `PLC21` → `PLC22`.

### 4. DXF corruption when cloning terminal blocks
**Root cause:** Terminal block INSERTs (`Wlterm1`, `Wlltermn`) have ATTRIB and SEQEND sub-entities. The text-based DXF cloner only copied the INSERT entity itself, not its sub-entities. This caused `DXFStructureError: Expected DXF entity TEXT or SEQEND` when ezdxf tried to load the file.

**V6 fix:** Excluded terminal block INSERTs from cloning. T7-T9 already have their own terminal blocks; we only need to clone the wire elements (LINEs, ARCs, TEXTs, simple INSERTs without ATTRIBs).

### 5. Revision history rows c, d, e empty
**Root cause:** Revision rows are ATTDEF placeholders inside the `PLAINS-D-CAN` title block. They have no default values — values are set as ATTRIB entities when the block is inserted. LibreDWG DXF round-trip may drop ATTRIB values that were present in the original DWG.

**V6 status:** Block exists, ATTDEF definitions present. If revision rows appear empty, the original DWG had ATTRIB values that were lost during DXF conversion. Fix requires explicitly setting ATTDEF default values or re-inserting the block with ATTRIB values.

## Correct Source Handle Groupings (for 3_clean.dxf)

```python
source_t4 = [
    # Wire elements for T4 (terminal 4, y≈19.875) → clone to T7
    "9846", "9978",    # ARCs
    "997B",            # Vertical LINE
    "963B",            # Left horizontal LINE
    "9877",            # INSERT WLGND (no ATTRIBs)
    "9970",            # Right horizontal LINE
    "963D", "997A", "9979",  # ARC transitions
    "9852",            # TEXT 'EPAC G1 14 N'
    "9639",            # TEXT '(4)'
    "9972",            # TEXT '(W)'
    "9A77",            # TEXT '2C SPARE'
    # T3-associated elements that belong to T4 wire path
    "998B",            # TEXT 'PLC21 (FUTURE)'
    "963C",            # LINE
    "9971",            # LINE
    "9983",            # INSERT WFEND (no ATTRIBs)
    "9A76",            # INSERT WECOIL (no ATTRIBs)
    "9638",            # TEXT 'EPAC G1 14 H'
    "9868", "9885",    # ARCs
    "998A",            # TEXT 'TO DWG. B-SAR-280-02732'
    "9A79",            # LWPOLYLINE cable line
    "9886",            # LINE horizontal
    "9866",            # LINE horizontal
    "9A81",            # TEXT 'CA-1451'
    "9A80",            # LINE cable tag
    "97A4",            # LINE vertical cable
]

source_t5 = [
    # Wire elements for T5 (terminal 5, y≈19.375) → clone to T8
    "9647",            # Left horizontal LINE
    "9643",            # TEXT 'EPAC G1 15 H'
    "964D",            # TEXT '(5)'
    "9A78",            # TEXT '(RED & BLUE)'
    "9974",            # Right horizontal LINE
    "9975",            # TEXT '(GND)'
    "9648",            # ARC
]

source_t6 = [
    # Wire elements for T6 (terminal 6, y≈19.125) → clone to T9
    "9847",            # ARC
    "9646",            # Left horizontal LINE
    "9648",            # ARC (shared visually)
    "9853",            # TEXT 'EPAC G1 15 N'
    "9644",            # TEXT '(6)'
]

# DO NOT include terminal block INSERTs:
# "3621"/"3651" (T4 blocks), "3623"/"3657" (T5), "3624"/"365A" (T6)
# These have ATTRIB/SEQEND sub-entities and cause DXF corruption.
```

## Key Pitfalls

**Pitfall: Uniform dy offset across multiple source terminals**
When cloning wires from different source terminals, each terminal may need a different dy. Compute dy per group as `target_y - source_y`, not a single global value.

**Pitfall: Cloning INSERTs with ATTRIB sub-entities**
INSERT blocks that contain ATTRIB definitions (group code 66 = 1) require their ATTRIB and SEQEND sub-entities to be cloned with matching new handles. If using text-based DXF editing, it's safer to skip these INSERTs entirely and only clone simple geometry.

**Pitfall: Mixing adjacent terminal wire elements**
Wire elements from adjacent terminals (e.g., T3 and T4) may overlap in y-coordinate. Use tighter y-bands or explicit handle lists rather than broad y-range discovery to avoid including wrong-terminal elements.

**Pitfall: ATTDEF vs ATTRIB for title block values**
Title block values like revision history are often ATTDEF (default values in block definition) or ATTRIB (instance values on INSERT). ATTDEF values survive DXF round-trip; ATTRIB values may not. If revision rows appear empty after DXF→DWG conversion, the original values were likely ATTRIB instance data lost by LibreDWG.
