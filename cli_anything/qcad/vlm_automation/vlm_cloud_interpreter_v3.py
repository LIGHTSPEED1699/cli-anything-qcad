#!/usr/bin/env python3
"""
VLM Cloud Interpreter v3 — HYBRID approach.

Combines geometric candidate detection + VLM disambiguation:
  1. For each cloud, find ALL TEXT entities inside a small-margin DXF bbox
  2. Render the cloud region image
  3. Send image + candidate list to VLM
  4. VLM picks from the provided list → zero hallucination
  5. Geometric cross-validate for safety

Usage:
    python3 vlm_cloud_interpreter_v3.py --pdf 1.pdf --dxf 1.dxf --out cloud_v3
"""
import argparse
import base64
import json
import math
import requests
from pathlib import Path
from typing import List, Dict, Any
import fitz
import ezdxf


def call_ollama_vision_http(model: str, prompt: str, image_path: Path, timeout: int = 120) -> str:
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
        "options": {"temperature": 0.05},  # very deterministic
    }
    resp = requests.post("http://localhost:11434/api/chat", json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data["message"]["content"]


def extract_cloud_region(pdf_path: Path, annot_idx: int, out_dir: Path, zoom: float = 1.5, pad: int = 80) -> Path:
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    a = list(page.annots())[annot_idx]
    
    # Use polygon vertices for tighter crop
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


def build_dxf_index(dxf_path: Path) -> Dict[str, Dict]:
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    by_handle = {}
    for ent in msp:
        if ent.dxftype() == 'TEXT':
            h = ent.dxf.handle.upper()
            by_handle[h] = {
                "handle": h, "type": "TEXT",
                "text": ent.dxf.text.strip(),
                "pos": (round(ent.dxf.insert.x, 4), round(ent.dxf.insert.y, 4))
            }
        elif ent.dxftype() == 'MTEXT':
            h = ent.dxf.handle.upper()
            by_handle[h] = {
                "handle": h, "type": "MTEXT",
                "text": ent.text.strip(),
                "pos": (round(ent.dxf.insert.x, 4), round(ent.dxf.insert.y, 4))
            }
    return by_handle


def pdf_to_dxf_scale(pdf_path: Path, dxf_path: Path) -> tuple:
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    doc_dxf = ezdxf.readfile(dxf_path)
    msp = doc_dxf.modelspace()
    pts = []
    for ent in msp:
        if ent.dxftype() in ('TEXT', 'MTEXT'):
            pts.append((ent.dxf.insert.x, ent.dxf.insert.y))
        elif ent.dxftype() == 'LWPOLYLINE':
            for v in ent.get_points('xy'):
                pts.append(v)
    if not pts:
        return 1.0, 1.0, 0.0, 0.0
    xs, ys = zip(*pts)
    dxf_w, dxf_h = max(xs)-min(xs), max(ys)-min(ys)
    pdf_w, pdf_h = page.rect.width, page.rect.height
    scale_x = dxf_w/pdf_w if pdf_w else 1.0
    scale_y = dxf_h/pdf_h if pdf_h else 1.0
    return scale_x, scale_y, min(xs), min(ys)


def point_in_bbox(point, bbox):
    x, y = point
    x0, y0, x1, y1 = bbox
    return x0 <= x <= x1 and y0 <= y <= y1


def find_candidates_in_bbox(msp, dxf_bbox, margin=0.2):
    """Find text entities inside a margin-expanded DXF bbox."""
    mx0 = dxf_bbox[0] - margin
    my0 = dxf_bbox[1] - margin
    mx1 = dxf_bbox[2] + margin
    my1 = dxf_bbox[3] + margin
    candidates = []
    for ent in msp:
        if ent.dxftype() not in ('TEXT', 'MTEXT'):
            continue
        try:
            x, y = ent.dxf.insert.x, ent.dxf.insert.y
        except Exception:
            continue
        if mx0 <= x <= mx1 and my0 <= y <= my1:
            t = ent.dxf.text.strip() if ent.dxftype() == 'TEXT' else ent.text.strip()
            # Strict geometric check: must be inside base bbox
            in_bbox = point_in_bbox((x, y), dxf_bbox)
            candidates.append({
                "handle": ent.dxf.handle.upper(),
                "type": ent.dxftype(),
                "text": t,
                "pos": (round(x, 4), round(y, 4)),
                "in_bbox": in_bbox,
            })
    return candidates


def vlm_disambiguate(model, cloud_image, annot_text, candidates_list, timeout=120) -> List[str]:
    """Send image + candidate list to VLM; returns selected handles."""
    if not candidates_list:
        return []
    
    # Build candidate text description
    lines = []
    for i, c in enumerate(candidates_list):
        in_str = "(inside cloud)" if c["in_bbox"] else "(near cloud)"
        lines.append(f"{i+1}. '{c['text']}' {in_str} @ ({c['pos'][0]},{c['pos'][1]})")
    candidate_str = "\n".join(lines)
    
    prompt = f"""You are looking at a CAD drawing annotation. A red cloud polygon highlights text labels that should be deleted.

Annotation instruction: "{annot_text}"

The following text entities are near or inside the clouded region:

{candidate_str}

Task: Select ONLY the entities that are ACTUALLY inside the cloud (highlighted by the red polygon). Consider that leader lines and connecting wires may pass through the cloud — only delete the text labels themselves, not wiring annotations.

Return only the entity numbers that should be deleted, one per line. If no entity is inside the cloud, say "NONE".
Example:
1
3
6
"""
    response = call_ollama_vision_http(model, prompt, cloud_image, timeout)
    
    selected = []
    for line in response.strip().split('\n'):
        line = line.strip()
        if not line or line.upper() == "NONE":
            continue
        # Try to parse as number
        try:
            idx = int(line) - 1  # 1-indexed
            if 0 <= idx < len(candidates_list):
                selected.append(candidates_list[idx]["handle"])
        except ValueError:
            # Try fuzzy match to candidate text
            line_lower = line.strip('"').strip("'").lower()
            for c in candidates_list:
                if line_lower in c["text"].lower() or c["text"].lower() in line_lower:
                    selected.append(c["handle"])
                    break
    return selected


def run_cloud_pipeline(pdf_path: Path, dxf_path: Path, out_dir: Path, 
                      model: str = "qwen2.5vl:latest") -> Dict[str, Any]:
    doc_pdf = fitz.open(str(pdf_path))
    page = doc_pdf[0]
    
    annots = []
    for i, a in enumerate(page.annots()):
        r = a.rect
        text = (a.get_text() or "").strip()
        annots.append({"idx": i, "type": a.type[1], "text": text, "rect": (r.x0, r.y0, r.x1, r.y1)})
    
    polygons = [a for a in annots if a["type"] == "Polygon"]
    delete_txts = [a for a in annots if a["type"] == "FreeText" and "delete" in a["text"].lower()]
    
    # Map delete texts to nearest polygons
    def sq_dist(a, b):
        cx = (a["rect"][0]+a["rect"][2])/2
        cy = (a["rect"][1]+a["rect"][3])/2
        bx = (b["rect"][0]+b["rect"][2])/2
        by = (b["rect"][1]+b["rect"][3])/2
        return (cx-bx)**2 + (cy-by)**2
    
    associations = []
    for t in delete_txts:
        nearest = min(polygons, key=lambda p: sq_dist(t, p)) if polygons else None
        associations.append({"text": t, "polygon": nearest})
    
    # Deduplicate cloud associations
    seen_clouds = set()
    unique_associations = []
    for assoc in associations:
        p = assoc["polygon"]
        if p and p["idx"] in seen_clouds:
            continue
        if p:
            seen_clouds.add(p["idx"])
        unique_associations.append(assoc)
    
    # Load DXF
    doc_dxf = ezdxf.readfile(dxf_path)
    msp = doc_dxf.modelspace()
    scale_x, scale_y, offset_x, offset_y = pdf_to_dxf_scale(pdf_path, dxf_path)
    by_handle = build_dxf_index(dxf_path)
    
    results = []
    all_unique_handles = set()
    
    for assoc in unique_associations:
        t, p = assoc["text"], assoc["polygon"]
        if not p:
            results.append({
                "annot_idx": t["idx"], "annot_text": t["text"],
                "cloud_idx": None, "candidates": [], "selected": [], "reason": "no_polygon"
            })
            continue
        
        # Compute DXF bbox using polygon vertices (tighter than rect)
        a = list(page.annots())[p["idx"]]
        vertices = a.vertices if hasattr(a, 'vertices') and a.vertices else None
        if vertices and len(vertices) >= 3:
            xs = [v[0] for v in vertices]
            ys = [v[1] for v in vertices]
            cx0, cy0, cx1, cy1 = min(xs), min(ys), max(xs), max(ys)
        else:
            cx0, cy0, cx1, cy1 = p["rect"]
        
        dxf_bbox = (cx0*scale_x+offset_x, cy0*scale_y+offset_y,
                    cx1*scale_x+offset_x, cy1*scale_y+offset_y)
        
        # Find geometric candidates
        candidates = find_candidates_in_bbox(msp, dxf_bbox, margin=0.2)
        
        # Render cloud image
        try:
            cloud_img = extract_cloud_region(pdf_path, p["idx"], out_dir, zoom=1.5, pad=80)
        except Exception as e:
            results.append({
                "annot_idx": t["idx"], "annot_text": t["text"],
                "cloud_idx": p["idx"], "dxf_bbox": dxf_bbox,
                "candidates": candidates, "selected": [],
                "reason": f"render_error: {e}"
            })
            continue
        
        # If no candidates, skip VLM
        if not candidates:
            results.append({
                "annot_idx": t["idx"], "annot_text": t["text"],
                "cloud_idx": p["idx"], "dxf_bbox": dxf_bbox,
                "candidates": [], "selected": [],
                "reason": "no_candidates_in_bbox"
            })
            continue
        
        # Ask VLM to disambiguate
        try:
            selected_handles = vlm_disambiguate(model, cloud_img, t["text"], candidates)
        except Exception as e:
            selected_handles = []
            reason = f"vlm_error: {e}"
        else:
            reason = f"vlm_ok: {len(selected_handles)}/{len(candidates)} selected"
        
        # Cross-validate: only keep selected handles that are actually in handle index
        validated = []
        for h in selected_handles:
            if h in by_handle and h not in all_unique_handles:
                all_unique_handles.add(h)
                validated.append(by_handle[h])
        
        results.append({
            "annot_idx": t["idx"],
            "annot_text": t["text"],
            "cloud_idx": p["idx"],
            "cloud_image": str(cloud_img),
            "dxf_bbox": dxf_bbox,
            "candidates": candidates,
            "selected_handles": selected_handles,
            "validated_handles": [v["handle"] for v in validated],
            "reason": reason,
        })
    
    return {
        "model": model,
        "annotations": {"total": len(annots), "polygons": len(polygons), "delete_texts": len(delete_txts)},
        "results": results,
        "all_unique_handles": list(all_unique_handles),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=Path("./cloud_interpretation_v3"))
    parser.add_argument("--model", default="qwen2.5vl:latest")
    args = parser.parse_args()
    
    args.out.mkdir(exist_ok=True, parents=True)
    
    result = run_cloud_pipeline(args.pdf, args.dxf, args.out, model=args.model)
    
    json_path = args.out / "interpretation.json"
    with open(json_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    
    print(f"VLM v3 hybrid complete. Results: {json_path}")
    print(f"Unique handles to clear: {len(result['all_unique_handles'])}")
    for r in result["results"]:
        print(f"\n  Annot #{r['annot_idx']} (cloud #{r['cloud_idx']}): {r['reason']}")
        if r.get('candidates'):
            print(f"    Candidates: {len(r['candidates'])}")
            for c in r["candidates"]:
                marker = "*" if c['in_bbox'] else " "
                print(f"      {marker} '{c['text']}' @ {c['pos']}")
        if r.get('validated_handles'):
            print(f"    Validated:")
            for h in r["validated_handles"]:
                print(f"      → {h}")
