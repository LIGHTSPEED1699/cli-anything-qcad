# Wiring / Reference Line Exclusions Inside Cloud Polygons

**Session:** 2026-05-13 | **Drawing:** Pair 1 | **Version:** V10 → V11

## Problem

A strict point-in-polygon (PIP) cloud-deletion pipeline may remove LINE entities that geometrically fall inside a cloud polygon but are **functional drawing content**, not obsolete markup.

## Incident

In V10, an **L-shaped POLYLINE** on the right side of instrument label **F174** was deleted because it fell inside the **C2** (left-bottom) cloud polygon. This POLYLINE represents **electrical ground wiring** — a voltage reference showing that F174 is wired to ground.

- **Entity:** `POLYLINE` handle `4B6E`
- **Shape:** L-shape, 3 vertices (horizontal segment 0.22 + vertical segment 0.15 = **0.37 total length**)
- **Vertices:** `(9.17, 8.35) → (9.39, 8.35) → (9.39, 8.20)`
- **Context:** Instrument label F174 (TEXT handle `36FC` at `8.60, 8.28`)
- **Distance from F174:** 0.71 DXF units
- **User correction:** "Only thing I see wrongly deleted is the two short lines on the right side of F174, which should be kept on the drawing. These two lines are representing F174 is wired to electrical ground as voltage reference."
- **Action:** Removed handle `4B6E` from deletion list for **V11**.

## Root Cause

Geometric containment (PIP) is **necessary but not sufficient** for entities near instrument labels. Short L-shaped or straight LINE/POLYLINE entities adjacent to kept instrument labels are often **functional schematic content** (ground symbols, wiring stubs, signal references) that the PDF markup annotator never intended to delete. The cloud polygon may enclose them incidentally.

## Diagnostic Pattern

When reviewing deletion candidates near instrument labels, flag any entity that matches ALL of the following:
- **Type:** `LINE` or `POLYLINE` (open, not closed loop)
- **Short length:** Total path length `< 1.0` DXF unit
- **Proximity to kept label:** Within `1.0` DXF unit of a **kept** `TEXT` entity (F-label, T-label, loop number, etc.)
- **Shape:** Simple L-shape (`n=3` vertices, two orthogonal segments) or straight line (`n=2`)
- **Not a box:** Does not form a closed rectangle around the label (those are label boxes, distinct from wiring symbols)

## Mitigation Pattern

For any deletion pipeline operating on schematic / P&ID / electrical drawings:

1. **Post-PIP whitelist scan:** After point-in-polygon selection, iterate deletion candidates and remove any `LINE`/`POLYLINE` that satisfies the diagnostic pattern above.
2. **Context rule:** Any short line/L-shape whose nearest kept neighbor is an instrument label TEXT is a **wiring reference symbol** and must be preserved unless the user explicitly marks it for removal.
3. **Visual confirmation:** In pre-deletion overlays, render wiring-symbol candidates in **orange** (distinct from deletion-target red) so the user can flag false positives.
4. **Label-aware exclusion zone:** Maintain a `0.5` DXF-unit exclusion radius around all **kept** instrument labels; short lines inside this radius default to KEEP unless they are explicitly inside a strikethrough annotation.

## Cross-Reference

- Related: [Pitfall #92 — ground-reference L-shapes](SKILL.md#pitfall-92)
- Related: [Pitfall #93 — boundary-touching exclusion](SKILL.md#pitfall-93)
- Related: [Pitfall #91 — content-based text sweep](SKILL.md#pitfall-91)
- Related: [Pitfall #88 — bidirectional label-box matching](SKILL.md#pitfall-88)

## Files

- `1_handles_v10.json` — V10 deletion list (contains `4B6E`; removed for V11)
- `1_handles_v11.json` — V11 deletion list (104 handles, `4B6E` excluded)
