# PDF Annotation Handling & Source-of-Truth Preservation

Session-specific reference for the PDF annotation ingestion layer. Covers PyMuPDF annotation extraction, file integrity verification, and the distinction between user-uploaded annotated PDFs vs. generated review PDFs.

## 1. Source-of-Truth Integrity Check

Before trusting any PDF in the workspace as the annotation source, verify:

| Check | Command | Expected for valid original |
|-------|---------|----------------------------|
| File size vs. user upload | `ls -lah file.pdf` | Should match user's file (~same order of magnitude) |
| Annotation count | `python3 -c "import fitz; doc=fitz.open('file.pdf'); print(len(list(doc[0].annots())))"` | >0 (for annotated files) |
| Page dimensions | `python3 -c "import fitz; print(fitz.open('file.pdf')[0].rect)"` | Should be engineering-drawing scale, e.g., Rect(0,0,600,800+) |
| File type | `file file.pdf` | Should be `PDF document, version 1.x`, not truncated |

**Corruption signal examples:**
- 136 KB original → 19 KB "original" in workspace = overwritten by generated PDF
- Page rect: `Rect(0.0, 0.0, 34.0, 26.0)` = generated review thumbnail, not original
- 0 annotations on a file that user says is annotated = wrong file in workspace

## 2. Annotation Extraction with PyMuPDF

```python
import fitz

doc = fitz.open(pdf_path)
for page in doc:
    for annot in page.annots():
        atype = annot.type[1]       # "FreeText", "Polygon", "Line", etc.
        text  = annot.get_text() or ""
        rect  = annot.rect            # (x0, y0, x1, y1)
        # Skip decorative geometry — only actionable text counts
        if atype != "FreeText" or not text.strip():
            continue
        # Safe to use as pipeline input
```

### Real annotation breakdown (from 2026-05-08 `1.pdf`):

| Type | Count | Actionable? |
|------|-------|-------------|
| Polygon (cloud shape) | 3 | ❌ Decorative |
| Line (leader/arrow) | 17 | ❌ Decorative |
| FreeText | 7 | ✅ Only pipeline input |

**~70% of all annotations are non-actionable markers.** The pipeline should skip them.

### FreeText annotations in `1.pdf`:

```
[10] "mark spare on both\nends"  →  T1: replace text
[22] "mark spare on both\nends"  →  T1: replace text
[25] "delete clouded \nobjects"    →  T3/T4: delete all entities under cloud polygons
[26] "delete clouded \nobjects"    →  T3/T4
[27] "delete clouded \nobjects"    →  T3/T4
[28] "delete clouded \nobjects"    →  T3/T4
[29] "delete"                     →  T1/T3: ambiguous, low confidence → human review
```

## 3. Generated PDF vs. Original PDF

| | User's annotated PDF | Generated review PDF |
|---|---|---|
| **Contains annotations** | ✅ FreeText, Polygons, Lines | ❌ None (rendered from DXF) |
| **Fonts** | PDF-native fonts (e.g., Arial, Helvetica) | DXF fonts → SHX or vector outlines |
| **Graphics** | Original CAD geometry + markup | Re-rendered CAD geometry only |
| **Size** | Usually 100–500 KB | Often 10–30 KB (low-res) |
| **Page dimensions** | Standard drawing size | Often tiny if rendered at default scale |
| **Fidelity** | Exact user intent | Approximate — may differ |

## 4. Safe File Naming Conventions

**NEVER use these names for generated files in the same directory as originals:**
- `1_ORIGINAL.pdf` — clashes with user's mental model
- `1_MODIFIED.pdf` — implies you modified their file, which you didn't
- `1.pdf` — overwrites user's file

**Correct approach:**
- Put generated PDFs in `pipeline_output/review_pdfs/`
- Prefix with `generated_` or `rendered_`: `generated_pair1_review_150dpi.pdf`
- Keep user's `1.pdf` untouched in the workspace root

## 5. LibreCAD / QCAD PDF Generation Issues

LibreCAD's `dxf2pdf` or headless QCAD export has known quality issues:

- **Low resolution** — default ~100 DPI or lower
- **Small page size** — output dimensions may be a fraction of original
- **No annotation preservation** — PDF annotations are NOT in the DXF; they won't survive
- **Font substitution** — SHX fonts get replaced or dropped
- **Corrupted material tables** — ezdxf save of LibreDWG DXF can corrupt metadata

**Verification command for generated PDFs:**
```bash
pdftoppm -png -r 150 generated.pdf generated_preview
# Check if the PNG looks correct before trusting it
```

If the generated PNG is 71×55 pixels, the PDF is broken — do not use it for any downstream pipeline step.

## 6. DWG→DXF→PDF Round-Trip Quality

LibreDWG `dwg2dxf` produces valid DXF for most geometry, but:
- ~22% LINE entities may be lost on round-trip (R4 finding)
- ~77% LWPOLYLINE entities may be lost on round-trip (R4 finding)
- Text entities survive well (direct ASCII replacement works)

For review PDF generation, prefer:
1. ezdxf + matplotlib backend (higher fidelity, configurable DPI)
2. QCAD headless ECMAScript export (native CAD rendering)
3. LibreCAD only as last resort (lowest fidelity)

## 7. Session Context & Resume Safety

When context compaction triggers a gateway handoff:
1. **DO NOT assume the workspace file is the original** — it may be a generated artifact from a previous session
2. **Re-verify** file sizes, annotation counts, and page dimensions on resume
3. **If the user's file differs from the workspace file, always use the user's file**
4. Write `SESSION_STATE.md` with explicit file integrity notes when checkpointing
