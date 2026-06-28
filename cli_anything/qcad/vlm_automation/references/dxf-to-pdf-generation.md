# DXF/DWG → PDF Generation

Three paths for producing review/archival PDFs from DXF or DWG on a headless Linux system.

## Path A: ezdxf + matplotlib (Recommended for Review Renders)

Best for: quick visual verification, automated pipeline outputs, headless servers.

```python
import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, matplotlib
import matplotlib.pyplot as plt

doc = ezdxf.readfile("input.dxf")
fig = plt.figure(figsize=(20, 14))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_aspect("equal")
ax.axis("off")

ctx = RenderContext(doc)
out = matplotlib.MatplotlibBackend(ax)
frontend = Frontend(ctx, out)
frontend.draw_layout(doc.modelspace(), finalize=True)

fig.savefig("output.pdf", format="pdf", bbox_inches="tight", pad_inches=0.05)
plt.close(fig)
```

**Critical caveat:** ezdxf's parser is stricter than LibreDWG/AutoCAD. It rejects blank values under group code `1` (empty TEXT strings) and may fail on raw LibreDWG `dwg2dxf` output due to `OBJECTS`-section quirks. **If `ezdxf.readfile()` fails, round-trip the file first** (see Path C prerequisite below).

## Path B: LibreCAD `dxf2pdf` (Avoid Headless)

```bash
# GUI mode — works if DISPLAY is available
librecad dxf2pdf -o output.pdf -m -a input.dxf

# Headless attempt — often hangs indefinitely
QT_QPA_PLATFORM=offscreen librecad dxf2pdf -o output.pdf -m -a input.dxf
```

**Why avoid:** LibreCAD dxf2pdf is a Qt application that initializes the full GUI stack even in "console" mode. On a headless server without a real display, it hangs on `propagateSizeHints()` / event-loop initialization. The timeout is 60–120s and may leave a zombie process. Use only on workstations with active X11/Wayland.

## Path C: LibreDWG Round-trip + ezdxf (Reliable for Complex DXFs)

When ezdxf fails on a raw LibreDWG DXF:

```bash
# Step 1: convert DXF → DWG (cleans structural quirks)
/media/sdddata1/libredwg/bin/dxf2dwg -o temp.dwg input.dxf

# Step 2: convert DWG → DXF (produces ezdxf-safe output)
/media/sdddata1/libredwg/bin/dwg2dxf -o clean.dxf temp.dwg

# Step 3: render with ezdxf + matplotlib (see Path A)
```

The round-trip resolves:
- Empty/malformed group-code-1 values (replaced with valid defaults)
- Corrupted `MATERIAL` / `TABLESTYLE` / `MLEADERSTYLE` tables
- Handle stream boundary errors in `HATCH` entities

**Expected output size:** ~160 KB per A1/A0 engineering drawing at matplotlib default DPI.

## Naming Safety

Generated review PDFs must **never** overwrite the user's source-of-truth annotated PDF.

| Source | Generated | Safe?
|--------|-----------|-------|
| `1.pdf` (user upload, 136 KB, has FreeText annotations) | `pipeline_output/generated_1_review.pdf` | ✅ Yes |
| `1.pdf` | `1_MODIFIED.pdf` in same directory | ⚠️ Risk of confusion |
| `1.pdf` | `1.pdf` (overwrite) | ❌ NEVER |

Always write generated PDFs to a dedicated output directory (`pipeline_output/review_pdfs/`) with a `generated_` or `render_` prefix.

## Verifying Source Integrity Before Generation

```python
import fitz
doc = fitz.open("1.pdf")
page = doc[0]
annot_count = sum(1 for _ in page.annots())
file_size = Path("1.pdf").stat().st_size
print(f"Size: {file_size:,} bytes, Annotations: {annot_count}")
# A valid annotated PDF should be >100 KB and have >0 annotations.
```
