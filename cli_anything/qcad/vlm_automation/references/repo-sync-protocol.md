# Bidirectional Repo Sync Protocol

Validated workflow for keeping a Hermes skill directory in sync with its GitHub remote clone.

## Problem

Hermes skills live under `~/.hermes/skills/` and are NOT git repositories. The actual GitHub clone may be elsewhere (e.g., `~/.openclaw/workspace/`). After bidirectional sync, post-sync drift accumulates in the skill dir as new scripts and reference docs are created during sessions.

## Sync Workflow

### 1. Identify drift
```bash
cd /path/to/github/clone
git status --short
git diff --stat
```

### 2. Copy skill-unique files into clone
```bash
cp ~/.hermes/skills/<category>/<skill-name>/scripts/<new_script>.py \
   /path/to/github/clone/scripts/
cp ~/.hermes/skills/<category>/<skill-name>/references/<new_doc>.md \
   /path/to/github/clone/references/
```

### 3. Update README.md in clone
Edit `README.md` in the GitHub clone (not the skill dir — the skill dir has no README by convention). Cover:
- Current status of all delivered work (versions, entity counts, file sizes)
- Pipeline descriptions with script names
- Known Issues table (consolidated, no duplicates)
- File Map (all root scripts + `scripts/` + `references/`)
- Prerequisites and coordinate mapping
- Key Learnings (numbered, derived from resolved issues)

### 4. Copy updated README back to skill dir
```bash
cp /path/to/github/clone/README.md \
   ~/.hermes/skills/<category>/<skill-name>/README.md
```

### 5. Commit and push
```bash
cd /path/to/github/clone
git add -A
git commit -m "Sync README, SKILL.md, and new refs from skill dir (YYYY-MM-DD)"
git push origin main
```

## Pitfalls

- **Do NOT edit README in skill dir first** — the skill dir is not a git repo, so changes there can't be committed. Edit in the clone, push, then sync back.
- **Do NOT create duplicate rows** in Known Issues — if an older row covers similar territory, consolidate and replace.
- **Name all tools precisely** — "ODA File Converter" (standalone) vs "QCAD ODA" (authoring) vs "LibreCAD" (also ODA-based). They behave differently.
- **Verify after push** — `git log --oneline -1 origin/main` should match local HEAD.

## Reference
- Applied to `LIGHTSPEED1699/QCAD-VLM-automation` on 2026-05-26 (commits `2a70886`, `e330062`, `23f6620`).
