# Port legacy QCAD-VLM-automation into cli-anything-qcad

Created: 2026-06-27
Source repo: `LIGHTSPEED1699/QCAD-VLM-automation`
Target repo: `LIGHTSPEED1699/cli-anything-qcad`

## What happened

User asked to "test pipeline on pairs 1, 2, 3". Initial results through `cli-anything-qcad` were poor because the scaffold backends were stubs that had not yet absorbed the legacy scripts. User then asked to port all legacy scripts into the harness.

## Files to port

| Legacy script | Target module | Purpose |
|---------------|---------------|---------|
| `scripts/dxf_entity_lookup.py` | `cli_anything/qcad/utils/dxf_entity_index.py` | TEXT/MTEXT/INSERT/DIMENSION index |
| `scripts/dxf_editor.py` | `cli_anything/qcad/backends/ezdxf_backend.py` | Real text replace, revision block, keyword delete |
| `scripts/pair1_fixed_executor.py` | `cli_anything/qcad/engines/cloud_deletion.py` or pipeline | Polygon annotation → TEXT/MTEXT clearing |
| `scripts/clone_pair3_v9.py` | `cli_anything/qcad/engines/terminal_clone.py` | Row-based terminal wire clone |
| `scripts/qcad_action_executor.py` | `cli_anything/qcad/backends/qcad_ecma_backend.py` | ECMAScript generation for move/add/block swap |
| `scripts/qcad_vlm_verifier.py` | `cli_anything/qcad/backends/vlm_x11_backend.py` | Launch QCAD, screenshot, VLM yes/no |
| `scripts/visual_verifier.py` | `cli_anything/qcad/utils/visual_verify.py` | Headless render, pixel diff, VLM semantic compare |
| `scripts/tier_router.py` | `cli_anything/qcad/core/categories.py` | Better rule-based classifier |

## Pipeline changes needed

1. PDF parser must extract **Polygon** annotations as clouds (done in session).
2. Each cloud annotation must be processed **individually** by its own vertices (done in session).
3. Router must distinguish:
   - deletion clouds → `CloudDeletionEngine`
   - circled objects → `CloudDeletionEngine` with Polygon annotation
   - clone/copy rows → `TerminalCloneEngine`
   - text changes → `EzdxfBackend` / `DXFEditor`
   - move/add → `QcadEcmaBackend` with real ECMAScript

## Test pairs

- Pair 1: `/media/RAIDARY1/WorkRAID1/QCAD_testfiles/pair1_2_3/1.dwg` + `1.pdf`
- Pair 2: `/media/RAIDARY1/WorkRAID1/QCAD_testfiles/pair1_2_3/2.dwg` + `2.pdf`
- Pair 3: `/media/RAIDARY1/WorkRAID1/QCAD_testfiles/pair1_2_3/3.dwg` + `3.pdf`

## Results before port

| Pair | Result | Notes |
|------|--------|-------|
| Pair 1 | 1.41% changed, 54 entities deleted | Worked after Polygon + per-task cloud fixes, but over-deleted boundary-touching terminals |
| Pair 2 | 0.17% changed, 5 deleted | Circled cloud deletion worked after routing fix |
| Pair 3 | 0% changed | Clone+text-change unsupported by scaffold |

## Pitfalls to avoid

- Do not assume the harness already implements the legacy logic.
- After each backend port, re-run the relevant pair before moving on.
- `dwg2bmp` is the only reliable headless renderer for verification.
- The `QcadEcmaBackend` stub only generated trivial scripts; real `qcad_action_executor.py` produces ECMAScript with entity handles and transformation matrices.
- `EzdxfBackend` stub did substring-only replacement; real `DXFEditor` uses `DxfEntityIndex` for handle-level edits and revision-block table heuristics.

## Status

Port in progress. Initial modules copied to `/tmp/cli-anything-qcad/cli_anything/qcad/backends/_ported/` and `ezdxf_backend.py` partially rewritten. Not yet committed or tested end-to-end.
