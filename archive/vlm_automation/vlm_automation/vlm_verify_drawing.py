#!/usr/bin/env python3
"""
VLM Visual Verification for CAD Drawings (Post-Edit)
=====================================================
Uses a local vision model (qwen2.5vl via Ollama) to visually inspect rendered
DXF/DWG screenshots and answer targeted questions about the drawing.

Architecture
------------
1. ezdxf reads DXF → ezdxf.addons.drawing.matplotlib renders to PNG
2. PNG is base64-encoded and sent to Ollama vision endpoint
3. VLM returns structured analysis (terminals, wires, labels, anomalies)
4. Verdict: GOOD / NEEDS_WORK / ERROR with explanation

Prerequisites
-------------
- Ollama running locally (default: localhost:11434)
- Vision model pulled (e.g. qwen2.5vl, llava)
- ezdxf + matplotlib installed

Usage Quick Test
----------------
    python3 vlm_verify_drawing.py \\
        --dxf 3_cloned_v7_fixed.dxf \\
        --original-dxf 3_clean.dxf \\
        --model qwen2.5vl:latest \\
        --ollama-url http://192.168.2.15:11434
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import requests


def _import_ezdxf():
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, matplotlib as mlb
    import matplotlib.pyplot as plt
    return ezdxf, Frontend, RenderContext, mlb, plt


def render_dxf_to_png(dxf_path, png_path, figsize=(24, 16), dpi=200):
    ezdxf, Frontend, RenderContext, mlb, plt = _import_ezdxf()
    doc = ezdxf.readfile(dxf_path)
    fig = plt.figure(figsize=figsize)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    ax.axis("off")
    ctx = RenderContext(doc)
    out = mlb.MatplotlibBackend(ax)
    frontend = Frontend(ctx, out)
    frontend.draw_layout(doc.modelspace(), finalize=True)
    fig.savefig(png_path, format="png", bbox_inches="tight", pad_inches=0.02, dpi=dpi)
    plt.close(fig)
    return os.path.getsize(png_path)


def render_side_by_side(dxf_orig, dxf_new, png_path, figsize=(32, 18), dpi=200):
    ezdxf, Frontend, RenderContext, mlb, plt = _import_ezdxf()
    fig, axes = plt.subplots(1, 2, figsize=figsize)
    for ax, dxf_file, title in zip(axes, [dxf_orig, dxf_new], ["ORIGINAL", "MODIFIED"]):
        doc = ezdxf.readfile(dxf_file)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(title, fontsize=14, fontweight="bold", pad=10)
        ctx = RenderContext(doc)
        out = mlb.MatplotlibBackend(ax)
        frontend = Frontend(ctx, out)
        frontend.draw_layout(doc.modelspace(), finalize=True)
    plt.tight_layout()
    fig.savefig(png_path, format="png", bbox_inches="tight", pad_inches=0.05, dpi=dpi)
    plt.close(fig)
    return os.path.getsize(png_path)


def vlm_analyze_image(png_path, prompt, model, ollama_url, timeout=120):
    with open(png_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False,
        "options": {"num_ctx": 8192, "temperature": 0.3}
    }
    r = requests.post(f"{ollama_url}/api/generate", json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return {
        "response": data.get("response", ""),
        "raw": data,
        "done": data.get("done", False),
        "eval_count": data.get("eval_count", 0),
        "eval_duration": data.get("eval_duration", 0)
    }


PROMPT_TERMINAL_CLONE_CHECK = """You are reviewing an updated electrical terminal wiring CAD drawing screenshot.

IMPORTANT CONTEXT:
- Original had terminals 3-6 with wires
- Update should CLONE wires from terminals 4-6 to new terminals 7-9
- Terminals 7,8,9 should ALREADY have terminal number labels
- Clone should NOT duplicate terminal labels at 7-9
- Expect text changes: CA-1451→CA-1452, PLC21→PLC22, drawing -01→-02

PAY SPECIAL ATTENTION to terminal rows 4-6 vs 7-9:
1. Are labels (4),(5),(6),(7),(8),(9) present? Any duplicates at 7-9?
2. Are 7-9 wire routings geometric clones of 4-6?
3. Are 7-9 positioned vertically below 4-6?
4. Visible text: CA-1452, PLC22, -02?

Verdict: GOOD / NEEDS_WORK / ERROR, with concise explanation."""

PROMPT_SIDE_BY_SIDE_TERMINAL = """You are comparing two versions of an electrical terminal wiring CAD drawing.
LEFT = Original diagram with terminals 3-6
RIGHT = Updated diagram that should have cloned wires from 4-6 to 7-9

COMPARE terminal rows 4,5,6 (LEFT) vs 7,8,9 (RIGHT):
1. Do RIGHT 7-9 have wire routing cloned from LEFT 4-6?
2. Are RIGHT 7-9 positioned below LEFT 4-6?
3. Any duplicate labels at 7,8,9?
4. Text differences: CA-1452, PLC22, -02?

Status: GOOD / NEEDS_WORK / ERROR, with 2-3 sentence explanation."""


def parse_verdict(text):
    for v in ["GOOD", "NEEDS_WORK", "ERROR"]:
        if v in text.upper():
            return v
    return "UNKNOWN"


def main():
    parser = argparse.ArgumentParser(description="VLM visual verification for CAD drawings")
    parser.add_argument("--dxf", required=True, help="Path to DXF file to verify")
    parser.add_argument("--original-dxf", help="Optional original DXF for side-by-side")
    parser.add_argument("--model", default="qwen2.5vl:latest", help="Ollama vision model")
    parser.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama base URL")
    parser.add_argument("--prompt", choices=["terminal_clone", "side_by_side"], default="terminal_clone")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--out-png", help="Save rendered PNG to path")
    parser.add_argument("--json", action="store_true", help="Emit JSON output only")
    args = parser.parse_args()

    png_path = args.out_png or "/tmp/vlm_verify_render.png"
    if args.original_dxf:
        size = render_side_by_side(args.original_dxf, args.dxf, png_path)
        prompt = PROMPT_SIDE_BY_SIDE_TERMINAL
    else:
        size = render_dxf_to_png(args.dxf, png_path)
        prompt_map = {
            "terminal_clone": PROMPT_TERMINAL_CLONE_CHECK,
            "side_by_side": PROMPT_SIDE_BY_SIDE_TERMINAL,
        }
        prompt = prompt_map.get(args.prompt, PROMPT_TERMINAL_CLONE_CHECK)

    result = vlm_analyze_image(png_path, prompt, args.model, args.ollama_url, args.timeout)
    text = result["response"].strip()
    verdict = parse_verdict(text)

    output = {
        "verdict": verdict,
        "response": text,
        "model": args.model,
        "render_size_bytes": size,
        "png_path": png_path,
        "eval_count": result.get("eval_count", 0),
        "eval_duration_ns": result.get("eval_duration", 0)
    }

    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print("=" * 70)
        print(f"VLM VERDICT: {verdict}")
        print("=" * 70)
        print(text)
        print("=" * 70)
        print(f"\nPNG: {png_path} ({size:,} bytes)")
        print(f"Model: {args.model}  |  Tokens: {result.get('eval_count', 0)}")

    sys.exit(0 if verdict == "GOOD" else 1)


if __name__ == "__main__":
    main()
