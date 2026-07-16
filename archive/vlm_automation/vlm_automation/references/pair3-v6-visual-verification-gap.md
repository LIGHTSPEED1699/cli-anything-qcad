# Pair 3 V6 Visual Verification Gap

**Date:** 2026-05-25 (session reset)
**Task:** Run Pair 3 terminal wire cloning (ezdxf dynamic discovery clone) on drawing 3
**Lesson:** Programmatic verification alone is insufficient for entity-cloning pipelines; VLM visual verification must be the second gate.

---

## What Happened

The user asked: *"check script in the skill as pair 3 has been completed in the past. this time we just want to run same modification again but on the dxf file directly."*

I executed the `pair3_pipeline_v6.py` pattern:
1. Discovered terminals T4/T5/T6 via `Wltermn` INSERT positions
2. Collected wire groups with `tol=0.2` y-tolerance bands
3. Cloned with per-terminal `dy` offsets
4. Replaced text: PLC21→PLC22, CA-1451→CA-1452, 02732→02733
5. Updated revision block: REV_DATE_3, REV_DESCR_3
6. Exported to DWG via QCAD headless

I verified programmatically:
- ✅ 39 entities cloned (T4→T7)
- ✅ 3 entities cloned (T5→T8)
- ✅ 3 entities cloned (T6→T9)
- ✅ Text "PLC22" and "CA-1452" found in target rows
- ✅ Revision block updated correctly

But I **did not** run a VLM visual verification step.

## User Correction

The user's very next message: *"did you run VLM vision verification acript?"*

Spelling error aside, this was a clear signal that the user expected visual verification as a standard post-action step. I had to admit I only did programmatic checks.

## The Two Repos / Two Verifiers Problem

When the user then asked *"Does the visual_verifier.py serve the purpose?"*, I discovered:

- **Skill** (`~/.hermes/skills/data-science/vlm-cad-automation/scripts/`): Contains `vlm_visual_verifier.py` — a simple 130-line script built on `ezdxf` matplotlib rendering + `qwen2.5vl` API calls. Render + prompt to VLM, print response.
- **Repo** (`~/.openclaw/workspace/vlm-gui-automation/`): Contains `visual_verifier.py` — the 564-line comprehensive version from the actual GitHub repo (`https://github.com/LIGHTSPEED1699/QCAD-VLM-automation`). This one supports pixel-level diffing, threshold scoring, side-by-side renders, multiple renderers (LibreCAD, QCAD, ODA, ImageMagick), and `PASSED/WARNING/FAILED` decision gates.

The skill directory has NO `.git` folder and is NOT connected to the remote repo. Changes in the repo do NOT propagate to the skill directory.

## Action Taken

- Patched SKILL.md with repo divergence warning
- Added "Post-Action Verification" section requiring visual gate after programmatic gate for cloning tasks
- Added this reference file

## Recommendations for Future Work

1. **For cloning tasks:** Always render the output DWG to PNG (ezdxf matplotlib backend or QCAD headless export) and run VLM analysis before declaring completion.
2. **For verification script choice:** Prefer `visual_verifier.py` (repo) when available — it has pixel-diff and structured decision gates.
3. **For skill maintenance:** Periodically diff `~/.openclaw/workspace/vlm-gui-automation/` against `~/.hermes/skills/data-science/vlm-cad-automation/` and port improvements.
4. **Specific Pair 3 prompt** (for future runs):
   ```
   Analyze this CAD drawing screenshot. Terminal rows 4,5,6 were cloned to 7,8,9.
   Check:
   1. Are terminal labels (4),(5),(6),(7),(8),(9) visible and not duplicated?
   2. Are PLC21/PLC22 and CA-1451/CA-1452 labels in correct rows?
   3. Any wire overlaps, missing connections, or text anomalies?
   4. Does the revision block show REV 3: 2026/05/04 IFR?
   ```
