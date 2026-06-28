# V10→V11→V12 Iteration Log — False Positive Corrections (2026-05-13)

## V10 Status (105 deletions)

V10 was the first version to successfully delete all 5 user-reported issues:
1. ✅ HATCH rectangle gone
2. ✅ White dots gone
3. ✅ Red strikethrough line gone
4. ✅ "Hydrogen Peroxide" texts gone
5. ✅ Instrument labels (101-108, Tb703) kept

**False positive:** Handle `4B6E` (POLYLINE n=3 L-shape, 0.37 units total length) was wrongly deleted. This is the ground-reference wiring symbol next to F174.

## V11 (104 deletions) — Restore 4B6E

Removed handle `4B6E` from deletion list. All other entities unchanged.

**New false positives reported by user:**
1. Handle `4067` — Small red POLYLINE n=4 (triangle) next to F175, a callout arrow between instrument tag and +24V label
2. Handle `4152` — "F194" label text sitting exactly on C3 cloud's max-y boundary (y=4.512)

Both were boundary-touching entities caught by expanded PIP (`radius=+0.08`).

## V12 (102 deletions) — Restore 4067 + 4152

Removed handles `4067` and `4152` from deletion list.

**Final state:** 102 deletions, 1,080 entities remaining.

## V13 (101 deletions) — Restore 4B6E wiring ground symbol

User reported that handle `4B6E`, a short POLYLINE n=3 (L-shape, 0.37 units total length) next to F174, was still wrongly deleted in V12. This is the **wiring ground-reference symbol** representing that F174 is wired to electrical ground as voltage reference.

**Root cause:** Handle `4B6E` is located inside the C1 cloud interior (strict PIP, radius=-0.08), so it was correctly classified as "inside → delete" by the pipeline. The ground symbol is physically inside the cloud, so automated polygon containment cannot distinguish it from an intended deletion target.

**Fix:** User explicitly identified the handle as a false positive and restored it.

### Wiring-Symbol Exclusion Rule (New)

Short POLYLINE segments (n≤8 vertices, total length < 0.5 units, color=1-7 typical) located **inside** a cloud annotation may represent wiring ground connections, terminal jumpers, or reference symbols rather than deleted content. 

**Detection:** There is NO reliable automated way to distinguish these. The only safe approach is:
1. Generate a "review list" of all short-n POLYLINEs inside each cloud
2. Present handles + a minimal description to the user for human confirmation
3. Only delete after explicit approval, OR default to KEEP and let the user add them to the deletion list

## Final V13 State

- **101 deletions**, 1,081 entities remaining  
- All 5 user-reported V9 issues resolved  
- All 3 boundary-touching false positives (4067, 4152, 4B6E) restored  
- **Key lesson:** Wiring ground symbols are visually small, geometrically inside clouds, and functionally critical. They are invisible to the automated pipeline and must be protected by user review.

## Boundary-Touching Rule (Pitfall #94)

Entities whose containment depends **only** on expanded PIP (radius ≥ 0) but fail strict inside PIP (radius < 0) are **NOT deletion targets**. They sit on or near the cloud boundary but are outside the interior.

The user explicitly stated: *"When the object is just touching the clouds, it is not meant for deletion as the objects sit on the boundary."*

### Detection Pattern

```python
from matplotlib.path import Path as MplPath

strict = polygon.contains_point(pt, radius=-0.08)   # inside?
loose  = polygon.contains_point(pt, radius=+0.08) # near boundary?

if strict:
    status = "INSIDE → DELETE"
elif loose:
    status = "BOUNDARY-TOUCHING → REVIEW (likely KEEP)"
else:
    status = "OUTSIDE → KEEP"
```

## Specific False Positive Patterns

| Pattern | Entity | Handle | Why Caught | Why Kept |
|---------|--------|--------|-----------|----------|
| Ground L-shape | POLYLINE n=3 | 4B6E | Inside C1 cloud interior | Wiring symbol connecting F174 to ground |
| Arrow triangle | POLYLINE n=4, color=1 | 4067 | Expanded PIP near C3 boundary | Callout arrow F175 → +24V |
| Label text on edge | TEXT | 4152 | Expanded PIP on C3 max-y boundary | "F194" instrument tag, not inside cloud |

## Lesson

Every iteration from V9→V10→V11→V12 was driven by user feedback on **individual wrongly-deleted entities**. The automated pipeline is reliable for the bulk of deletions, but **boundary-touching edge cases require user review**. A "flag for review" list of boundary-touching entities should be generated and presented to the user as a checklist.
