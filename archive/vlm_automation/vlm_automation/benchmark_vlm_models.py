#!/usr/bin/env python3
"""
benchmark_vlm_models.py — Re-runnable VLM benchmark for CAD verification.

Implements the 3-question protocol from references/vlm-model-comparison-2026-06.md
against any number of Ollama models on any input image (DWG/DXF/PNG).

Usage:
    # Default: 3-question protocol, 3 models, v11 DWG
    python3 benchmark_vlm_models.py

    # Custom models and image
    python3 benchmark_vlm_models.py \\
        --image /path/to/drawing.dwg \\
        --models qwen2.5vl:latest gemma4:31b-cloud \\
        --ground-truth "The F174 ground-reference L-shape is at handle 4B6E"

    # Custom questions
    python3 benchmark_vlm_models.py \\
        --image drawing.png \\
        --question "Is X present?" \\
        --question "Describe Y" \\
        --question "List all Z"

Output:
    JSON file with full results (default: /tmp/vlm_benchmark.json)
    Markdown summary (default: /tmp/vlm_benchmark.md)

The protocol is described in detail in:
    ~/.hermes/skills/data-science/vlm-cad-automation/references/vlm-model-comparison-2026-06.md
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


# Default protocol: the 3 questions from the F174 benchmark
DEFAULT_QUESTIONS = [
    {
        "id": "Q1_binary",
        "text": "Are the two short ground-reference lines (L-shaped or short vertical/horizontal segments) on the right side of the F174 label present? Answer YES or NO first, then explain briefly."
    },
    {
        "id": "Q2_detail",
        "text": "Describe what you see at the F174 label. How many lines connect to it, and where do they go? Be specific about which direction each line extends."
    },
    {
        "id": "Q3_anti_hallucination",
        "text": "List every instrument terminal label and every text label you can read in the rightmost column of the drawing. If a label is unclear, say 'unclear' rather than guessing. Do not invent labels."
    }
]

DEFAULT_MODELS = ["qwen2.5vl:latest", "gemma4:e4b", "gemma4:31b-cloud"]
DEFAULT_OLLAMA = "http://localhost:11434"
DEFAULT_IMAGE = "~/.hermes/kanban/workspaces/testfiles_2026.05.07/1_FINAL_v11.dwg"
DEFAULT_OUT_JSON = "/tmp/vlm_benchmark.json"
DEFAULT_OUT_MD = "/tmp/vlm_benchmark.md"


def render_dwg_to_png(dwg_path: Path, png_path: Path, timeout: int = 60) -> Path:
    """Headless DWG/DXF → PNG using QCAD's dwg2bmp.

    See references/dwg2bmp-headless-renderer.md for details.
    """
    qcad_dir = os.path.expanduser("~/opt/qcad")
    env = {**os.environ, "LD_LIBRARY_PATH": f"{qcad_dir}:{os.environ.get('LD_LIBRARY_PATH','')}"}
    # Kill any lingering QCAD instances
    subprocess.run(["pkill", "-9", "-f", "qcad"], capture_output=True)
    time.sleep(1)
    result = subprocess.run(
        ["timeout", str(timeout), f"{qcad_dir}/dwg2bmp",
         "-f", "-a", "-o", str(png_path), str(dwg_path)],
        env=env, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"dwg2bmp failed (exit {result.returncode}): {result.stderr[:500]}")
    if not png_path.exists() or png_path.stat().st_size < 1000:
        raise RuntimeError(f"dwg2bmp produced empty/blank PNG: {png_path} "
                           f"({png_path.stat().st_size if png_path.exists() else 0} bytes)")
    return png_path


def query_ollama(model: str, image_b64: str, question: str, ollama_url: str,
                 max_tokens: int = 600, timeout: int = 300) -> dict:
    """Send a vision query to Ollama. Returns dict with answer, tokens, timing."""
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": question,
            "images": [image_b64]
        }],
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0.2}
    }
    req = urllib.request.Request(
        f"{ollama_url}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
        return {
            "model": model,
            "elapsed_s": time.time() - t0,
            "answer": result.get("message", {}).get("content", ""),
            "prompt_eval_count": result.get("prompt_eval_count", 0),
            "eval_count": result.get("eval_count", 0),
            "total_tokens": result.get("prompt_eval_count", 0) + result.get("eval_count", 0),
            "done": result.get("done", False),
            "error": None
        }
    except Exception as e:
        return {
            "model": model,
            "elapsed_s": time.time() - t0,
            "answer": "",
            "prompt_eval_count": 0,
            "eval_count": 0,
            "total_tokens": 0,
            "done": False,
            "error": str(e)[:500]
        }


def parse_yes_no(answer: str) -> str:
    """Extract YES/NO from the first 50 chars of the answer."""
    head = answer.lower()[:50]
    if "yes" in head:
        return "YES"
    if "no" in head:
        return "NO"
    return "UNCLEAR"


def main():
    parser = argparse.ArgumentParser(
        description="Re-runnable VLM benchmark for CAD verification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE,
                        help=f"Input image (DWG/DXF/PNG) (default: {DEFAULT_IMAGE})")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                        help=f"Models to benchmark (default: {' '.join(DEFAULT_MODELS)})")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA,
                        help=f"Ollama API URL (default: {DEFAULT_OLLAMA})")
    parser.add_argument("--question", action="append", default=None,
                        help="Override default questions (repeatable)")
    parser.add_argument("--max-tokens", type=int, default=600,
                        help="Max output tokens per question (default: 600)")
    parser.add_argument("--out-json", default=DEFAULT_OUT_JSON,
                        help=f"Output JSON path (default: {DEFAULT_OUT_JSON})")
    parser.add_argument("--out-md", default=DEFAULT_OUT_MD,
                        help=f"Output markdown path (default: {DEFAULT_OUT_MD})")
    parser.add_argument("--keep-png", action="store_true",
                        help="Keep the rendered PNG (don't delete after benchmark)")
    parser.add_argument("--png-path", default=None,
                        help="Reuse existing PNG instead of re-rendering (skips dwg2bmp)")
    parser.add_argument("--ground-truth", default="",
                        help="Optional ground truth statement to include in report")
    args = parser.parse_args()

    # Step 1: Resolve image to PNG
    img_path = Path(args.image)
    if not img_path.exists():
        print(f"ERROR: image not found: {img_path}", file=sys.stderr)
        sys.exit(1)

    if args.png_path:
        png_path = Path(args.png_path)
        if not png_path.exists():
            print(f"ERROR: --png-path not found: {png_path}", file=sys.stderr)
            sys.exit(1)
        print(f"Using existing PNG: {png_path}")
    elif img_path.suffix.lower() == ".png":
        png_path = img_path
        print(f"Using PNG directly: {png_path}")
    else:
        # Render DWG/DXF to PNG
        png_path = Path("/tmp") / f"benchmark_{img_path.stem}.png"
        print(f"Rendering {img_path.name} -> {png_path.name} via dwg2bmp...")
        try:
            render_dwg_to_png(img_path, png_path)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"  done: {png_path} ({png_path.stat().st_size/1024:.1f} KB)")

    # Step 2: Resolve questions
    if args.question:
        questions = [{"id": f"Q{i+1}", "text": q} for i, q in enumerate(args.question)]
    else:
        questions = DEFAULT_QUESTIONS

    # Step 3: Encode image
    png_b64 = base64.b64encode(png_path.read_bytes()).decode()
    print(f"Image: {png_path} ({len(png_b64)/1024:.1f} KB b64)")
    print(f"Models: {', '.join(args.models)}")
    print(f"Questions: {len(questions)}")
    print()

    # Step 4: Run benchmark
    results = {"meta": {
        "image": str(png_path),
        "image_size_bytes": png_path.stat().st_size,
        "models": args.models,
        "ollama_url": args.ollama_url,
        "max_tokens": args.max_tokens,
        "ground_truth": args.ground_truth,
        "questions": questions,
    }, "results": {}}

    for model in args.models:
        print(f"=== {model} ===")
        results["results"][model] = []
        for q in questions:
            print(f"  {q['id']}: ", end="", flush=True)
            r = query_ollama(model, png_b64, q["text"], args.ollama_url, args.max_tokens)
            r["qid"] = q["id"]
            r["yes_no"] = parse_yes_no(r["answer"])
            results["results"][model].append(r)
            ans_preview = r["answer"][:120].replace("\n", " | ")
            print(f"({r['elapsed_s']:.1f}s, {r.get('prompt_eval_count',0)}+{r.get('eval_count',0)} tok, "
                  f"{r['yes_no']}) {ans_preview}")
        print()

    # Step 5: Write JSON
    Path(args.out_json).write_text(json.dumps(results, indent=2))
    print(f"JSON: {args.out_json}")

    # Step 6: Write markdown summary
    md = []
    md.append(f"# VLM Benchmark — {Path(args.image).name}")
    md.append(f"")
    md.append(f"- Image: `{results['meta']['image']}` ({results['meta']['image_size_bytes']/1024:.1f} KB)")
    md.append(f"- Models: {', '.join(f'`{m}`' for m in args.models)}")
    md.append(f"- Questions: {len(questions)}")
    if args.ground_truth:
        md.append(f"- Ground truth: {args.ground_truth}")
    md.append("")

    for q in questions:
        md.append(f"## {q['id']}")
        md.append(f"")
        md.append(f"**Q:** {q['text']}")
        md.append("")
        for model in args.models:
            r = next((x for x in results["results"][model] if x["qid"] == q["id"]), None)
            if r is None:
                continue
            md.append(f"### {model}")
            md.append(f"")
            md.append(f"- Time: {r['elapsed_s']:.1f}s")
            md.append(f"- Tokens: {r['prompt_eval_count']} in + {r['eval_count']} out = {r['total_tokens']} total")
            md.append(f"- YES/NO: {r['yes_no']}")
            md.append(f"- Answer: {r['answer']}")
            md.append("")

    # Summary table
    md.append("## Summary Table")
    md.append("")
    md.append("| Model | Avg latency | Q1 (binary) | Q2 (detail) | Q3 (anti-hall) |")
    md.append("|---|---|---|---|---|")
    for model in args.models:
        rs = results["results"][model]
        if not rs:
            continue
        avg_lat = sum(r["elapsed_s"] for r in rs) / len(rs)
        cells = [f"`{model}`", f"{avg_lat:.1f}s"]
        for q in questions:
            r = next((x for x in rs if x["qid"] == q["id"]), None)
            cells.append(r["yes_no"] if r else "—")
        md.append("| " + " | ".join(cells) + " |")
    md.append("")

    Path(args.out_md).write_text("\n".join(md))
    print(f"Markdown: {args.out_md}")

    # Cleanup PNG if not requested to keep
    if not args.keep_png and not args.png_path and png_path.exists() and png_path != img_path:
        png_path.unlink()
        print(f"Cleaned up: {png_path}")


if __name__ == "__main__":
    main()
