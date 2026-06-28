#!/usr/bin/env python3
"""
Standalone DXF/DWG → PDF converter using ezdxf + matplotlib.
Handles the LibreDWG round-trip automatically if ezdxf can't parse raw DXF.

Usage:
    python3 dxf_to_pdf.py input.dxf output.pdf
    python3 dxf_to_pdf.py input.dwg output.pdf --libredwg /media/sdddata1/libredwg/bin

Requires: ezdxf, matplotlib (in Hermes venv)

ODA File Converter note:
    If LibreDWG cannot produce AutoCAD-compatible DWGs for your file format,
    use ODA File Converter instead (Qt-based). You have the AppImage at:
    /media/sdddata1/libredwg/ODAFileConverter.AppImage
    Extract with: AppImage --appimage-extract (requires no FUSE)
    Then run: xvfb-run ./squashfs-root/usr/bin/ODAFileConverter [args]
"""
import sys
import subprocess
import tempfile
from pathlib import Path


def roundtrip_through_libredwg(dxf_path: Path, libredwg_bin: Path) -> Path:
    """DXF → DWG → DXF round-trip to sanitize structural quirks."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        dwg = td / "temp.dwg"
        clean_dxf = td / "clean.dxf"
        
        r1 = subprocess.run(
            [str(libredwg_bin / "dxf2dwg"), "-o", str(dwg), str(dxf_path)],
            capture_output=True, text=True
        )
        if r1.returncode != 0 and not dwg.exists():
            raise RuntimeError(f"dxf2dwg failed: {r1.stderr[:500]}")
        
        r2 = subprocess.run(
            [str(libredwg_bin / "dwg2dxf"), "-o", str(clean_dxf), str(dwg)],
            capture_output=True, text=True
        )
        if r2.returncode != 0 and not clean_dxf.exists():
            raise RuntimeError(f"dwg2dxf failed: {r2.stderr[:500]}")
        
        return clean_dxf


def render_pdf(dxf_path: Path, pdf_path: Path, figsize=(20, 14)) -> None:
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, matplotlib
    import matplotlib.pyplot as plt
    
    doc = ezdxf.readfile(str(dxf_path))
    fig = plt.figure(figsize=figsize)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    ax.axis("off")
    
    ctx = RenderContext(doc)
    out = matplotlib.MatplotlibBackend(ax)
    frontend = Frontend(ctx, out)
    frontend.draw_layout(doc.modelspace(), finalize=True)
    
    fig.savefig(str(pdf_path), format="pdf", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 dxf_to_pdf.py input.[dxf|dwg] output.pdf [--libredwg PATH]")
        sys.exit(1)
    
    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    libredwg_arg = sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "--libredwg" else "/media/sdddata1/libredwg/bin"
    libredwg_bin = Path(libredwg_arg)
    
    if not in_path.exists():
        raise FileNotFoundError(f"Input not found: {in_path}")
    
    dxf_path = in_path
    
    # If input is DWG, convert to DXF first
    if in_path.suffix.lower() == ".dwg":
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            temp_dxf = td / "input.dxf"
            subprocess.run(
                [str(libredwg_bin / "dwg2dxf"), "-o", str(temp_dxf), str(in_path)],
                capture_output=True, text=True, check=False
            )
            dxf_path = temp_dxf
    
    # Try ezdxf direct; fall back to round-trip if it fails
    try:
        render_pdf(dxf_path, out_path)
    except Exception as e:
        print(f"ezdxf direct failed ({e}), trying LibreDWG round-trip...")
        clean_dxf = roundtrip_through_libredwg(dxf_path, libredwg_bin)
        render_pdf(clean_dxf, out_path)
    
    print(f"PDF written: {out_path} ({out_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
