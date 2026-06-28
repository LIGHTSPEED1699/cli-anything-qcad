#!/usr/bin/env python3
"""
Fixed PDF → DXF text clear pipeline.

Changes from v1:
  1. margin=0.2 (was 2.0) — polygon bbox barely expanded, only catches truly inside
  2. entity_type_filter = ['TEXT', 'MTEXT'] — skip geometry entities
  3. text_match_bonus=0.5 — if annotation text contains entity text, boost confidence
  4. min_confidence=0.3 — skip low-confidence matches
  5. per-annot result logging — track what was matched and why
"""
import fitz, math, json, re
from pathlib import Path
from difflib import SequenceMatcher

def ratio(a, b):
    return SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()

def pdf_to_dxf_scale(pdf_path, dxf_path):
    """Approximate scale factor from PDF page to DXF extents."""
    doc_pdf = fitz.open(str(pdf_path))
    page = doc_pdf[0]
    pdf_w, pdf_h = page.rect.width, page.rect.height
    import ezdxf
    doc_dxf = ezdxf.readfile(dxf_path)
    msp = doc_dxf.modelspace()
    pts = []
    for ent in msp:
        if ent.dxftype() in ('TEXT', 'MTEXT'):
            pts.append((ent.dxf.insert.x, ent.dxf.insert.y))
        elif ent.dxftype() == 'LWPOLYLINE':
            for v in ent.get_points('xy'):
                pts.append(v)
    if pts:
        xs, ys = zip(*pts)
        dxf_w = max(xs) - min(xs)
        dxf_h = max(ys) - min(ys)
        return dxf_w/pdf_w, dxf_h/pdf_h, min(xs), min(ys)
    return 1.0, 1.0, 0.0, 0.0

def extract_annotations(pdf_path):
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    annots = []
    for i, a in enumerate(page.annots()):
        r = a.rect
        text = (a.get_text() or "").strip()
        annots.append({
            "idx": i,
            "type": a.type[1],
            "text": text,
            "rect": (r.x0, r.y0, r.x1, r.y1),
            "cx": (r.x0 + r.x1)/2,
            "cy": (r.y0 + r.y1)/2,
        })
    return annots

def associate_delete_with_nearest_polygon(delete_txts, polygons):
    """Map each delete annotation to its spatially nearest polygon."""
    def dist(a, b):
        return math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"])
    out = []
    for t in delete_txts:
        nearest = min(polygons, key=lambda p: dist(t, p)) if polygons else None
        out.append({"text": t, "polygon": nearest})
    return out

def find_text_entities_in_bbox(msp, bbox, annot_text, margin=0.2,
                                entity_types=('TEXT', 'MTEXT'),
                                text_match_bonus=0.5,
                                min_confidence=0.30):
    """
    Find TEXT/MTEXT entities inside bbox, score them by:
      - geometric center distance to bbox center (lower = better)
      - text overlap with annotation text (higher = better)
    Skip entities with final confidence < min_confidence.
    """
    bx0, by0, bx1, by1 = bbox
    # center of bbox
    bcx, bcy = (bx0 + bx1)/2, (by0 + by1)/2
    annot_lower = annot_text.lower()

    matches = []
    for ent in msp:
        etype = ent.dxftype()
        if etype not in entity_types:
            continue
        try:
            pos = (ent.dxf.insert.x, ent.dxf.insert.y)
        except Exception:
            continue
        # Must be inside bbox
        if not (bx0 <= pos[0] <= bx1 and by0 <= pos[1] <= by1):
            continue
        txt = getattr(ent.dxf, 'text', getattr(ent, 'text', ''))
        txt_str = str(txt).strip()
        # Score: inverse normalized distance + text match bonus
        dx = abs(pos[0] - bcx) / max(bx1 - bx0, 1e-6)
        dy = abs(pos[1] - bcy) / max(by1 - by0, 1e-6)
        dist_score = 1.0 - math.hypot(dx, dy) / math.sqrt(2)
        text_score = ratio(annot_lower, txt_str)
        confidence = min(1.0, dist_score + text_score * text_match_bonus)
        matches.append({
            "handle": ent.dxf.handle,
            "etype": etype,
            "text": txt_str,
            "pos": pos,
            "confidence": confidence,
            "distance_score": dist_score,
            "text_score": text_score,
        })
    # Filter by confidence
    filtered = [m for m in matches if m["confidence"] >= min_confidence]
    # Sort by confidence desc
    filtered.sort(key=lambda m: m["confidence"], reverse=True)
    return filtered

def run_pdf_to_dxf_clear(pdf_path: Path, dxf_in: Path, dxf_out: Path,
                         margin: float = 0.2,
                         qcad_bin: Path = Path("/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad-bin")):
    import ezdxf, subprocess, os, textwrap, tempfile

    annots = extract_annotations(pdf_path)
    polygons = [a for a in annots if a["type"] == "Polygon"]
    delete_txts = [a for a in annots if a["type"] == "FreeText" and "delete" in a["text"].lower()]
    spare_txts = [a for a in annots if a["type"] == "FreeText" and "spare" in a["text"].lower()]

    print(f"Annotations: total={len(annots)} polygons={len(polygons)} delete={len(delete_txts)} spare={len(spare_txts)}")

    scale_x, scale_y, offset_x, offset_y = pdf_to_dxf_scale(pdf_path, dxf_in)
    print(f"Scale: PDF→DXF = {scale_x:.4f},{scale_y:.4f} Offset=({offset_x:.2f},{offset_y:.2f})")

    doc = ezdxf.readfile(dxf_in)
    msp = doc.modelspace()

    associations = associate_delete_with_nearest_polygon(delete_txts, polygons)

    all_targets = []
    per_annot = []
    for assoc in associations:
        t, p = assoc["text"], assoc["polygon"]
        if not p:
            per_annot.append({"annot_text": t["text"], "reason": "no_polygon", "targets": []})
            continue
        # Convert polygon bbox with small margin
        rect = p["rect"]
        bbox = (rect[0]*scale_x+offset_x-margin, rect[1]*scale_y+offset_y-margin,
                rect[2]*scale_x+offset_x+margin, rect[3]*scale_y+offset_y+margin)
        matches = find_text_entities_in_bbox(
            msp, bbox, t["text"], margin=margin, entity_types=('TEXT','MTEXT'),
            text_match_bonus=0.5, min_confidence=0.30
        )
        # Top-N: cap at 3 entities per delete annotation
        top = matches[:3]
        for m in top:
            m["annot_text"] = t["text"]
            m["polygon_rect"] = rect
            all_targets.append(m)
        per_annot.append({
            "annot_text": t["text"],
            "reason": f"matched {len(top)}/{len(matches)} in bbox ({bbox[0]:.2f},{bbox[1]:.2f},{bbox[2]:.2f},{bbox[3]:.2f})",
            "targets": top,
        })

    # Deduplicate by handle
    seen = set()
    unique_targets = [d for d in all_targets if not (d["handle"] in seen or seen.add(d["handle"]))]
    print(f"Unique targets to clear: {len(unique_targets)}")
    for d in unique_targets:
        print(f"  {d['etype']:8s} {d['handle']} conf={d['confidence']:.2f} '{d['text'][:40]}'")

    # Apply edits via raw-byte handle replacement
    with open(dxf_in, 'rb') as f:
        raw = f.read()

    for d in unique_targets:
        h = d["handle"].upper()
        pat = b'\r\n  5\r\n' + h.encode() + b'\r\n'
        h_pos = raw.find(pat)
        if h_pos == -1:
            print(f"  WARNING: handle {h} not found")
            continue
        search_start = h_pos + len(pat)
        gc1_pos = raw.find(b'\r\n  1\r\n', search_start)
        if gc1_pos == -1:
            print(f"  WARNING: group code 1 not found after handle {h}")
            continue
        val_start = gc1_pos + len(b'\r\n  1\r\n')
        val_end = raw.find(b'\r\n', val_start)
        if val_end == -1:
            continue
        raw = raw[:val_start] + b'.' + raw[val_end:]
    with open(dxf_out, 'wb') as f:
        f.write(raw)
    print(f"Saved: {dxf_out} ({dxf_out.stat().st_size} bytes)")

    # DWG conversion via QCAD Pro headless ODA
    dwg_out = dxf_out.with_suffix('.dwg')
    script = textwrap.dedent(f'''\
    include("scripts/library.js");
    function main() {{
        var inputFile = "{dxf_out.resolve()}";
        var outputFile = "{dwg_out.resolve()}";
        var storage = new RMemoryStorage();
        var spatialIndex = new RSpatialIndexSimple();
        var doc = new RDocument(storage, spatialIndex);
        var di = new RDocumentInterface(doc);
        print("Importing DXF...");
        var importResult = di.importFile(inputFile);
        if (importResult !== RDocumentInterface.IoErrorNoError) {{
            qWarning("ERROR: Cannot import DXF (code " + importResult + ")");
            qcad.quit(1); return;
        }}
        print("  Entities: " + doc.queryAllEntities().length);
        print("Exporting DWG...");
        var formats = ["DWG R32 (2018)", "R32 (2018) DWG", "DWG", "R32"];
        var success = false;
        for (var i = 0; i < formats.length; i++) {{
            if (di.exportFile(outputFile, formats[i])) {{
                print("  Exported: " + formats[i]); success = true; break;
            }}
        }}
        if (!success) {{ qWarning("ERROR: All DWG export attempts failed."); qcad.quit(1); return; }}
        print("SUCCESS: " + outputFile);
        if (typeof(QCoreApplication) !== 'undefined') QCoreApplication.quit(0);
    }}
    if (typeof(including) === 'undefined' || including === false) main();
    ''')
    qcad_dir = qcad_bin.parent.resolve()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(script)
        script_path = Path(f.name)
    env = {**os.environ, 'LD_LIBRARY_PATH': f'{qcad_dir}:{qcad_dir / "plugins"}:{os.environ.get("LD_LIBRARY_PATH","")}'}
    cmd = [str(qcad_bin), '-no-gui', '-platform', 'offscreen', '-allow-multiple-instances', '-autostart', str(script_path)]
    print(f"\nQCAD headless: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    print(f"  exit={result.returncode}")
    script_path.unlink(missing_ok=True)
    if dwg_out.exists():
        print(f"  DWG: {dwg_out} ({dwg_out.stat().st_size} bytes)")
    else:
        print("  ERROR: DWG not created")
        for line in result.stderr.split('\n')[:10]:
            print(f"    {line}")

    # Log
    log = {
        "config": {"margin": margin, "text_match_bonus": 0.5, "min_confidence": 0.30, "max_per_annot": 3},
        "annotations": {"total": len(annots), "polygons": len(polygons), "delete_texts": len(delete_txts), "spare_texts": len(spare_txts)},
        "per_annot": per_annot,
        "cleared": [{"handle": d["handle"], "type": d["etype"], "text": d["text"], "pos": [round(x,4) for x in d["pos"]], "confidence": round(d["confidence"], 2)} for d in unique_targets],
        "files": {"dxf": str(dxf_out), "dwg": str(dwg_out) if dwg_out.exists() else None},
    }
    log_path = dxf_out.parent / (dxf_out.stem + "_fixed_deletion_log.json")
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2, default=str)
    print(f"\nLog: {log_path}")
    return log

if __name__ == "__main__":
    import sys
    DATA_DIR = Path("/home/hongbin/.hermes/kanban/workspaces/testfiles_2026.05.07")
    if len(sys.argv) > 1 and sys.argv[1] == "pair2":
        # Pair 2: need to handle "remove circled objects; make RELAY 15 box smaller"
        # This is complex multi-step; print warning
        print("Pair 2 has complex multi-step annotations. This script only handles deletions.")
        PDF = DATA_DIR / "2_ORIGINAL.pdf"  # 0 annotations; can't run
    else:
        # Pair 1
        PDF = DATA_DIR / "1.pdf"
        DXF_IN = DATA_DIR / "1.dxf"
        DXF_OUT = DATA_DIR / "1_MODIFIED_FIXED.dxf"
        run_pdf_to_dxf_clear(PDF, DXF_IN, DXF_OUT, margin=0.2)
