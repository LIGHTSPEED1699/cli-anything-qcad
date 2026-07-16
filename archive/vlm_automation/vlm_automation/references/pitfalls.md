# VLM-CAD Pitfalls & Workarounds Quick Reference

Session-hardened knowledge from live Phase A/B pipeline builds.

## Tier Router

- `TierRouter.route(text)` returns `RouteResult` dataclass, NOT a raw `Tier` enum.
- Access tier via `result.tier == Tier.EZDXF`, not `result == Tier.EZDXF`.
- T4 (VLM+X11) triggers on: "rearrange", "reorder", "resize", "rotate", "explode", "fix", "correct", "adjust".

## DXF Editor

- **Always call `.load()` first**: `editor = DXFEditor(path); editor.load()`.
- Without `.load()`, `self.index` is `None` → `AttributeError: 'NoneType' object has no attribute 'search_exact'`.

## VLM Models

- **Warmup delay**: First Ollama call loads ~6GB model → 30–60s. Test scripts use `timeout=600`.
- **Auto-select logic**: `VLMClient.auto_select("vision")` checks VRAM budget (default 12GB × 0.85 headroom).
- **JSON extraction**: Models wrap JSON in ````json ... ````. `VLMClient._extract_json()` handles both markdown and raw blocks. Always check `parsed_json is not None`.

## Confidence Scoring

- Composite methods: `weighted_product` (default), `weighted_sum`, `min`.
- Human review triggered when ≥2 layers fail their thresholds.
- Fast path: single exact-match → skip VLM disambiguation.

## Environment Mismatch

- **Hermes venv**: ezdxf lives in `~/.hermes/venv/` (Python 3.11). System `python3` often lacks it.
  Use: `~/.hermes/venv/bin/python3 script.py`
- **LibreDWG bindings**: Compiled for pyenv 3.11.9. Match Python version exactly.
  Wrapper: `/usr/local/bin/libredwg-python`

## ODA File Converter

- `QT_QPA_PLATFORM=offscreen` **does NOT work**. Must use `xvfb-run`.
- Only batch mode (directories). One file per directory for single-file conversion.
- Headless-broken Qt6 GUI. AppImage needs FUSE or `--appimage-extract`.

## DXF Binary-Level Editing (LibreDWG Round-Trip)

When editing LibreDWG-generated DXFs that must round-trip back through `dxf2dwg`/`dwg2dxf`:

- **⚠️ Deleting entities breaks cross-references** — Removing an entity from the `ENTITIES` section leaves its handle referenced in the `OBJECTS` section. `dxf2dwg` fails with "Object handle not found" and aborts.
- **✅ Safe workaround: clear text instead** — Set group-code-1 (text content) to an empty string: `b'\r\n'`. The entity remains in DXF but becomes invisible. This preserves all handle references and passes `dxf2dwg` validation.
- **CRLF preservation is mandatory** — DXF uses `\r\n` line endings. Python's default line-separator logic silently converts them. Open file in text mode with `newline=''` and write in binary mode, or read lines with `str.splitlines(keepends=True)` instead of `f.readlines()`.
- **Group-code vs value trap** — A raw line `'  0\r\n'` is a **group code** (new entity starts). A line `'0\r\n'` is a **value** (e.g., layer name "0"). Code that checks `line.strip() == '0'` to detect entity boundaries will split at layer names, corrupting the entity block. Check for the space-padded group-code prefix: `line == '  0\r\n'` (not `line.strip() == '0'`).
- **Entity block length isn't fixed** — A TEXT entity may be 25+ lines (including `10`, `20`, `30`, `40`, `1`, `7`, `11`, `21`, `31`, `100` sub-entries). A naive `block[:4]` won't capture the text value. Always read until the next `line == '  0\r\n'`.
- **Empty group-code-1 values are valid for LibreDWG but rejected by ezdxf** — Setting a TEXT entity's text to empty string (`group_code_1_line + '\r\n'`) produces a DXF that `dxf2dwg` converts successfully, but `ezdxf.readfile()` rejects it with `Invalid group code "" at line N`. For the VLM-CAD pipeline, validate with LibreDWG round-trip, not ezdxf. If ezdxf inspection is required, replace empty text with `" "` (single space).
- **Python text-based clone data loss** — When inserting new entity blocks into a DXF, always use **concatenation** (`raw[:insert] + new + raw[insert:]`), never overwrite. A 51-entity clone that loses 55 KB of post-ENTITIES data means the insertion logic replaced instead of expanded the file.
  - **Guard:** `assert len(output_bytes) > len(input_bytes), "Data loss detected"`
  - **Duplicate handles:** After insertion, regex-scan for duplicate group-code-5 values. If found, strip ALL group-code-5 values from cloned blocks — QCAD ODA will reassign them on import.
  - **Insertion point:** Before the last `  0\r\nENDSEC\r\n` in the ENTITIES section, verified by checking that `ENDSEC` still follows later.

## Entity Duplication (Cloning)

- **T2/T3 contamination** — Broad y-range cloning (e.g. `y ∈ [19.0, 20.7]`) catches neighboring terminal rows. Identify terminal centers first, then use tight bands (±0.35 per terminal) or explicit exclusion lists.
- **Cable tag circles missed** — LWPOLYLINE circles around cable tags (e.g. `CA-1451`) often sit slightly above the terminal cluster and are missed by geometric filters. **Explicitly whitelist** these by handle (e.g. `9A79`) regardless of position.
- **Terminal label duplication** — Row labels like `(2)`, `(3)` in the source region get cloned to the destination, creating duplicates alongside native labels `(7)`, `(8)`. Exclude label TEXTs matching `^\(\d+\)$` or `^Terminal \d+$` from source handles.
- **Ground symbols inside clone regions** — Short ground-reference lines adjacent to instrument labels (e.g. near F174) represent electrical ground wiring, not annotation strokes. Preserve them unless explicitly requested for deletion.
- 🔷**Spatial collision check before placement** — When cloning callouts, cable tags, or symbols to a new x,y position, the automated pipeline currently has NO text-to-text collision detection. This caused the V21 CA-1452 tag to overlap the "TO DWG. B-SAR-280-02732" note at x=20.875. Before finalizing clone placement, scan existing TEXT/MTEXT entities within a bbox of ±0.6 units around the insertion point, sorted by y proximity. If any text entity is within 0.4 units vertically, present the user with explicit options (e.g., "Option A: shift right by +0.5 x" vs "Option B: shift down by -0.25 y"). Do not silently place overlapping text.
- **Shift entire assembly, not individual entities** — When relocating a callout group attached to a wire, extend the wire AND shift all dependent symbols (WFEND, WECOIL, bracket, leader, tag, notes) by the same Δx. Shifting one entity and not others causes the tag to float off the wire. See `references/pair3-v19-v24-iteration-log.md`.
- **Content verification before deletion** — Before deleting any entity by handle, verify at least TWO attributes (type + text content) match intent. In V20, deleting what was thought to be an empty bracket actually removed the CA-1452 TEXT (9A8E), leader LINE (9A8F), and LWPOLYLINE bracket (9A9O). An empty generic bracket (9A8A) was preserved instead. Always read entity content before deleting.

## QCAD ODA Export & BLOCK Section Stripping

- **QCAD ODA export strips BLOCK definitions** containing ATTDEF/ATTRIB data. A 314 KB original DWG becomes ~75 KB after QCAD export. This destroys title-block revision rows, sheet metadata, and block attribute defaults.
- **Verdict (V24, 2026-05-23):** No programmatic workaround exists via the DXF→QCAD pipeline. Both options failed:
  - **Option A (modelspace ATTRIB injection)** — confirmed experiment: 21 standalone ATTRIB instances (handles `9B40`–`9B54`, owner `47E7`) were injected into ENTITIES with correct tags. After QCAD ODA export: DWG grew only +1,377 bytes (76,531 → 77,908), `strings` found zero `REV_` tags. QCAD ODA rebuilds BLOCKS from ENTITIES geometry alone and discards all standalone ATTRIBs.
  - **Option B (full BLOCK reconstruction)** — inserting a complete BLOCK definition with ATTDEFs into DXF works in QCAD GUI, but QCAD ODA export rebuilds BLOCKS from scratch and discards the reconstructed definition.
- **Implication**: Title-block revision editing is a **pipeline architectural boundary**. If this is needed, the original DWG must be edited with a DWG-native editor (AutoCAD, full QCAD GUI, ODA SDK direct write) that does NOT round-trip through QCAD ODA headless export.
- **What the pipeline CAN do**: Edit visible text in ENTITIES (e.g. drawing number suffix `00002-01`→`00002-02` at handle `97B8`), geometry deletion, wire cloning, label shifts. Modelspace edits survive. Block-level metadata does not.
- See `references/revision-block-attdef-lesson.md` for complete analysis and `references/dwg-block-attdef-structure-and-tag-extraction.md` for technical details.

## Deletion Boundary Precision (Ground Symbols & Labels)

### Ground/Wiring Symbol Description Mismatch

When the user describes drawing elements in natural language, **a single DXF entity may be described as a group** and vice versa.

**Example from Pair 1 V11:**
- User said: *"two short lines on the right side of F174, which should be kept... representing F174 is wired to electrical ground"*
- DXF reality: **One** L-shaped POLYLINE with 3 vertices: `(9.17,8.35) → (9.39,8.35) → (9.39,8.20)`
- The user visually perceived two connected line segments (horizontal then vertical); DXF stored them as a single POLYLINE.

**Pattern to recognize:**
- "two short lines" near an F-label → single POLYLINE, n=3 vertices, `┐` or `└` shape
- "short lines around X" near instrument labels → single POLYLINE, n=3–5 vertices
- User describes *visual appearance*; DXF stores *entity topology*

**Mitigation:**
1. When user mentions "short lines" near a label, scan for L-shaped POLYLINEs (n=3) or Z-shaped POLYLINEs (n=4–5) within ~1.5 units of the label.
2. Check entity metadata: `n_vertices=3`, color=7 (white/bylayer), layer=0 — strong wiring-symbol signal.
3. Treat these descriptions as referring to the closest short POLYLINE/LWPOLYLINE, not separate LINE entities.

### Ground-Reference Lines Near Labels

Short vertical/horizontal lines adjacent to instrument labels (e.g. F174) represent electrical ground continuity, not annotation strokes. They often fall inside cloud polygons during vertex-level intersection tests.

**Whitelist rule:** Before finalizing handle lists, scan for short LINE/POLYLINE segments (length < 0.3) near kept labels and flag them for manual review. If the user confirms they are ground symbols, add them to a keep-whitelist by handle.

**Label text cluster protection:** When cloud polygons are drawn around the drawing area (not around specific entities), instrument labels (101–108, Tb703) and their F-label boxes may unintentionally fall inside the polygon. These must be identified by proximity to label text (y-spacing ≤ 0.15) and excluded from deletion lists even if their vertices test inside the cloud.

## Cloud Deletion Boundary Rules (V13-Hardened)

These rules were refined across V9→V13 iterations on live engineering drawings.

### Strict vs Expanded PIP Classification

| Test | Radius | Meaning | Action |
|------|--------|---------|--------|
| `contains_point(pt, radius=-0.08)` | Inward contraction | Strictly inside cloud interior | DELETE |
| `contains_point(pt, radius=+0.08)` | Outward expansion | On or near cloud boundary | REVIEW (likely KEEP) |
| Neither | — | Outside cloud | KEEP |

**Only entities that pass the strict inward test are unambiguously inside.** Boundary-touching entities (expanded catches them, strict does not) are **NOT deletion targets**.

### Wiring-Symbol Exclusion Rule

Short POLYLINE segments (n≤8 vertices, total length < 0.5 units, any standard color) located inside a cloud annotation may represent:

- Ground-reference wiring symbols
- Terminal jumpers
- Instrument reference connections

**There is NO reliable automated way to distinguish these from deletion targets.** The safe pipeline:

1. Generate a "review list" of all short POLYLINEs inside each cloud
2. Present handles + description to the user
3. Default to KEEP; delete only after explicit user approval

### Specific False Positive Patterns

| Pattern | Entity | Why Caught | Why Kept |
|---------|--------|-----------|----------|
| Ground L-shape | POLYLINE n=3, color=7 | Inside C1/C2 cloud interior | Wiring ground reference (e.g. F174→GND). User describes these as "two short lines on the right side of F174" representing electrical ground wiring. These are single L-shaped POLYLINE entities (horizontal + vertical segments, total length < 0.5 units) that appear visually as two connected short lines. |
| Arrow triangle | POLYLINE n=4, color=1 | Expanded PIP near C3 boundary | Callout arrow (instrument tag → power label) |
| Label text on edge | TEXT (e.g. "F194") | Expanded PIP on C3 max-y boundary | Instrument tag, not inside cloud |

### Verification Workflow

After generating a deletion list:

```python
from matplotlib.path import Path as MplPath

strict_inside = []
boundary_touching = []
outside = []

for e in candidate_entities:
    pt = entity_center(e)
    if cloud_poly.contains_point(pt, radius=-0.08):
        strict_inside.append(e.handle)
    elif cloud_poly.contains_point(pt, radius=+0.08):
        boundary_touching.append(e.handle)
    else:
        outside.append(e.handle)

# Delete ONLY strict_inside
# boundary_touching → user review list
# outside → keep
```

## Context Loss & Session Continuity

### Pitfall 88: Context Reset → Rebuild from Scratch

When a session resets due to context limit, the new context window may not include the proven pipeline state. The agent may:
1. Fail to load the relevant skill first
2. Rebuild deletion logic from scratch instead of using the proven pipeline or archived handle lists
3. Use a suboptimal fallback (LibreDWG `dxf2dwg`) instead of the proven QCAD headless pipeline
4. Produce a blank or corrupted deliverable

**Defense:**
- ALWAYS load `vlm-cad-automation` skill at session start after any reset
- Check `references/pair1-completion-status.md` — it says "DO NOT RESUME" if already complete
- Before regenerating any file, check the old workspace for an existing proven deliverable
- If QCAD headless worked yesterday but crashes today, debug why (lingering processes, display env) rather than abandoning the proven tool

**Validated:** 2026-05-24 — Pair 1 re-processed from scratch producing a 1.7MB blank R2004 DWG, when proven V11 (46KB, AC2010) already existed in old workspace.

### Pitfall 89: Trusting `file` Command Over Viewer Test

`file 1_FINAL.dwg` returning `DWG AutoDesk AutoCAD 2004/2005/2006` does NOT mean the DWG is valid. LibreDWG `dxf2dwg` writes syntactically valid but very old-format DWGs that modern viewers may render as blank. Always open in QCAD/AutoCAD to verify before delivering.

### Pitfall 90: Reusing Old Deliverables When Available

If a previous session produced a verified DWG, **copy it** rather than regenerate. Regeneration risk:
- Tool availability changes (QCAD worked yesterday, crashes today)
- Environment drift (DISPLAY, LD_LIBRARY_PATH)
- Random breakage (segfaults, timeouts)
- Format downgrade (R2010 → R2004)

Recovery: Old workspace `~/.hermes/kanban/workspaces/testfiles_2026.05.07/` contained `1_FINAL_v11.dwg` (46,785 bytes, AC2010, verified working). New session produced `1_FINAL.dwg` (1.7MB, R2004, blank).

## Integration Test Results (Expected)

- Phase A: 4/4 suites pass (tier_router, dxf_editor, visual_verifier, end-to-end)
- Phase B: 8/8 suites pass (VLM client, parser, disambiguator, scorer, queue, logger, verifier, e2e)

## Headless DWG Rendering — `dwg2bmp` is the Real Path (2026-06-04)

### Pitfall 91: matplotlib+ezdxf renders are 1-color blank for some DWGs

The previous fallback (matplotlib + ezdxf render of LibreDWG-converted DXF) produces a 2.2 KB single-color PNG for DWGs whose materials/MATERIAL objects are malformed (this is the norm for LibreDWG round-tripped files). The PNG passes the "image exists" check but is useless for VLM verification — the model sees a blank page and hallucinates.

**Validation date**: 2026-06-04, file `1_FINAL_v11.dwg` (46.8KB) → 2.2 KB blank PNG via matplotlib fallback, then 31 KB true-color PNG via `dwg2bmp` in 3 seconds.

**Fix**: Use QCAD's bundled `dwg2bmp` CLI. It's headless, no X11 required, accepts DWG directly, and produces the same true-color render you'd see in QCAD's GUI. See `references/dwg2bmp-headless-renderer.md`.

```bash
QCAD=/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64
LD_LIBRARY_PATH=$QCAD:$LD_LIBRARY_PATH $QCAD/dwg2bmp -f -a -o /tmp/out.png /path/in.dwg
```

**Note**: `dwg2bmp` accepts malformed DXF (e.g. those produced by `delete_entities_text.py` surgery, see Pitfall 92) that ezdxf refuses to read. The Qt-based renderer is more lenient than the strict ezdxf parser.

## Text-Based DXF Editing — Group Code Corruption (2026-06-04)

### Pitfall 92: `delete_entities_text.py` can leave malformed ENDSEC markers

When using text-based entity deletion (the `delete_entities_text.py` script), the surgery can leave stray `ENDSEC` markers without the leading `0` group code, breaking ezdxf's strict DXF parser:

```
ezdxf.lldxf.const.DXFStructureError: Invalid group code "ENDSEC
" at line 55137.
```

**Root cause**: When a SECTION is removed or inserted in the wrong place, the line-rewrite can lose the `0` group code prefix. Pattern in the broken file: `0\n  0\nENDSEC\n  0\nSECTION\n  2\n` — a stray `0` group code with no entity type.

**Workarounds** (in order of preference):
1. Render with `dwg2bmp` (accepts malformed DXF) instead of ezdxf+matplotlib
2. Convert DWG → DXF via LibreDWG `dwg2dxf` (more lenient parser) before ezdxf
3. Patch the missing `0` group code with Python text-rewrite: insert `  0\n` before any `ENDSEC` line that doesn't have a `  0` immediately preceding it

**Prevention** (long-term fix for `delete_entities_text.py`): track SECTION boundaries and explicitly write `0\nENDSEC\n` pairs. Don't rely on the input file's whitespace structure — explicitly emit group codes on every entity boundary.

**Validation date**: 2026-06-04, file `1_v10_deleted_fixed_plus_4b6e.dxf` (line 55137, broken after adding F174 ground line via text rewrite).

## VLM Model Selection (2026-06 Benchmark)

### Pitfall 93: qwen2.5vl hallucinates connections that don't exist

For CAD verification questions like "describe the lines connected to F174", `qwen2.5vl:latest` may invent plausible-but-nonexistent labels (e.g. it said "connects to C957" which doesn't exist in the drawing). The model sees shapes but lacks spatial precision to verify connections.

**Mitigation**:
- For binary yes/no questions, qwen2.5vl is reliable (5/5 pass in benchmark)
- For detail-description questions, prefer `gemma4:31b-cloud` (12.8× faster, no fabricated labels)
- For "list all labels" questions, qwen2.5vl may loop on the most common label — use explicit "if unclear, say 'unclear'" instruction

### Pitfall 94: gemma4:e4b is strictly worse than qwen2.5vl on RTX 3060 12GB

Smaller vision encoder (~150M params) misses CAD detail that qwen2.5vl's 675M encoder catches. gemma4:e4b returned FALSE negative on the F174 ground-reference question (the line IS present) and burned 584 output tokens producing a wrong answer.

**Mitigation**: Do not use gemma4:e4b for CAD verification. If you must use it, treat all answers with low confidence.

### Pitfall 95: Ollama Cloud pricing is GPU-time, not token-based

Ollama Cloud's "usage" metric is GPU-time, not tokens. The Pro plan ($20/mo) already includes 50× Free usage of Level 4 models like `gemma4:31b-cloud`. For VLM-CAD verification workloads (~500 calls/month), the cost is $0 marginal.

**Reference**: See `references/vlm-model-comparison-2026-06.md` for full benchmark.

**Configuration recommendation**: Update `vlm_client.py` picker chain to default `gemma4:31b-cloud` for vision tasks, with `qwen2.5vl:latest` as local fallback. Drop `gemma4:e4b` to last-resort.
