# VLM Visual Verification via QCAD Screenshot Pipeline

## Problem

Programmatic verification (entity counts, handle lists, text string checks) is **necessary but not sufficient** for DWG validation. A drawing can pass all programmatic gates while having geometric anomalies only visible in a CAD viewer. The user had to explicitly ask, *"did you run VLM vision verification script?"* — indicating this step was missing from the pipeline.

**Validated failure that programmatic verification missed:**
- Pair 3 V6: 0.40% pixel diff, 227→272 entities, all expected labels present
- But the user visually inspected and found the F174 ground-reference lines were missing
- The programmatic gate did not catch this because the lines were never part of the expected entity list

## Solution: QCAD → Screenshot → VLM Query

Use Geisterhand to open the DWG in QCAD, take a screenshot, and query an Ollama vision model (e.g. `qwen2.5vl`) with a natural-language question about the drawing.

## Pipeline Steps

1. **Kill old QCAD** (`pkill -9 -f qcad-bin`)
2. **Launch QCAD** with the DWG file (`qcad /path/to.dwg`)
3. **Wait** 5 seconds for drawing to load
4. **Focus window** (`xdotool windowactivate windowraise`)
5. **Zoom extents** (Ctrl+E in QCAD)
6. **Wait** 2 seconds for zoom to complete
7. **Screenshot** via ImageMagick `import -window <wid>`
8. **Query VLM** via Ollama `/api/chat` with base64-encoded PNG
9. **Parse yes/no** from VLM response
10. **Kill QCAD** (unless `--keep-qcad` requested)

## Script

`~/.hermes/skills/geisterhand-qcad/scripts/qcad_vlm_verifier.py`

Also synced to: `vlm-gui-automation/scripts/qcad_vlm_verifier.py` (GitHub repo)

## Environment Variables

```python
QCAD_BIN = "~/opt/qcad/qcad"
GEISTERHAND_URL = "http://127.0.0.1:7680"
OLLAMA_URL = "http://localhost:11434"
VISION_MODEL = "qwen2.5vl:latest"
```

## VLM Prompt Engineering

The prompt must be explicit about what the model is looking at:
```
You are examining an electrical CAD drawing (DWG file opened in QCAD).

QUESTION: Are the two short ground-reference lines on the right side of F174 present?

Please answer clearly: YES or NO, then explain what you see. Be specific about labels, lines, symbols, and their positions.
```

**Why this matters:** Without context, the VLM may describe the drawing generally rather than answering the specific question.

## Exit Codes

- `0` — VLM answered YES (or pass=True)
- `1` — VLM answered NO (or pass=False)
- `2` — Error (screenshot failed, VLM unreachable, etc.)

## Calibration Mode

```bash
python3 qcad_vlm_verifier.py drawing.dwg --calibrate
```

Launches QCAD, takes screenshot, exits without VLM query. Use this to:
- Verify QCAD renders the drawing correctly
- Check if labels/text are readable at current resolution
- Debug window detection or screenshot issues

## Limitations

1. **VLM cannot reliably read small text** (< 10pt at full screen resolution). Do not rely on VLM for precise label verification — use programmatic verification for that.
2. **VLM answers are probabilistic**. A "YES" today might be "UNCLEAR" tomorrow. Always combine with programmatic gates.
3. **Screenshot size**. A 3440×1440 ultrawide PNG is ~2–3 MB base64-encoded. This adds latency to the Ollama query.
4. **QCAD launch time**. First launch takes 5–8 seconds. Subsequent launches are faster if QCAD is already in memory.

## When to Use This

| Verification Need | Tool |
|-------------------|------|
| Entity count, handle list, text strings | Programmatic (ezdxf) |
| Labels exist at specific coordinates | Programmatic (ezdxf) |
| **Visual appearance** (lines present, shapes correct, no corruption) | **VLM screenshot** |
| User-requested "does this look right?" | VLM screenshot |
| Final human approval before delivery | TrueView/AutoCAD |

## VLM Accuracy Finding (2026-05-27)

Tested against V10 DWG (1_FINAL_v10.dwg) querying F174 ground-reference lines:

- **Core question accuracy:** VLM correctly answered **YES** — lines are present.
- **Detail hallucination:** VLM claimed labels are "24v" and "F73" — these are nearby TEXT entities, not line labels. Vision models cannot reliably read small CAD text at full-zoom resolution.
- **Programmatic cross-check:** ezdxf search within 2 units of F174 found:
  - LINE h=4B6D (horizontal, 0.236 units) — **kept in V10**
  - LINE h=4B6C (vertical, 0.244 units) — **kept in V10**
  - TEXT "F73" at (9.488, 9.025) — kept
  - TEXT "24v" at (9.496, 9.232) — kept
  - **Zero entities within 2 units of F174 were deleted in V10**

**Rule:** VLM visual verification is good for "are these lines present?" but bad for "what are their labels?" Always cross-check with programmatic entity search for precision.

## References

- `geisterhand-qcad/SKILL.md` — Geisterhand skill with full API docs
- `references/pair3-v6-visual-verification-gap.md` — Original incident that prompted this pipeline
- `scripts/qcad_vlm_verifier.py` — Production script
