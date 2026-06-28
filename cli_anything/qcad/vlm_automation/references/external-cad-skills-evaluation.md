# External CAD Skill Marketplace Evaluation

## Quick Reference (2026-05-14)

The agent skill marketplace (e.g. `agent-skills-cli`, SkillsMP) lists many "CAD" skills, but **almost all are generative/parametric CAD toolchains** built on `build123d`, `OCP` (OpenCascade), and `step.parts`. They generate new STEP/STL/DXF parts from natural-language prompts. They are **not** tools for editing legacy DWG/DXF markup, title blocks, or PDF-annotated drawings.

### Representative Example: `earthtojake/text-to-cad`

| Aspect | Their Pipeline | Our VLM-CAD Pipeline |
|--------|---------------|----------------------|
| Input | Prose description of a new part | PDF with cloud/strikethrough annotations |
| Core library | `build123d` (OpenCascade Python) | `ezdxf` (read-only), text-based DXF editing, QCAD ODA |
| Primary output | STEP, STL, DXF, 3MF | Clean DWG with entities deleted per PDF markup |
| DXF role | Secondary 2D projection of 3D geometry | Primary document for entity manipulation |
| Block/title block handling | N/A (new parts have no title blocks) | Critical — QCAD ODA strips BLOCK data |
| Installation | `npx agent-skills-cli add <skill>` for Cursor/Claude/Codex | Hermes native skill in `~/.hermes/skills/` |

### Fast Relevance Check

Before installing any external CAD skill, run:

```bash
curl -sL "https://raw.githubusercontent.com/<owner>/<repo>/main/README.md" | head -n 50
curl -sL "https://raw.githubusercontent.com/<owner>/<repo>/main/skills/cad/SKILL.md" | head -n 30
```

Look for these keywords:
- **Generative**: "Create CAD models from natural language", "STEP-first", "build123d", "parametric"
- **Markup editing**: "Delete entities", "PDF annotation", "DWG cleanup", "cloud/strikethrough"

If the skill mentions `build123d`, `step.parts`, `OCP`, or `URDF`, it is generative CAD. It will not help with legacy DWG/DXF review workflows.

### `agent-skills-cli` vs Hermes

- `agent-skills-cli` installs to `${CODEX_HOME}`, `${CLAUDE_SKILLS_DIR}`, etc. for **Cursor, Claude Code, GitHub Copilot, OpenAI Codex**.
- Hermes skills live in `~/.hermes/skills/` and use a YAML-frontmatter SKILL.md format.
- Even if an external skill is relevant, it cannot be consumed by Hermes without manual porting (reformatting SKILL.md, rewriting script paths, etc.).

### Verdict for VLM-CAD Pipeline

External marketplace CAD skills add **zero value** to PDF→DXF→DWG markup processing. They are for greenfield mechanical design, not brownfield drawing review. Keep this reference handy to quickly dismiss "have you tried skill X?" suggestions in future sessions.
