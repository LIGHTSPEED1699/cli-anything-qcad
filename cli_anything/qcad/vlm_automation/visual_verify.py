#!/usr/bin/env python3
"""
visual_verify.py — Post-pipeline verification: render two DXFs and pixel-diff.

Usage:
    python3 visual_verify.py original.dxf modified.dxf "Description of expected change"

Returns exit code 0 if pixel diff < 1% (recommended threshold for clones),
1 if 1–15% (review), 2 if > 15% (likely corruption).

Requirements: ezdxf, matplotlib, numpy, Pillow
"""

import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path
from PIL import Image
import ezdxf


def render_dxf(filepath: Path, out_png: Path):
    """Render DXF to PNG via matplotlib. Handles LINE, LWPOLYLINE, ARC, CIRCLE, TEXT, MTEXT."""
    doc = ezdxf.readfile(filepath)
    all_pts = []
    
    # First pass: collect bounds
    for e in doc.modelspace():
        try:
            et = e.dxftype()
            if et == "LINE":
                all_pts += [(e.dxf.start.x, e.dxf.start.y),
                            (e.dxf.end.x, e.dxf.end.y)]
            elif et == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
                all_pts += pts
            elif et in ("TEXT", "MTEXT"):
                all_pts += [(e.dxf.insert.x, e.dxf.insert.y)]
            elif et in ("ARC", "CIRCLE"):
                all_pts += [(e.dxf.center.x, e.dxf.center.y)]
        except Exception:
            pass
    
    if not all_pts:
        raise ValueError("No plottable entities found")
    
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    margin_x = (max(xs) - min(xs)) * 0.05 + 1.0
    margin_y = (max(ys) - min(ys)) * 0.05 + 1.0
    bbox = (min(xs) - margin_x, max(xs) + margin_x,
            min(ys) - margin_y, max(ys) + margin_y)
    
    # Second pass: draw
    fig, ax = plt.subplots(figsize=(14, 10), dpi=150)
    ax.set_xlim(bbox[0], bbox[1])
    ax.set_ylim(bbox[2], bbox[3])
    ax.set_aspect('equal')
    ax.set_facecolor('white')
    ax.set_title(f"{filepath.name} — verification render")
    
    # Suppress scientific notation
    ax.ticklabel_format(style='plain', axis='both')
    
    def _color(color_val):
        cmap = {1: 'red', 2: 'yellow', 3: 'limegreen', 4: 'cyan',
                5: 'blue', 6: 'magenta', 7: 'black', 0: 'black',
                256: 'black'}
        return cmap.get(color_val, 'black')
    
    for e in doc.modelspace():
        try:
            et = e.dxftype()
            c = _color(getattr(e.dxf, 'color', 7))
            lw = 1.2 if e.dxf.layer != "0" else 0.6
            
            if et == "LINE":
                ax.plot([e.dxf.start.x, e.dxf.end.x],
                       [e.dxf.start.y, e.dxf.end.y],
                       color=c, lw=lw)
            elif et == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
                if len(pts) > 1:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    # Closed if bulge-last or if start==end — just draw polyline
                    ax.plot(xs + [xs[0]], ys + [ys[0]], color=c, lw=lw)
            elif et == "ARC":
                theta = np.linspace(np.radians(e.dxf.start_angle),
                                   np.radians(e.dxf.end_angle), 120)
                ax.plot(e.dxf.center.x + e.dxf.radius * np.cos(theta),
                       e.dxf.center.y + e.dxf.radius * np.sin(theta),
                       color=c, lw=lw)
            elif et == "CIRCLE":
                theta = np.linspace(0, 2*np.pi, 120)
                ax.plot(e.dxf.center.x + e.dxf.radius * np.cos(theta),
                       e.dxf.center.y + e.dxf.radius * np.sin(theta),
                       color=c, lw=lw)
            elif et == "TEXT":
                ax.text(e.dxf.insert.x, e.dxf.insert.y, e.dxf.text,
                       fontsize=8, color=c, ha='center', va='center',
                       rotation=np.degrees(e.dxf.rotation or 0))
            elif et == "MTEXT":
                ax.text(e.dxf.insert.x, e.dxf.insert.y, e.text,
                       fontsize=8, color=c, ha='left', va='center')
        except Exception:
            pass
    
    plt.tight_layout()
    fig.savefig(out_png, dpi=150, bbox_inches='tight')
    plt.close(fig)


def pixel_diff(orig_png: Path, mod_png: Path, out_diff: Path = None, threshold: int = 30):
    """Compute pixel-level diff between two PNGs. Returns (changed, total, pct)."""
    img1 = Image.open(orig_png).convert("RGB")
    img2 = Image.open(mod_png).convert("RGB")
    
    if img1.size != img2.size:
        img2 = img2.resize(img1.size)
    
    w, h = img1.size
    total = w * h
    diff_count = 0
    
    diff_img = Image.new("RGB", img1.size) if out_diff else None
    
    for y in range(h):
        for x in range(w):
            p1 = img1.getpixel((x, y))
            p2 = img2.getpixel((x, y))
            d = sum(abs(a - b) for a, b in zip(p1, p2))
            if d > threshold:
                diff_count += 1
                if diff_img:
                    diff_img.putpixel((x, y), (255, 0, 0))
            else:
                if diff_img:
                    diff_img.putpixel((x, y), p1)
    
    if diff_img and out_diff:
        diff_img.save(out_diff)
    
    pct = diff_count / total if total > 0 else 0
    return diff_count, total, pct


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 visual_verify.py original.dxf modified.dxf [--diff diff.png] [expected_change_desc]")
        sys.exit(1)
    
    orig_file = Path(sys.argv[1])
    mod_file = Path(sys.argv[2])
    diff_file = Path("/tmp/visual_verification_diff.png")
    
    for i, a in enumerate(sys.argv):
        if a == "--diff" and i + 1 < len(sys.argv):
            diff_file = Path(sys.argv[i + 1])
    
    expected = sys.argv[3] if len(sys.argv) > 3 else ""
    
    tmpdir = Path("/tmp") / "vverify"
    tmpdir.mkdir(exist_ok=True)
    orig_png = tmpdir / "orig.png"
    mod_png = tmpdir / "mod.png"
    
    print(f"Rendering original: {orig_file}")
    render_dxf(orig_file, orig_png)
    print(f"  → {orig_png} ({orig_png.stat().st_size} bytes)")
    
    print(f"Rendering modified: {mod_file}")
    render_dxf(mod_file, mod_png)
    print(f"  → {mod_png} ({mod_png.stat().st_size} bytes)")
    
    print("Computing pixel diff...")
    changed, total, pct = pixel_diff(orig_png, mod_png, diff_file)
    print(f"  Changed: {changed}/{total} ({pct * 100:.2f}%)")
    if diff_file.exists():
        print(f"  Diff image: {diff_file}")
    
    if pct < 0.01:
        verdict = "PASSED"
        code = 0
    elif pct < 0.15:
        verdict = "WARNING"
        code = 1
    else:
        verdict = "FAILED (large change — possible corruption)"
        code = 2
    
    print(f"  Verdict: {verdict}")
    sys.exit(code)


if __name__ == "__main__":
    main()
