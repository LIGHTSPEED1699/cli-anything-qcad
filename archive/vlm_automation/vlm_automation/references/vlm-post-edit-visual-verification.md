# VLM Post-Edit Visual Verification

## Overview
After any DXF/DWG edit, render the result to a screenshot and query a local vision model for validation. Catches errors invisible to programmatic checks (missing wire bends, misaligned clones, duplicate labels overlaying originals).

## Architecture
1. **ezdxf** reads DXF → **matplotlib** renders modelspace to PNG
2. PNG base64-encoded → `POST /api/generate` to local Ollama vision model
3. Domain-specific prompt asks VLM to compare terminal labels, wire routing, text changes
4. Verdict: `GOOD` / `NEEDS_WORK` / `ERROR`

## Usage
```bash
python3 scripts/vlm_verify_drawing.py \
    --dxf 3_cloned_v7_fixed.dxf \
    --original-dxf 3_clean.dxf \
    --model qwen2.5vl:latest \
    --ollama-url http://localhost:11434 \
    --prompt terminal_clone
```

Args:
- `--dxf`: Modified DXF to verify
- `--original-dxf`: Optional original for side-by-side
- `--model`: Ollama vision model (e.g. `qwen2.5vl:latest`, `llava:latest`)
- `--prompt`: `terminal_clone` or `side_by_side`
- `--json`: JSON-only output
- `--out-png`: Save rendered PNG

## Key Pitfalls

### Renderer Fidelity Gap
`ezdxf MatplotlibBackend` simplifies complex entities. Text that TrueView shows may not render. The VLM only judges what it sees. **#1 cause of VLM false negatives.**

### Missing Vision Model
If `qwen2.5vl` is not pulled (`ollama list`), the script fails. Always verify model availability first.

### Prompt Specificity
Generic prompts produce generic responses. Name exact terminal numbers, expected text changes, and known anomaly patterns. Built-in prompts cover terminal clone validation; extend for other patterns.

### Timeout
Vision models with `num_ctx=8192` take 30–60s per image on local GPU. Do not use short timeouts.

## Pair 3 Example (2026-05-15)
VLM verdict on V7: `GOOD` — no duplicate labels, clones positioned below originals, text changes visible (CA-1452, PLC22, -02). User (TrueView) later flagged a wiring error the ezdxf renderer dropped and the VLM couldn't see at default zoom.

**Lesson:** When VLM says GOOD but user says WRONG — zoom to region, increase DPI, and use side-by-side. Programmatic DXF + VLM visual is still not authoritative against TrueView.
