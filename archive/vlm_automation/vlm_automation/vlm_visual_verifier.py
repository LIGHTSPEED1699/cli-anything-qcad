#!/usr/bin/env python3
"""
VLM Visual Verifier for CAD Drawings

Uses a local vision-language model (qwen2.5vl via Ollama) to analyze
rendered DXF screenshots and report on terminal labels, wire routing,
and duplication issues.

Usage:
    python3 vlm_visual_verifier.py <dxf_file> --prompt "Check for duplicate labels"
    python3 vlm_visual_verifier.py <dxf_file> --side-by-side <other_dxf>
"""

import argparse
import base64
import os
import requests
import sys

def render_dxf_to_png(dxf_path, png_path, figsize=(24, 16), dpi=200):
    """Render DXF to PNG using ezdxf matplotlib backend."""
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, matplotlib
    import matplotlib.pyplot as plt

    doc = ezdxf.readfile(dxf_path)
    fig = plt.figure(figsize=figsize)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    ax.axis("off")
    ctx = RenderContext(doc)
    out = matplotlib.MatplotlibBackend(ax)
    frontend = Frontend(ctx, out)
    frontend.draw_layout(doc.modelspace(), finalize=True)
    fig.savefig(png_path, format="png", bbox_inches="tight", pad_inches=0.02, dpi=dpi)
    plt.close(fig)
    print(f"Rendered: {png_path} ({os.path.getsize(png_path):,} bytes)")


def render_zoomed(dxf_path, png_path, xlim, ylim, figsize=(14, 14), dpi=200):
    """Render a zoomed region of a DXF."""
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, matplotlib
    import matplotlib.pyplot as plt

    doc = ezdxf.readfile(dxf_path)
    fig = plt.figure(figsize=figsize)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    ctx = RenderContext(doc)
    out = matplotlib.MatplotlibBackend(ax)
    frontend = Frontend(ctx, out)
    frontend.draw_layout(doc.modelspace(), finalize=True)
    fig.savefig(png_path, format="png", bbox_inches="tight", pad_inches=0.02, dpi=dpi)
    plt.close(fig)
    print(f"Zoomed render: {png_path} ({os.path.getsize(png_path):,} bytes)")


def render_side_by_side(left_dxf, right_dxf, png_path, xlim=None, ylim=None,
                         figsize=(32, 18), dpi=200):
    """Render two DXFs side by side for comparison."""
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, matplotlib
    import matplotlib.pyplot as plt

    def render_to_axis(ax, dxf_path):
        doc = ezdxf.readfile(dxf_path)
        ax.set_aspect("equal")
        ax.axis("off")
        if xlim:
            ax.set_xlim(xlim)
        if ylim:
            ax.set_ylim(ylim)
        ctx = RenderContext(doc)
        out = matplotlib.MatplotlibBackend(ax)
        frontend = Frontend(ctx, out)
        frontend.draw_layout(doc.modelspace(), finalize=True)

    fig, axes = plt.subplots(1, 2, figsize=figsize)
    axes[0].set_title("Original", fontsize=14, fontweight='bold', pad=10)
    render_to_axis(axes[0], left_dxf)
    axes[1].set_title("Modified", fontsize=14, fontweight='bold', pad=10)
    render_to_axis(axes[1], right_dxf)
    plt.tight_layout()
    fig.savefig(png_path, format="png", bbox_inches="tight", pad_inches=0.05, dpi=dpi)
    plt.close(fig)
    print(f"Side-by-side: {png_path} ({os.path.getsize(png_path):,} bytes)")


def vlm_analyze(png_path, prompt, model="qwen2.5vl:latest",
                url="http://localhost:11434/api/generate", timeout=120):
    """Send PNG to VLM for analysis."""
    with open(png_path, "rb") as f:
        img_data = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "model": model,
        "prompt": prompt,
        "images": [img_data],
        "stream": False,
        "options": {"num_ctx": 8192, "temperature": 0.3}
    }
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json().get("response", "")


def main():
    parser = argparse.ArgumentParser(description="VLM Visual Verifier for CAD")
    parser.add_argument("dxf", help="Input DXF file")
    parser.add_argument("--prompt", default="Analyze this CAD drawing screenshot. Identify terminal labels, wire routing, and any anomalies.",
                        help="VLM prompt")
    parser.add_argument("--side-by-side", metavar="OTHER_DXF", help="Compare with another DXF")
    parser.add_argument("--zoom-xlim", nargs=2, type=float, help="Zoom region x-min x-max")
    parser.add_argument("--zoom-ylim", nargs=2, type=float, help="Zoom region y-min y-max")
    parser.add_argument("--model", default="qwen2.5vl:latest")
    parser.add_argument("--url", default="http://localhost:11434/api/generate")
    parser.add_argument("--out", default="/tmp/vlm_verify.png")
    args = parser.parse_args()

    if args.side_by_side:
        render_side_by_side(args.dxf, args.side_by_side, args.out,
                           xlim=tuple(args.zoom_xlim) if args.zoom_xlim else None,
                           ylim=tuple(args.zoom_ylim) if args.zoom_ylim else None)
    elif args.zoom_xlim or args.zoom_ylim:
        render_zoomed(args.dxf, args.out,
                     xlim=tuple(args.zoom_xlim) if args.zoom_xlim else (0, 30),
                     ylim=tuple(args.zoom_ylim) if args.zoom_ylim else (0, 25))
    else:
        render_dxf_to_png(args.dxf, args.out)

    result = vlm_analyze(args.out, args.prompt, model=args.model, url=args.url)
    print("\n" + "=" * 70)
    print("VLM ANALYSIS:")
    print("=" * 70)
    print(result)
    print("=" * 70)


if __name__ == "__main__":
    main()
