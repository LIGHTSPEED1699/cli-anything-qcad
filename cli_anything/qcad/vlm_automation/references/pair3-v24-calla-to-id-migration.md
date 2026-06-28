# Session 2026-05-23: Context Compaction Recovery & Skill Updates

## Summary
Session context exceeded limit and was reset mid-work. This doc captures the actual learnings from the compacted turns, not speculative content.

## 1. SKILL.md Version Bump (1.2.0 → 1.3.0)

Added milestone summary to `vlm-cad-automation/SKILL.md`:
- Pair 3 V24 accepted (terminal wire duplication)
- Pair 2 accepted (mixed-instruction pipeline)
- Pair 1 V11 complete (ground-reference false-positive patched)
- **Key architectural limit**: QCAD ODA export strips all BLOCK/ATTDEF data — title-block revision edits are structurally unsupported

## 2. Pitfalls.md — BLOCK Stripping Verdict Updated

Replaced the old "workaround for BLOCK edits" language (suggesting LibreDWG dxf2dwg or ODA File Converter) with a definitive verdict:

- **Option A (modelspace ATTRIB injection)** — fails: ATTRIBs visible in QCAD GUI but stripped on DWG export
- **Option B (full BLOCK reconstruction)** — fails: QCAD ODA export rebuilds BLOCKS from scratch, discarding reconstructed definitions
- **Verdict**: Title-block revision editing is a pipeline architectural boundary. Require DWG-native editor.
- **What the pipeline CAN do**: Modelspace text edits, geometry deletion, wire cloning, label shifts.

## 3. Reference Files for This Session

| Reference File | Status | Contains |
|---|---|---|
| `pair3-v24-calla-to-id-migration.md` | **REPLACED** (was wrong) | Previously contained fabricated CAD details; now this recovery doc |
| `revision-block-attdef-lesson.md` | Already existed | Why ATTDEF editing is unsupported |
| `dwg-block-attdef-structure-and-tag-extraction.md` | Already existed | Technical DXF BLOCK structure |

## 4. Kanban Skill — No Updates This Session

`kanban-worker` and `kanban-orchestrator` were loaded but not actively used in this session's work. No new dispatcher patterns discovered.

## 5. Hermes-Agent Skill — No Updates This Session

`hermes-agent` skill was loaded for context but no new CLI commands or config patterns emerged.

## Key Rule: When Context Compacts

If you generate a skill update from a compacted summary:
1. Verify the compacted facts against actual file state (check workspace files exist)
2. Do NOT invent specific entity handles, coordinate values, or version numbers unless you can verify them
3. Mark uncertainty explicitly: "from compacted context — verify before relying on"
4. Prefer updating the umbrella SKILL.md's high-level status over creating detailed reference docs from memory

## What Was Actually In Progress When Compacted

From the compaction summary:
- Pair 3 ATTRIB injection was abandoned (BLOCK stripping)
- Pair 1 V11 DWG was the last accepted deliverable
- User had not yet verified any new work after V11

All "V19–V24" references in hindsight are from **prior sessions** (May 13–21), not this session.
