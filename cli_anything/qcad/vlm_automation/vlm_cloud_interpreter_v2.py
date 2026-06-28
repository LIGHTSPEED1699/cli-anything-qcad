#!/usr/bin/env python3
"""
VLM Cloud Interpreter v2 — adds geometric cross-validation.

Fixes:
  1. After VLM returns a label, search DXF for ALL matching handles
  2. For each matched handle, check if its (x,y) is inside the cloud's DXF bbox
  3. Only keep entities that are both named by VLM AND inside the cloud bbox
  4. Write image as temporary file; call Ollama via HTTP POST (avoids arg list too long)
  5. Crop clouds with bounding box of polygon vertices instead of annot.rect (more accurate)

Usage:
    python3 vlm_cloud_interpreter_v2.py --pdf 1.pdf --dxf 1.dxf --out cloud_v2
"""
import argparse
import base64
import json
import math
import requests
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional
import fitz
import ezdxf


def call_ollama_vision_http(model: str, prompt: str, image_path: Path) -> str:
    """Call Ollama vision via HTTP POST (avoids shell argument limits)."""
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [img_b64],
            }
        ],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    resp = requests.post("http://localhost:11434/api/chat", json=payload, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    return data["message"]["content"]


def extract_cloud_region(pdf_path: Path, annot_idx: int, out_dir: Path, zoom: float = 1.5, pad: int = 80) -> Path:
    """Render a cloud polygon annotation region to PNG.
    
    Uses actual polygon vertices (not just annot.rect) for tighter crop.
    """
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    a = list(page.annots())[annot_idx]
    
    # Get actual polygon vertices for tighter crop
    vertices = a.vertices if hasattr(a, 'vertices') and a.vertices else None
    if vertices and len(vertices) >= 3:
        xs = [v[0] for v in vertices]
        ys = [v[1] for v in vertices]
        mx0, my0, mx1, my1 = min(xs), min(ys), max(xs), max(ys)
    else:
        r = a.rect
        mx0, my0, mx1, my1 = r.x0, r.y0, r.x1, r.y1
    
    pw, ph = page.rect.width, page.rect.height
    cx0 = max(0, mx0 - pad)
    cy0 = max(0, my0 - pad)
    cx1 = min(pw, mx1 + pad)
    cy1 = min(ph, my1 + pad)
    clip = fitz.Rect(cx0, cy0, cx1, cy1)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(clip=clip, matrix=mat)
    out_path = out_dir / f"cloud_{annot_idx}_{zoom}x.png"
    pix.save(str(out_path))
    return out_path


def build_dxf_index(dxf_path: Path) -> (Dict[str, Dict], List[Dict]):
    """Index DXF TEXT/MTEXT entities by handle and by text content."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    by_handle = {}
    by_text = {}
    all_texts = []
    for ent in msp:
        if ent.dxftype() == 'TEXT':
            t = ent.dxf.text.strip()
            pos = (ent.dxf.insert.x, ent.dxf.insert.y)
            h = ent.dxf.handle.upper()
            entry = {"handle": h, "type": "TEXT", "text": t, "pos": pos}
            by_handle[h] = entry
            by_text.setdefault(t.lower(), []).append(entry)
            all_texts.append(entry)
        elif ent.dxftype() == 'MTEXT':
            t = ent.text.strip()
            pos = (ent.dxf.insert.x, ent.dxf.insert.y)
            h = ent.dxf.handle.upper()
            entry = {"handle": h, "type": "MTEXT", "text": t, "pos": pos}
            by_handle[h] = entry
            by_text.setdefault(t.lower(), []).append(entry)
            all_texts.append(entry)
    return by_handle, by_text, all_texts


def pdf_to_dxf_scale(pdf_path: Path, dxf_texts: List[Dict]) -> tuple:
    """Compute PDF-to-DXF coordinate mapping."""
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    pts = [(d["pos"][0], d["pos"][1]) for d in dxf_texts]
    if not pts:
        return 1.0, 1.0, 0.0, 0.0
    xs, ys = zip(*pts)
    dxf_w, dxf_h = max(xs) - min(xs), max(ys) - min(ys)
    pdf_w, pdf_h = page.rect.width, page.rect.height
    scale_x = dxf_w / pdf_w if pdf_w > 0 else 1.0
    scale_y = dxf_h / pdf_h if pdf_h > 0 else 1.0
    return scale_x, scale_y, min(xs), min(ys)


def point_in_bbox(point, bbox):
    x, y = point
    x0, y0, x1, y1 = bbox
    return x0 <= x <= x1 and y0 <= y <= y1


def interpret_cloud(model: str, cloud_image: Path, annot_text: str) -> List[str]:
    """Ask VLM to identify text labels inside the clouded region."""
    prompt = f"""You are looking at a CAD drawing annotation. A red cloud polygon highlights specific text labels that should be deleted.

Annotation instruction: "{annot_text}"

Task: List ONLY the exact text strings of the labels inside the clouded (highlighted) region. Do NOT list labels outside the cloud. Return one label per line. If you see no text labels inside the cloud, say "NONE".

Examples:
- If cloud covers "F175" and "F176", return:
F175
F176

- If cloud covers wire labels "105" and "106", return:
105
106

- If cloud covers only empty space, return:
NONE
"""
    response = call_ollama_vision_http(model, prompt, cloud_image)
    labels = []
    for line in response.strip().split('\n'):
        line = line.strip().strip('"').strip("'")
        if line and line.upper() != "NONE" and not line.startswith("-"):
            labels.append(line)
    return labels


def run_cloud_pipeline(pdf_path: Path, dxf_path: Path, out_dir: Path, model: str = "qwen2.5vl:latest") -> Dict[str, Any]:
    """Run full VLM+geometry cloud interpretation pipeline."""
    by_handle, by_text, all_texts = build_dxf_index(dxf_path)
    scale_x, scale_y, offset_x, offset_y = pdf_to_dxf_scale(pdf_path, all_texts)
    
    doc_pdf = fitz.open(str(pdf_path))
    page = doc_pdf[0]
    
    annots = []
    for i, a in enumerate(page.annots()):
        r = a.rect
        text = (a.get_text() or "").strip()
        annots.append({"idx": i, "type": a.type[1], "text": text, "rect": (r.x0, r.y0, r.x1, r.y1)})
    
    polygons = [a for a in annots if a["type"] == "Polygon"]
    delete_txts = [a for a in annots if a["type"] == "FreeText" and "delete" in a["text"].lower()]
    
    # Map each delete text to nearest polygon
    def sq_dist(a, b):
        return ((a["rect"][0]+a["rect"][2])/2 - (b["rect"][0]+b["rect"][2])/2)**2 + \
               ((a["rect"][1]+a["rect"][3])/2 - (b["rect"][1]+b["rect"][3])/2)**2
    
    associations = []
    for t in delete_txts:
        nearest = min(polygons, key=lambda p: sq_dist(t, p)) if polygons else None
        associations.append({"text": t, "polygon": nearest})
    
    # Remove duplicate cloud associations (multiple delete texts → same cloud)
    seen_clouds = set()
    unique_associations = []
    for assoc in associations:
        p = assoc["polygon"]
        if p and p["idx"] in seen_clouds:
            continue
        if p:
            seen_clouds.add(p["idx"])
        unique_associations.append(assoc)
    
    results = []
    all_unique_handles = set()
    
    for assoc in unique_associations:
        t, p = assoc["text"], assoc["polygon"]
        if not p:
            results.append({
                "annot_idx": t["idx"], "annot_text": t["text"],
                "cloud_idx": None, "vlm_labels": [], "matched_handles": [],
                "reason": "no_polygon",
            })
            continue
        
        # Compute DXF bbox (no margin — strict geometric containment)
        r = p["rect"]
        dxf_bbox = (r[0]*scale_x+offset_x, r[1]*scale_y+offset_y,
                    r[2]*scale_x+offset_x, r[3]*scale_y+offset_y)
        
        # Render cloud image
        try:
            cloud_img = extract_cloud_region(pdf_path, p["idx"], out_dir, zoom=1.5, pad=80)
        except Exception as e:
            results.append({
                "annot_idx": t["idx"], "annot_text": t["text"],
                "cloud_idx": p["idx"], "vlm_labels": [], "matched_handles": [],
                "reason": f"render_error: {e}",
            })
            continue
        
        # Ask VLM
        try:
            labels = interpret_cloud(model, cloud_img, t["text"])
        except Exception as e:
            results.append({
                "annot_idx": t["idx"], "annot_text": t["text"],
                "cloud_idx": p["idx"], "vlm_labels": [], "matched_handles": [],
                "reason": f"vlm_error: {e}",
            })
            continue
        
        # Cross-validate VLM labels against DXF geometry
        matched = []
        for lbl in labels:
            # Find ALL DXF entities matching this text
            hits = by_text.get(lbl.lower(), [])
            # Fallback: fuzzy search via substring
            if not hits:
                for txt_lower, entries in by_text.items():
                    if lbl.lower() in txt_lower or txt_lower in lbl.lower():
                        hits.extend(entries)
            
            # GEOMETRIC FILTER: only keep hits inside the cloud's DXF bbox
            for h in hits:
                if point_in_bbox(h["pos"], dxf_bbox):
                    if h["handle"] not in all_unique_handles:
                        all_unique_handles.add(h["handle"])
                        matched.append(h)
        
        results.append({
            "annot_idx": t["idx"],
            "annot_text": t["text"],
            "cloud_idx": p["idx"],
            "cloud_image": str(cloud_img),
            "dxf_bbox": dxf_bbox,
            "vlm_labels": labels,
            "matched_handles": matched,
            "reason": f"vlm_ok_geo_filter: {len(matched)}/{len(labels)} labels inside bbox",
        })
    
    return {
        "model": model,
        "annotations": {"total": len(annots), "polygons": len(polygons), "delete_texts": len(delete_txts)},
        "associations_processed": len(unique_associations),
        "results": results,
        "all_unique_handles": list(all_unique_handles),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=Path("./cloud_interpretation_v2"))
    parser.add_argument("--model", default="qwen2.5vl:latest")
    args = parser.parse_args()
    
    args.out.mkdir(exist_ok=True, parents=True)
    
    result = run_cloud_pipeline(args.pdf, args.dxf, args.out, model=args.model)
    
    json_path = args.out / "interpretation.json"
    with open(json_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    
    print(f"VLM v2 complete. Results: {json_path}")
    print(f"Unique handles to clear: {len(result['all_unique_handles'])}")
    for r in result["results"]:
        print(f"\n  Annot #{r['annot_idx']} (cloud #{r['cloud_idx']}): {r['reason']}")
        print(f"    VLM labels: {', '.join(r['vlm_labels']) or '(none)'}")
        if r.get('dxf_bbox'):
            b = r['dxf_bbox']
            print(f"    DXF bbox: ({b[0]:.2f},{b[1]:.2f},{b[2]:.2f},{b[3]:.2f})")
        for h in r["matched_handles"]:
            print(f"    → {h['handle']} '{h['text']}' @ ({h['pos'][0]:.2f},{h['pos'][1]:.2f})")
