# F174 Ground-Reference Lines Verification Incident

## Incident (2026-05-27)

**User report:** "Only thing I see wrongly deleted is the two short lines on the right side of F174, which should be kept on the drawing. These two lines are representing F174 is wired to electrical ground as voltage reference."

**Drawing:** `1_FINAL_v10.dwg` (Pair 1 V10, 105 deletions, 1086 remaining entities)

## Investigation

### Step 1: Programmatic check via ezdxf
Searched all entities within 2 units of F174 TEXT (at 8.600, 8.279) in the V10 DXF (`1_v10_deleted_fixed.dxf`):

| Entity | Handle | Type | Length | Status in V10 |
|--------|--------|------|--------|---------------|
| F174 text | 36FC | TEXT | — | ✓ KEPT |
| Short horizontal line | 4B6D | LINE | 0.236 | ✓ KEPT |
| Short vertical line | 4B6C | LINE | 0.244 | ✓ KEPT |
| F73 label | 4062 | TEXT | — | ✓ KEPT |
| 24v label | 4061 | TEXT | — | ✓ KEPT |

**Result: Zero entities within 2 units of F174 were deleted in V10.**

### Step 2: QCAD screenshot + VLM verification
Launched QCAD with `1_FINAL_v10.dwg`, zoomed extents, screenshot, queried `qwen2.5vl`:

- **VLM answer:** "YES. The two short ground-reference lines on the right side of F174 are present."
- **VLM hallucination:** Claimed lines are labeled "24v" and "F73" — these are nearby TEXT entities, not line labels.

### Step 3: Cross-reference with deletion list
Checked V10 handle list (`1_handles_v10.json`) — neither 4B6C nor 4B6D appear in the 105 deleted handles.

## Root Cause

The lines were **never deleted**. The user's visual inspection (or TrueView zoom level) made the short lines appear missing. Possible causes:
- Lines are very short (0.24 units ≈ ~6mm at typical scale)
- At zoom-to-extents, small symbols may be below pixel visibility threshold
- User may have been looking at a different version or zoom level

## Lessons

### For Short Schematic Symbols
- Short LINE/POLYLINE entities (< 0.5 units) adjacent to kept labels are often **functional schematic content** (ground symbols, test points, junction dots), not obsolete markup.
- **Rule:** When a user reports "lines missing near label X", always run programmatic verification first before assuming a deletion bug. The lines may simply be hard to see at the current zoom.

### For VLM Verification Accuracy
- VLM correctly answered "lines present" (core question) ✓
- VLM hallucinated "labels are 24v and F73" (detail) ✗
- **Rule:** VLM visual verification is reliable for "are these lines present?" but unreliable for "what are their labels?" at full-zoom resolution.

### Verification Priority
1. **Programmatic gate first** — ezdxf entity search by position and handle list
2. **VLM gate second** — confirm visual presence (not label accuracy)
3. **User gate third** — ask user to zoom in to specific area before reporting deletion

## Entity Details

```
LINE h=4B6C
  start: (8.1389, 8.5983)
  end:   (8.1389, 8.3540)
  length: 0.2443
  direction: VERTICAL
  color: 7 (BYLAYER)

LINE h=4B6D
  start: (8.1389, 8.3540)
  end:   (8.3750, 8.3540)
  length: 0.2361
  direction: HORIZONTAL
  color: 7 (BYLAYER)
```

These two lines form an **L-shape** — classic ground reference symbol in electrical schematics.

## Related

- `references/wiring-reference-line-exclusions.md` — General rule for keeping short lines near instrument labels
- `references/vlm-visual-verification-qcad-screenshot.md` — QCAD→VLM pipeline accuracy findings
- `references/vlm-detail-hallucination-pitfall.md` — User-preference pitfall about VLM hallucination
- `references/pair1-v11-ground-wire-fix.md` — Previous ground-reference line incident (V11, different cause: actual deletion)
