# Pair 3 Clone Pipeline Lessons

Session: May 15, 2026

## Critical Pipeline Steps

1. Extract source handles geometrically (y-range, x-range). Do NOT clone terminal INSERT blocks — they already exist at destination.
2. Allocate clone handles below original file's max handle. QCAD silently drops clones with handles > original max.
3. Clone by text insertion: copy raw entity blocks, replace group code 5 (handle) and 330 (owner), strip reactor handles (360) and XDICTIONARY (102/360).
4. Apply dy offset to Y coordinates only (group codes 20/21). Never touch 30/31 (Z stays 0.0).
5. Apply selective dx offset for cable tags that overlap destination text. Text-based handle-targeted shifting is safer than re-cloning.
6. Text replacements post-clone: CA-1451→CA-1452, (6)→(7), drawing number suffix.
7. Fix layer visibility (negative 62 → positive) before QCAD export. Use text-based script only.
8. QCAD headless: call qcad-bin directly with -platform offscreen. Never the qcad wrapper. Kill lingering processes first.
9. Verify in TrueView. QCAD ODA export strips BLOCK sections (title block lost). Modelspace entities preserved.

## V9 Lessons (May 16, 2026)

| Bug | Symptom | Fix |
|-----|---------|-----|
| ARC y-extraction wrong | ARC entities get y_avg = radius/2 instead of center.y | Skip group 40 (radius) when scanning for y-coordinates in ARC entities |
| Wide tolerance contamination | T5→T8 clone picks up T4/T6 entities | Use tight tolerance ±0.15 per row; only expand to ±0.35 after confirming no adjacent contamination |
| Hardcoded handle failure | V7 used handles from different DXF version, cloned wrong entities | Dynamic discovery from actual DXF using terminal label y-positions |
| Missing wire labels | Cloned geometry present but wire labels (W, RED&BLUE) missing | TEXT entities at y≈±0.15 from terminal row must be included in clone set |
| VLM verification false positive | VLM reports "missing (W) at T9" | Original T6 has no (W) label — T9 correctly has none. VLM verification needs side-by-side comparison PNG (original vs clone) for best accuracy |

## Verified Correct dy Offsets (V11 Fix — May 17, 2026)

**Critical correction**: Terminal spacing is uniform Δy = 0.250 per terminal number.
Therefore **dy = −0.75 for all three rows** (T4→T7, T5→T8, T6→T9).

| Mapping | Source Y | Target Y | dy |
|---------|----------|----------|-----|
| T4 → T7 | 20.125 | 19.375 | **−0.750** |
| T5 → T8 | 19.875 | 19.125 | **−0.750** |
| T6 → T9 | 19.625 | 18.875 | **−0.750** |

**Previous error**: Earlier versions (V7–V10) used −1.250 / −1.000 / −1.250, which landed clones at T9/T10/T11 instead of T7/T8/T9. Always verify terminal positions programmatically before cloning.

### Programmatic dy Verification

```python
import ezdxf

def get_terminal_positions(filepath):
    """Extract terminal y-positions from Wlltermn INSERTs."""
    doc = ezdxf.readfile(filepath)
    terminals = []
    for e in doc.modelspace():
        if e.dxftype() == 'INSERT' and e.dxf.name == 'Wlltermn':
            terminals.append(e.dxf.insert.y)
    terminals.sort(reverse=True)
    return {i+1: y for i, y in enumerate(terminals)}

# Verify before cloning
t = get_terminal_positions("3_clean.dxf")
print(f"T4={t[4]:.3f}, T7={t[7]:.3f}, dy={t[7]-t[4]:.3f}")
print(f"T5={t[5]:.3f}, T8={t[8]:.3f}, dy={t[8]-t[5]:.3f}")
print(f"T6={t[6]:.3f}, T9={t[9]:.3f}, dy={t[9]-t[6]:.3f}")
# → All should be −0.750
```

**Never assume dy from memory or previous sessions.** Always measure from the actual DXF.

## V9 Clone Mapping (OBSOLETE — dy values were wrong; corrected above)

- T4→T7: 8 wire entities (LINEs + ARCs + TEXT '(W)') + 3 cable/PLC tags
- T5→T8: 2 wire entities (LINE + TEXT '(RED & BLUE)')  
- T6→T9: 2 wire entities (LINE + ARC)

## ARC Y-Coordinate Extraction Rule

When extracting y-positions from DXF group codes for entity discovery:

| Entity | Y Group Codes | Skip Codes |
|--------|--------------|------------|
| LINE | 20, 21 | — |
| ARC | 20 (center.y) | 40 (radius) |
| TEXT/INSERT | 20 (insert.y) | — |
| LWPOLYLINE | 20 (vertex y) | — |

**Critical**: Group 40 in ARC entities is RADIUS, not a coordinate. Including it in y-extraction produces garbage y-values (e.g., y_avg=9.938 for an ARC at y=19.750 with radius=0.125), causing the entity to be missed by row-based discovery.

## Dynamic Discovery Algorithm

```
1. Parse DXF, build entity map with per-type y-extraction
2. Find all terminal labels: TEXT matching ^\(\d+\)$ → terminal number → y-position
3. For each source row:
   a. Collect non-INSERT entities where |entity.y - terminal.y| ≤ 0.15
   b. Exclude terminal labels ^\(\d+\)$
   c. Exclude instrument names (EPAC G1...)
   d. Exclude cable/PLC tags (unless explicitly cloning them)
   e. Result = wire geometry for that row
4. Clone with dy offset, inserting new handles before ENDSEC
```

## V17–V18 Geometry Clone Pass Lessons (May 17, 2026)

When cloning from a **clean source DXF** into an **already-modified target DXF** (e.g., text-only clones already present), a dedicated geometry-clone pass is required. Wire geometry does not automatically follow text labels.

### Tolerances That Prevented Cross-Terminal Contamination

| Check | Tolerance | Purpose |
|-------|-----------|---------|
| Source→target y-matching | ≤ **0.15** | Ensures only entities belonging to the source terminal row are cloned |
| Duplicate-position detection | ≤ **0.10** | Prevents re-cloning geometry already present at the target row from earlier scripts |
| Label overlap clearance | ≥ **0.70** | Minimum horizontal distance between cloned cable tag and existing target labels |

### Open-End Path Filtering (V18)

After cloning, some geometry entities may form **open-end cable drops** that extend far below the terminal stack. These are partial clones of source cable paths that don't terminate properly at the target.

**Detection rule:** Any cloned entity with y < **18.0** (or > 1.0 units below the lowest target terminal) is a stray open-end and must be removed.

Example: A vertical LINE cloned from T3 to T7 with endpoints (19.75, 19.31) → (19.75, 10.00) — the lower endpoint at y=10.00 is a clear stray.

### Cable Tag Offset Strategy (V18)

Cloning a cable callout group (CA-1451, WFEND, WECOIL, LWPOLYLINE, LINE, TEXT) **exactly** duplicates its x-position. If the target terminal already has a label at that x-position (e.g., "RED & BLUE" at x≈20.19), the cloned tag will overlap.

**Fix:** Apply `dx = −1.4` to the entire cable callout group during clone. This shifts the tag leftward while preserving visual association with the target terminal wiring.

| Before offset | After offset | Clearance to existing label |
|---------------|--------------|----------------------------|
| x ≈ 20.85 | x ≈ 19.45 | 0.74 units to "RED & BLUE" at x≈20.19 |

### Stray Arc Detection

Source ARCs at the boundary of the source row (y≈20.5) may be partially inside the ±0.15 tolerance. When cloned with dy=−0.75, they land at y≈19.5 — inside the target row but disconnected from the terminal's actual wire pattern. These are **duplicate arcs** (already present in the target from earlier scripts) or **open-end fragments**.

**Removal:** After the geometry-clone pass, scan for ARCs with x > 18.0 at the target row level. If they don't connect to a known terminal block, remove them.

### Geometry + Text Two-Pass Clone Pattern

1. **Text-only pass first** (v16) — clone TEXT/MTEXT labels, handle cable tag renumbering, fix layer colors.
2. **Geometry pass second** (v17) — clone LINE/ARC/LWPOLYLINE from clean source, skip duplicates already present at target.
3. **Stray cleanup third** (v18) — remove open-end paths, fix overlaps, add missing cable callout groups.
4. **Verification after each pass** — programmatic ezdxf checks before human/VLM review.

## Verification Checklist (Updated V18)

- [ ] Cloned entities present at target rows (T7/T8/T9)
- [ ] Wire labels present: (W) at T7, (RED & BLUE) near T8
- [ ] No duplicate terminal numbers
- [ ] Cable/PLC tags updated: CA-1451→CA-1452, PLC21→PLC22
- [ ] Cable tag does NOT overlap existing target labels (clearance ≥ 0.70)
- [ ] Zero stray entities with y < 18.0 (open-end cable drops)
- [ ] ARC y-extraction verified: no radius contamination
- [ ] All layer colors positive
- [ ] TrueView opens without errors
- [ ] VLM side-by-side comparison shows matching patterns

## V2 Bug Fixes

| Bug | Symptom | Fix |
|-----|---------|-----|
| Consecutive 0 insertion | 160+ empty lines at ee | Insert at ee-1 (before ENDSEC) |
| Z corruption | Modified 30/31 instead of 20/21 | Only offset Y (20/21) |
| Handle collision | SAFE_BASE > original max | Allocate below original max |
| LF line endings | TrueView hangs on open | Enforce CRLF (\r\n) |

## Coordinate Mapping

PDF → DXF (1224×792 landscape): x_dxf = y_pdf/72, y_dxf = (1224−x_pdf)/72

## Clone Offset

T4→T7: dy = −1.25 (verified by user). Terminal numbers 7/8/9 are Wlltermn block INSERTs with TERMNUM ATTRIBs — already present. Clone only wire geometry.

## QCAD ODA Export Limitations

- BLOCK definitions STRIPPED (314 KB → 76 KB)
- Modelspace entities PRESERVED
- Handle reassignment warnings BENIGN
- Title-block revision rows LOST (no headless fix in QCAD 3.32.7)

## Verification Checklist

- [ ] 39-40 clones in ezdxf modelspace
- [ ] All clone handles below original max
- [ ] Terminal 7/8/9 wires visible at correct y
- [ ] Text (7), (8), (9) present
- [ ] Cable tag shifted right by dx=1.0 if overlap reported
- [ ] Drawing number shows correct suffix
- [ ] Zero HATCH entities
- [ ] All layer colors positive
- [ ] CRLF only, no LF-only lines
- [ ] TrueView opens without errors
