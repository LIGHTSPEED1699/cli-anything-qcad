#!/usr/bin/env python3
"""
dwg_visual_verify.py — Render DWG/DXF to PNG, then send to VLM for visual QA.

Uses dwg2bmp for headless rendering (no X11 needed), then queries an Ollama
vision model with structured questions about the drawing.

Usage:
    # Basic verification with default question:
    python3 dwg_visual_verify.py /path/to/drawing.dwg

    # Custom question:
    python3 dwg_visual_verify.py /path/to/drawing.dwg \\
        --question "Are there wire labels at terminals 7, 8, and 9?"

    # Verify clone output (Pair 3 specific):
    python3 dwg_visual_verify.py pair3_clone.dwg --pair3

    # Use a different model:
    python3 dwg_visual_verify.py drawing.dwg --model qwen2.5vl --ollama-url http://localhost:11434
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def render_to_png(dwg_path: str, out_png: str, width: int = 2000, height: int = 1500) -> bool:
    """Render DWG/PDF to PNG using dwg2bmp + ImageMagick.

    Uses the proven flags: -x W -y H -zoom-all -m 0 -f
    Copies input to temp dir because dwg2bmp writes output next to input.
    """
    in_path = Path(dwg_path)
    if not in_path.exists():
        print(f"ERROR: file not found: {dwg_path}", file=sys.stderr)
        return False

    # Find dwg2bmp
    qcad_dirs = [
        Path.home() / "opt/qcad",
        Path.home() / "opt/qcad-3.32.5-pro-linux-qt6-x86_64",
    ]
    exe = None
    for qd in qcad_dirs:
        candidate = qd / "dwg2bmp"
        if candidate.exists():
            exe = str(candidate)
            break
    if not exe:
        exe = shutil.which("dwg2bmp")
    if not exe:
        print("ERROR: dwg2bmp not found", file=sys.stderr)
        return False

    # Guard: empty stem
    src_stem = in_path.stem
    if not src_stem:
        print(f"ERROR: path {dwg_path!r} has empty stem (name starts with dot?)", file=sys.stderr)
        return False

    with __import__('tempfile').TemporaryDirectory() as tmpdir:
        tmp_input = Path(tmpdir) / in_path.name
        shutil.copy(str(in_path), str(tmp_input))

        out_bmp = Path(tmpdir) / f"{src_stem}.bmp"
        cmd = [exe, "-x", str(width), "-y", str(height),
               "-zoom-all", "-m", "0", "-f", str(tmp_input)]
        env = os.environ.copy()
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        env.setdefault("LD_LIBRARY_PATH",
                       f"{Path(exe).parent}:{Path(exe).parent / 'plugins'}")

        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=120, env=env)
        if result.returncode != 0 or not out_bmp.exists():
            print(f"dwg2bmp stderr: {result.stderr[:500]}", file=sys.stderr)
            return False

        # Convert BMP to PNG
        subprocess.run(["convert", str(out_bmp), out_png],
                       capture_output=True, timeout=60)
        return Path(out_png).exists()


def ask_vlm(png_path: str, question: str,
            ollama_url: str = "http://localhost:11434",
            model: str = "gemma4:31b-cloud") -> dict:
    """Send PNG to Ollama vision model and return structured answer."""
    with open(png_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a CAD drawing QA assistant. Answer questions about "
                    "the drawing content. Be concise: YES/NO for binary questions. "
                    "If asked to list details, list them with y-coordinates."
                ),
            },
            {
                "role": "user",
                "content": question,
                "images": [b64],
            },
        ],
        "stream": False,
        "options": {"num_predict": 256, "temperature": 0.1},
    }

    req = urllib.request.Request(
        f"{ollama_url}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
        text = result.get("message", {}).get("content", "")
        return {"answer": text.strip(), "model": model, "png": png_path}
    except Exception as e:
        return {"error": str(e), "model": model, "png": png_path}


def pair3_questions() -> list[str]:
    """Return a list of Pair 3 specific verification questions."""
    return [
        "Is there a (W) wire label visible near terminal row 7 (y≈18.68)?",
        "Is there an EPAC G1 label visible near terminal row 9 (y≈17.93)?",
        "Are there wire lines (LINE entities) visible connecting terminals 7, 8, and 9?",
        "Is there a 'CA-1452' or 'PLC22' text label visible near the top of terminal 7?",
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Render DWG to PNG and verify with VLM")
    parser.add_argument("dwg", help="Path to DWG or DXF file")
    parser.add_argument("--question", "-q", default=None,
                        help="Verification question (default: Pair 3 clone check)")
    parser.add_argument("--pair3", action="store_true",
                        help="Run all Pair 3 clone verification questions")
    parser.add_argument("--model", default="gemma4:31b-cloud",
                        help="Ollama vision model")
    parser.add_argument("--ollama-url", default="http://localhost:11434",
                        help="Ollama endpoint URL")
    parser.add_argument("--output-png", default=None,
                        help="Save rendered PNG to this path")
    parser.add_argument("--width", type=int, default=2000,
                        help="Render width in pixels")
    parser.add_argument("--height", type=int, default=1500,
                        help="Render height in pixels")
    args = parser.parse_args()

    dwg = Path(args.dwg)
    if not dwg.exists():
        print(f"ERROR: file not found: {dwg}")
        sys.exit(1)

    # Render to PNG
    out_png = args.output_png or str(Path("/tmp") / f"{dwg.stem}_verify.png")
    print(f"Rendering {dwg} to {out_png}...")
    ok = render_to_png(str(dwg), out_png, args.width, args.height)
    if not ok:
        print("RENDER FAILED — DWG may not be rasterizable by dwg2bmp")
        print("Tip: cloned entities may be invisible in dwg2bmp.")
        print("Opening in QCAD GUI is the reliable alternative.")
        sys.exit(1)
    print(f"  Rendered: {out_png} ({os.path.getsize(out_png):,} bytes)")

    # Determine questions
    questions = []
    if args.pair3:
        questions = pair3_questions()
    elif args.question:
        questions = [args.question]
    else:
        questions = [
            "Describe what you see at the terminals in the upper area of the drawing. "
            "List any text labels visible and their approximate y-positions."
        ]

    # Ask VLM
    for i, q in enumerate(questions):
        print(f"\nQ{i+1}: {q}")
        result = ask_vlm(out_png, q, args.ollama_url, args.model)
        if "error" in result:
            print(f"  VLM ERROR: {result['error']}")
        else:
            print(f"  A: {result['answer']}")

    print(f"\nPNG saved: {out_png}")


if __name__ == "__main__":
    main()
