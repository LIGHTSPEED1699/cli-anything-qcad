# User-Preference Pitfall: VLM Detail Hallucination

## Observation (2026-05-27)

When the VLM (qwen2.5vl) was queried about F174 ground-reference lines in a QCAD screenshot, it answered correctly about line presence but hallucinated detail:

- **Correct:** "The two short ground-reference lines on the right side of F174 are present."
- **Incorrect:** "These lines are red and are labeled as '24v' and 'F73'."

The lines are actually color=7 (BYLAYER, white/black) and have no labels. The VLM conflated nearby TEXT entities ("24v" at 9.496, 9.232 and "F73" at 9.488, 9.025) with the line labels.

## Root Cause

Vision-language models at 3440×1440 full-zoom resolution cannot reliably:
1. Associate small geometric elements (0.24-unit lines) with nearby text labels
2. Distinguish between line color and text color at distance
3. Read 10pt CAD text with confidence

## User Impact

The user did NOT complain about this hallucination, but it demonstrates that VLM verification requires careful prompt engineering and cross-checking. If the user had asked "what color are the lines?" the VLM would have given wrong information.

## Mitigation

1. **Ask presence questions, not detail questions** — "Are the lines present?" not "What are their labels?"
2. **Always cross-check with programmatic search** — ezdxf entity positions are ground truth
3. **If detail matters, zoom in before screenshot** — use QCAD zoom window (Ctrl+Z, specify coordinates) to focus on the area of interest
4. **Ask VLM about relative position, not absolute labels** — "Do you see two short lines near F174?" not "What labels do the lines have?"

## Prompt Template

```
You are examining an electrical CAD drawing (DWG file opened in QCAD).

QUESTION: Are the two short ground-reference lines on the right side of F174 present?

Please answer: YES or NO. Then briefly describe what you see near F174.
Do NOT guess labels or colors. Only describe what is clearly visible.
```

## Related

- `references/vlm-visual-verification-qcad-screenshot.md` — Full QCAD→VLM pipeline
- `references/f174-ground-reference-verification.md` — Specific incident
- `references/vlm-visual-verification.md` — General VLM verification pitfalls
