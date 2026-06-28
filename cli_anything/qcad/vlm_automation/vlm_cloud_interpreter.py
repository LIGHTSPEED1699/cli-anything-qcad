#!/usr/bin/env python3
"""
VLM Cloud Interpreter — identifies text entities inside cloud polygon regions.

Uses Qwen2.5-VL (or llava) to visually inspect each cloud region and report
the text labels of objects inside the cloud. Maps those labels back to DXF
entity handles via the entity index.

Usage:
    python3 vlm_cloud_interpreter.py --pdf 1.pdf --dxf 1.dxf --cloud-idx 0
"""
import argparse
import base64
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
import fitz
import ezdxf


def encode_image_base64(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def call_ollama_vision(model: str, prompt: str, image_paths: List[Path]) -> str:
    """Call Ollama vision model via curl."""
    images_b64 = [encode_image_base64(p) for p in image_paths]
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": images_b64,
            }
        ],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    cmd = [
        "curl", "-s", "http://localhost:11434/api/chat",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(payload),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Ollama call failed: {result.stderr}")
    try:
        data = json.loads(result.stdout)
        return data["message"]["content"]
    except (json.JSONDecodeError, KeyError) as e:
        raise RuntimeError(f"Failed to parse Ollama response: {e}\nRaw: {result.stdout[:500]}")


def extract_cloud_region(pdf_path: Path, annot_idx: int, out_dir: Path, zoom: float = 2.0, pad: int = 100) -> Path:
    """Render a cloud polygon annotation region to PNG."""
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    annots = list(page.annots())
    a = annots[annot_idx]
    r = a.rect
    pw, ph = page.rect.width, page.rect.height
    cx0 = max(0, r.x0 - pad)
    cy0 = max(0, r.y0 - pad)
    cx1 = min(pw, r.x1 + pad)
    cy1 = min(ph, r.y1 + pad)
    clip = fitz.Rect(cx0, cy0, cx1, cy1)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(clip=clip, matrix=mat)
    out_path = out_dir / f"cloud_{annot_idx}_{zoom}x.png"
    pix.save(str(out_path))
    return out_path


def find_text_in_dxf(dxf_path: Path, search_text: str) -> List[Dict[str, Any]]:
    """Find TEXT/MTEXT entities matching search_text. Returns list of dicts."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    matches = []
    for ent in msp:
        if ent.dxftype() == 'TEXT':
            t = ent.dxf.text.strip()
            if search_text.lower() in t.lower() or t.lower() in search_text.lower():
                matches.append({
                    "handle": ent.dxf.handle,
                    "type": "TEXT",
                    "text": t,
                    "pos": (round(ent.dxf.insert.x, 4), round(ent.dxf.insert.y, 4)),
                })
        elif ent.dxftype() == 'MTEXT':
            t = ent.text.strip()
            if search_text.lower() in t.lower() or t.lower() in search_text.lower():
                matches.append({
                    "handle": ent.dxf.handle,
                    "type": "MTEXT",
                    "text": t,
                    "pos": (round(ent.dxf.insert.x, 4), round(ent.dxf.insert.y, 4)),
                })
    return matches


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
    response = call_ollama_vision(model, prompt, [cloud_image])
    labels = []
    for line in response.strip().split('\n'):
        line = line.strip().strip('"').strip("'")
        if line and line.upper() != "NONE" and not line.startswith("-"):
            labels.append(line)
    return labels


def run_cloud_pipeline(pdf_path: Path, dxf_path: Path, out_dir: Path, model: str = "qwen2.5vl:latest") -> Dict[str, Any]:
    """Run full cloud interpretation pipeline."""
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
    def dist(a, b):
        return ((a["rect"][0]+a["rect"][2])/2 - (b["rect"][0]+b["rect"][2])/2)**2 + \
               ((a["rect"][1]+a["rect"][3])/2 - (b["rect"][1]+b["rect"][3])/2)**2
    
    associations = []
    for t in delete_txts:
        nearest = min(polygons, key=lambda p: dist(t, p)) if polygons else None
        associations.append({"text": t, "polygon": nearest})
    
    results = []
    all_handles = set()
    
    for assoc in associations:
        t, p = assoc["text"], assoc["polygon"]
        if not p:
            results.append({
                "annot_idx": t["idx"],
                "annot_text": t["text"],
                "cloud_idx": None,
                "cloud_image": None,
                "vlm_labels": [],
                "matched_handles": [],
                "reason": "no_polygon",
            })
            continue
        
        # Render cloud region
        cloud_img = extract_cloud_region(pdf_path, p["idx"], out_dir, zoom=2.0, pad=100)
        
        # Ask VLM
        try:
            labels = interpret_cloud(model, cloud_img, t["text"])
        except Exception as e:
            labels = []
            reason = f"vlm_error: {e}"
        else:
            reason = f"vlm_ok: {len(labels)} labels"
        
        # Map labels to DXF handles
        matched = []
        for lbl in labels:
            hits = find_text_in_dxf(dxf_path, lbl)
            for h in hits:
                if h["handle"] not in all_handles:
                    all_handles.add(h["handle"])
                    matched.append(h)
        
        results.append({
            "annot_idx": t["idx"],
            "annot_text": t["text"],
            "cloud_idx": p["idx"],
            "cloud_image": str(cloud_img),
            "vlm_labels": labels,
            "matched_handles": matched,
            "reason": reason,
        })
    
    return {
        "model": model,
        "annotations": {"total": len(annots), "polygons": len(polygons), "delete_texts": len(delete_txts)},
        "results": results,
        "all_unique_handles": list(all_handles),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=Path("./cloud_interpretation"))
    parser.add_argument("--model", default="qwen2.5vl:latest")
    args = parser.parse_args()
    
    args.out.mkdir(exist_ok=True)
    
    result = run_cloud_pipeline(args.pdf, args.dxf, args.out, model=args.model)
    
    # Save JSON
    json_path = args.out / "interpretation.json"
    with open(json_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    
    print(f"Cloud interpretation complete. Results saved to {json_path}")
    print(f"Total unique handles to clear: {len(result['all_unique_handles'])}")
    for r in result["results"]:
        print(f"  Annot #{r['annot_idx']} (cloud #{r['cloud_idx']}): {r['reason']}")
        for h in r["matched_handles"]:
            print(f"    → {h['handle']} '{h['text']}'")
