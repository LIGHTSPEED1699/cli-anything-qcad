---
title: CLI-Anything QCAD
---

## Overview

CLI-Anything harness for QCAD. Applies PDF markups to a DWG file through a categorized, tier-routed pipeline and verifies the result visually.

## When to use

- You have a DWG file and a PDF print with markup annotations.
- You want an agent to interpret the markups, apply the edits deterministically, and verify the output.
- You want repeatable DWG modification workflows instead of one-off scripts.

## Commands

- `cli-anything-qcad apply <dwg> <pdf> [-o <out>] [--json]`
- `cli-anything-qcad dwg2dxf <dwg> <dxf>`
- `cli-anything-qcad dxf2dwg <dxf> <dwg>`
- `cli-anything-qcad parse <pdf> [--json]`
- `cli-anything-qcad render <dwg/dxf> --out <png>`

## Pipeline

1. Parse PDF annotations.
2. Convert DWG → DXF.
3. Classify each annotation into a modification category.
4. Route to backend tier:
   - T1 `ezdxf` for text/property edits and deletions.
   - T2 QCAD ECMAScript for moves/clones/reorders/adds/block swaps.
   - T3 ODA round-trip for DWG fidelity.
   - T4 VLM + X11 for ambiguous instructions.
5. Execute edits.
6. Render original + modified → pixel diff + VLM semantic verify.
7. Export verified DXF → DWG.

## Requirements

- Python 3.10+
- QCAD Professional for Linux or ODA File Converter
- `pip install -e .` installs the package

## Note

This is an early scaffold. Backend implementations are being ported from `QCAD-VLM-automation`.
