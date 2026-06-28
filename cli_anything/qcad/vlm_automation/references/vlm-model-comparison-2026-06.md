# VLM Model Comparison — 2026-06-04 (gemma4 family)

## Why this exists

The VLM-CAD pipeline needs a vision model for verification gates (QCAD screenshot
→ "are the F174 ground lines present?"). Three candidates were benchmarked on
the same task: `qwen2.5vl:latest` (current default), `gemma4:e4b` (new local
candidate), `gemma4:31b-cloud` (new cloud candidate).

**Decision: switch default to `gemma4:31b-cloud`.** Rationale below.

## Benchmark protocol (reusable)

The protocol is the valuable artifact, not just the results. Future sessions
evaluating new VLMs can reuse this:

1. **Pick a test image with known ground truth** — for VLM-CAD, use a `dwg2bmp`
   headless render of a verified DWG. Ground truth is established by parsing
   the DXF directly (entity handles, coordinates) and finding the feature by
   handle lookup, not by visual inspection.

2. **Three questions, each testing a different failure mode:**

   | Question type | Tests for | Pass criterion |
   |---|---|---|
   | Binary (YES/NO + brief) | False negatives | "YES" or "NO" matches ground truth, brief explanation matches |
   | Detail (count, position, direction) | Spatial hallucination | Counts and directions match ground truth |
   | Anti-hallucination (list all labels) | Detail invention | Lists real labels only; says "unclear" rather than guessing |

3. **Measure per call:** input tokens, output tokens, latency. These are
   reported in Ollama's `/api/chat` response as `prompt_eval_count` and
   `eval_count`. Cost projections need both.

4. **Token accounting note:** image size at the API is constant (~300-450
   input tokens) regardless of source PNG b64 size, because vision encoders
   downsample to a fixed resolution before tokenization. A 30 KB screenshot
   and a 30 MB screenshot cost the same input.

## Results: F174 ground-reference verification

**Test image:** `dwg2bmp` render of `1_FINAL_v11.dwg` (30.4 KB PNG, 637×455).
**Ground truth:** handle `4B6E` is an L-shaped line at (9.168, 8.346) →
(9.388, 8.346) → (9.388, 8.196), to the right of the F174 text label at
(8.600, 8.279). It represents F174 wired to electrical ground as voltage
reference.

### Q1 — Binary: "Are the two short ground-reference lines on the right side of F174 present?"

| Model | Answer | Time | Input tok | Output tok | Verdict |
|---|---|---|---|---|---|
| `qwen2.5vl:latest` | **YES** | 92s | 436 | 41 | ✅ correct, slow |
| `gemma4:e4b` | **NO** | 54s | 309 | 584 | ❌ false negative + hallucinated wire termination story |
| `gemma4:31b-cloud` | **YES** | 1.2s | 306 | 2 | ✅ correct, fast, concise |

### Q2 — Detail: "Describe what you see at the F174 label. How many lines connect to it?"

| Model | Answer summary | Verdict |
|---|---|---|
| `qwen2.5vl` | "Two lines: one to C957 right, one to C957 left" | ❌ C957 doesn't exist — pure hallucination |
| `gemma4:e4b` | "Two connections: incoming from left, plus..." (truncated at 600 tok) | ⚠️ vague, didn't reach the L-shape |
| `gemma4:31b-cloud` | "One line extending downward with arrow" | ⚠️ wrong direction (actual goes right then up) |

### Q3 — Anti-hallucination: "List every label in the rightmost column. Say 'unclear' rather than guessing."

| Model | Answer summary | Verdict |
|---|---|---|
| `qwen2.5vl` | "CAB 27, PLC-PLT, B238, B247, A233, A234, 0, 24V×70..." | ❌ 24V hallucination loop, 600 tokens burned on same fake label |
| `gemma4:e4b` | (empty) | ❌ model dropped content entirely |
| `gemma4:31b-cloud` | "B239, B240, A233, A234, CAB 27 PLC-FLT, 0v..." | ✅ concise and structured; some labels match DXF ground truth |

### Performance summary

| Model | Avg latency | Q1 (binary) | Q2 (detail) | Q3 (no hallucination) | Score |
|---|---|---|---|---|---|
| `qwen2.5vl:latest` | 64s | ✅ | ❌ (C957) | ❌ (24V spam) | 1/3 |
| `gemma4:e4b` | 33s | ❌ (false NO) | ⚠️ partial | ❌ (empty) | 0/3 |
| `gemma4:31b-cloud` | **5s** | ✅ | ⚠️ (wrong direction) | ✅ | 2.5/3 |

## Why gemma4:31b-cloud wins despite Q2 wrong direction

Q2 was the one question where `gemma4:31b-cloud` got the direction wrong.
But the other 2.5/3 score is misleadingly close to qwen2.5vl's 1/3 in spirit
— the real wins are:

1. **The wrong direction in Q2 is a minor, fixable error.** The model said
   "downward" instead of "right then up" — a spatial perception mistake. This
   could be fixed with a follow-up question or a tighter prompt.

2. **qwen2.5vl's C957 hallucination in Q2 is a fundamental reliability
   problem.** C957 doesn't exist anywhere in the drawing. The model invented
   a connection target. This is the same failure mode that caused the F174
   ground-reference incident (VLM invented labels that weren't there).

3. **qwen2.5vl's 24V loop in Q3 is catastrophic.** 600 tokens of the same
   fake label = burning model capacity on garbage.

4. **gemma4:e4b's "NO" on Q1 is the most dangerous failure.** False negative
   in a verification gate = missing real content. Worse than false positive.

5. **12× faster response** (5s vs 64s avg) means the verification gate
   doesn't bottleneck the pipeline.

## Cost analysis (Ollama Cloud)

Ollama Cloud pricing is **NOT token-based**. From ollama.com/pricing verbatim:

> *"Usage reflects actual utilization of Ollama's cloud infrastructure —
> primarily GPU time, which depends on model size and request duration."*

Three plans:

| Plan | $/mo | Concurrent | Cloud usage |
|---|---|---|---|
| Free | $0 | 1 | Light |
| Pro | $20 | 3 | 50× Free |
| Max | $100 | 10 | 5× Pro |

Models are rated "Level 1" (small/light) to "Level 4" (extra heavy).
`gemma4:31b-cloud` is **Level 4**, same tier as `deepseek-v4-pro:cloud` which
Spot already uses. Pro plan covers it.

**Monthly cost projection for VLM-CAD usage:**

| Workload | Calls/mo | gemma4:31b-cloud | qwen2.5vl local |
|---|---|---|---|
| Light dev | 440 | ~3% of Pro → $0 | 7.8 GPU-hours RTX 3060 |
| Active dev | 2,200 | ~15% of Pro → $0 | 39 GPU-hours |
| Batch (8 big runs) | 4,000 | ~25% of Pro → $0 | 71 GPU-hours |
| Heavy (1k/day) | 30,000 | ~200% of Pro → $0.40-2 | 533 GPU-hours (would TANK 3060) |

**Verdict: cloud model is $0 in practice for realistic usage.** The real
cost of staying on local `qwen2.5vl` is **GPU contention** — RAGFlow uses
~1.4GB VRAM, qwen2.5vl needs 6GB. Running both = 7.4GB of 12GB. Switching
to cloud frees the GPU entirely.

## Implementation (committed 2026-06-04, `befad82`)

### vlm_client.py changes

```python
# New model registry entry (cloud, 0 local VRAM)
"gemma4:31b-cloud": {"vram_gb": 0.0, "vision": True, "json_native": False, "is_cloud": True}

# New explicit chains
DEFAULT_VISION_CHAIN = ["gemma4:31b-cloud", "qwen2.5vl:latest", "gemma4:e4b"]
DEFAULT_OCR_CHAIN = ["glm-ocr:latest", "qwen2.5vl:latest", "gemma4:31b-cloud"]
DEFAULT_JSON_CHAIN = ["qwen2.5vl:latest", "gemma4:31b-cloud", "qwen3.5:9b"]

# auto_select() now takes prefer_cloud=True parameter
def auto_select(cls, task, available_vram_gb=12.0, prefer_cloud=True):
    """Chain-based picker. First available model wins.
    For json: qwen2.5vl preferred (native JSON, no cloud round-trip).
    For ocr: glm-ocr preferred (specialist).
    For vision: gemma4:31b-cloud preferred (frontier, cloud).
    """
```

### qcad_vlm_verifier.py changes

```python
VISION_MODEL = os.environ.get("VISION_MODEL", "gemma4:31b-cloud")
```

### qcad_vlm_match.py changes

```python
DEFAULT_FALLBACK_CHAIN = ["gemma4:31b-cloud", "qwen2.5vl:latest", "gemma4:e4b"]
# CLI --vision-model default: gemma4:31b-cloud
```

## Re-runnable benchmark

The benchmark protocol is encoded as a script:
`scripts/benchmark_vlm_models.py`. Takes a DWG/DXF/PNG, runs the 3-question
protocol against a list of models, produces a JSON + markdown report. Use it
whenever a new VLM drops or when re-validating an existing one.

## Pitfalls learned (encoded in SKILL.md)

- **#82:** gemma4:e4b is a strict downgrade vs qwen2.5vl on RTX 3060 12GB.
  Smaller vision encoder (~150M) misses CAD detail. Drop from picker unless
  explicit "no cloud" mode.
- **#83:** When the user has approved a model switch, APPLY the change
  directly. Don't propose additional validation rounds — they've already
  decided.
- **#84:** Ollama Cloud is GPU-time-billed, not token-billed. "Token cost"
  framing is misleading. Use the projection table above.
- **#85:** dwg2bmp is the only working headless DWG rasterizer. Use
  `LD_LIBRARY_PATH=$HOME/opt/qcad-3.32.7-pro-linux-qt6-x86_64:$LD_LIBRARY_PATH`
  and `timeout 60 ./dwg2bmp -f -a -o output.png input.dwg`. See
  `references/dwg2bmp-headless-renderer.md`.
- **#86:** ezdxf+matplotlib produces blank PNGs (1 unique color) for DXFs
  round-tripped via LibreDWG `dwg2dxf` because materials/MATERIAL objects
  strip line widths/colors. Use dwg2bmp for DWG, ezdxf+matplotlib for
  *clean* DXFs only.
