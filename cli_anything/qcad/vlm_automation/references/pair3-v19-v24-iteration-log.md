# Pair 3 V19–V24: Iteration Log — From User Rejection to Accepted

## Context

Pair 3 (Drawing 3) is a terminal wire duplication task: clone wire geometry and labels from terminals T4/T5/T6 to T7/T8/T9 per PDF markup. This log captures the V19 through V24 cycle — every mistake, correction, and the final acceptance — to prevent future regressions.

## V19 Submission (V18 Final → QCAD Export)

**What was sent:** `3_FINAL_v19.dwg` (76,721 bytes)

**Changes from V17:**
- Fixed T7 wire geometry (inserted from `3_cloned_v17.dxf`)
- Removed stray ARC 9A83 (T3→T7 contamination)
- Added CA-1452 cable callout group to T7 (clone from T5 with dx=−1.4)

**User-reported defects (V19 → V20):**
1. **Deleted wrong duplicate callout** — kept empty bracket 9A8A, deleted actual CA-1452 TEXT/leader/bracket 9A8E/9A8F/9A90
2. **WFEND/WECOIL positioned mid-wire** instead of at wire end
3. **Leftover vertical line** below ground symbol (LINE 9A82)

---

## V20 Correction Attempt

**What was sent:** `3_FINAL_v20.dwg` (76,368 bytes)

**Deletions:** 9A82, 9A84, 9A90, 9A8F, 9A8E (5 entities)

**Result:** CA-1452 tag/leader/bracket permanently gone. Only empty bracket 9A8A remained.

**User feedback (V20 → V21):**
> "deleted wrong duplicate callout — kept empty bracket 9A8A, deleted actual CA-1452 tag/leader/bracket (9A8E/9A8F/9A90)"

**Root cause:** Failed to verify entity content before deletion. 9A8E was the TEXT "CA-1452", 9A8F was a leader LINE, 9A90 was a LWPOLYLINE bracket. 9A8A was an empty generic bracket. The wrong group was deleted.

---

## V21 Callout Reconstruction & Mid-Wire Fix

**What was sent:** `3_FINAL_v21.dwg` (76,560 bytes)

**Fixes applied:**
1. **Reconstructed CA-1452 callout** — cloned from V19 source DXF (handles 95E5–95E9) into v16_fixed.dxf with new handles 9A93(TEXT), 9A94(leader), 9A8A(bracket), 9A91(WFEND), 9A92(WECOIL)
2. **Shifted WFEND/WECOIL right** — from mid-wire (x=19.35, x=18.69) to wire end (x=20.75, x=20.09)
3. **Positioned CA-1452 at x=20.875**

**New defect introduced:** CA-1452 cable tag overlapped "TO DWG. B-SAR-280-02732" note at x=20.875 — only 0.406 units vertical separation.

**User feedback (V21 → V22):**
> "cable tag callout symbol is still overlapping with text above"

---

## V22 First Shift (+0.5 x)

**What was sent:** `3_FINAL_v22.dwg` (76,561 bytes)

**Shift applied:**
| Element | V21 x | V22 x |
|---|---|---|
| Bracket (9A8A) | 20.531 | 21.031 |
| Leader start (9A94) | 20.531 | 21.031 |
| Leader end (9A94) | 20.813 | 21.313 |
| CA-1452 text (9A93) | 20.875 | 21.375 |

**Collision detector result:**
- Residual overlap between "T" of "TO DWG" and "C" of "CA-1452" ≈ 0.004 units
- Below visual tolerance but user still noticed

**User feedback (V22 → V23):**
> "cable tag and callout symbols are moved to the right, the wire length, the end of line (discontinuous symbol ~), and the 'TO DWG...' text should all be moved to the right"

---

## V23 Second Shift (+0.5 x, total +1.0 from V21)

**What was sent:** `3_FINAL_v23.dwg` (76,561 bytes)

**Shift applied:**
| Element | V21 x | V23 x |
|---|---|---|
| Horizontal wire | 17.25→20.75 | 17.25→21.75 |
| WFEND | 20.750 | 21.750 |
| WECOIL | 20.093 | 21.094 |
| Bracket (9A8A) | 20.531 | 21.531 |
| Leader (9A94) | 20.531→20.813 | 21.531→21.813 |
| CA-1452 text (9A93) | 20.875 | 21.875 |
| TO DWG note | 20.875 | 21.875 |

**Collision detector:** Zero overlapping boundingboxes. Vertical gap = 0.32 units ≥ 0.05 threshold.

**User feedback (V23 → V24):**
> "in 3_FINAL_V22.dwg, the cable tag callout symbol is still overlapping with text above … when cable tag and callout symbols are moved to the right, the wire length, the end of line (discontinuous symbol ~), and the 'TO DWG...' text should all be moved to the right. Currently, the cable tag callout symbol is not on the wire itself as the wire length is still the original length."

---

## V24 Final — Entire Wire-End Assembly Shifted Right

**What was sent:** `3_FINAL_v24.dwg` (76,593 bytes)

**Critical realization:** The user's complaint was NOT about residual micro-overlap. It was conceptual — the CA-1452 callout assembly (bracket, leader, tag, symbols, notes) was shifted right but the **wire itself had not been extended**. The tag was not sitting ON the wire end.

**V24 fix:**
- Extended horizontal wire: 17.25 → 21.75 (was 20.75)
- Repositioned WFEND at wire end: 21.750
- Repositioned WECOIL: 21.094
- Repositioned bracket: 21.531
- Repositioned CA-1452 text: 21.875 (sits ON wire at y=19.375)
- Repositioned TO DWG note: 21.875 (vertically above)
- All elements share consistent x-offset relative to wire end

**Verification:** Bounding-box collision detector re-run. All textual/callout entities ≥ 0.05 units separated vertically. Zero overlaps.

**User verdict (2026-05-21):**
> "this version V24 is excellent on the terminal wire duplication task"

---

## Mistakes Timeline (What Went Wrong & Why)

| Version | Mistake | Root Cause | Corrective Action |
|---|---|---|---|
| **V18** | Stray ARC and LINE at T7 | ±0.15 tolerance too broad; caught T3 source | Removed stray entities; tighten to ±0.10 |
| **V18** | Missing CA-1452 callout group | Group sat above T4 row at y≈20.375; missed by geometric filter | Explicitly cloned by handle from clean source |
| **V19** | Deleted actual CA-1452 tag/leader/bracket | Failed to verify entity content before deletion; deleted 9A8E/9A8F/9A90 instead of empty 9A8A | Properly content-verify before any deletion |
| **V20** | WFEND/WECOIL mid-wire | Positioned at x=19.35/18.69 instead of wire end x=20.75/20.09 | Shift to wire-end position |
| **V21** | CA-1452 overlap with "TO DWG" | No spatial collision check before placement | Add font-aware bbox collision detection |
| **V22** | Residual micro-overlap (0.004) | `margin=0.02` too tight for font metric approximation | Use `margin=0.05` minimum |
| **V22–V23** | Wire not extended with callout | Shifted callout assembly but left wire at original length | Shift **entire wire-end assembly** together (wire + symbols + text) |

---

## Reusable Prevention Rules

### Rule 1: Content Verification Before Deletion

Before deleting any entity by handle:
```python
# Must verify at least two attributes match intent
assert entity.dxftype() in expected_types, f"Type mismatch: {entity.dxftype()}"
assert entity.dxf.text.strip() in expected_texts, f"Text mismatch: {entity.dxf.text!r}"
```

### Rule 2: Shift Entire Assembly, Not Individual Entities

When relocating a callout group that sits ON a wire:
```
Wire ──→ extend by Δx
  ├─ WFEND ──→ shift by Δx (preserve wire-end position)
  ├─ WECOIL ──→ shift by Δx
  ├─ LWPOLYLINE bracket ──→ shift by Δx
  ├─ Leader LINE ──→ shift by Δx
  ├─ TEXT tag ──→ shift by Δx
  └─ Dependent notes ──→ shift by Δx
```
Individual entity shifts → inconsistency → visual defects.

### Rule 3: Collision Margin ≥ 0.05 Units

For text-to-text overlap detection:
```python
def overlaps(a, b, margin=0.05):
    return not (a[2] < b[0] - margin or a[0] > b[2] + margin or
                a[3] < b[1] - margin or a[1] > b[3] + margin)
```

### Rule 4: Present Explicit Options, Never Auto-Correct

When collision detected:
> "Option A: Shift entire callout right by +0.5 x, extend wire, reposition symbols"
> "Option B: Shift callout below existing '2C SPARE' text"
> "Spot, which option do you prefer?"

---

## Files

| Version | DWG | Size | Key Change |
|---|---|---|---|
| V19 | `3_FINAL_v19.dwg` | 76,721 B | Initial T7 wire + CA-1452 callout |
| V20 | `3_FINAL_v20.dwg` | 76,368 B | Deleted wrong entities (5 removed) |
| V21 | `3_FINAL_v21.dwg` | 76,560 B | Reconstructed callout, wire-end symbols |
| V22 | `3_FINAL_v22.dwg` | 76,561 B | Callout shifted +0.5 x |
| V23 | `3_FINAL_v23.dwg` | 76,561 B | Callout shifted +1.0 x total |
| **V24** | **`3_FINAL_v24.dwg`** | **76,593 B** | **Wire extended, assembly unified** |
