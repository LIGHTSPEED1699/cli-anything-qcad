#!/usr/bin/env python3
"""
Executes DXF edits for high-confidence annotations and generates review packages.
Produces modified DXF + PDF for each pair.
"""
import argparse
import json
import fitz
from pathlib import Path
from dataclasses import dataclass

import ezdxf
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tier_router import TierRouter
from confidence_scorer import ConfidenceScorer
from audit_logger import AuditLogger
from review_queue import ReviewQueue
from vlm_client import VLMClient
from vlm_instruction_parser import InstructionParser

DATA_DIR = Path("/home/hongbin/.hermes/kanban/workspaces/testfiles_2026.05.07")
OUT_DIR  = DATA_DIR / "pipeline_output"
OUT_DIR.mkdir(exist_ok=True)

@dataclass
class PdfAnnotation:
    idx: int; page: int; annot_type: str; text: str; rect: tuple

def extract_annots(pdf_path: Path):
    doc = fitz.open(str(pdf_path))
    out = []
    for pn, page in enumerate(doc):
        for i, a in enumerate(page.annots()):
            out.append(PdfAnnotation(i, pn, a.type[1], (a.get_text() or "").strip(), (a.rect.x0,a.rect.y0,a.rect.x1,a.rect.y1)))
    return out

def find_text_in_dxf(dxf_path: Path, search_text: str):
    """Find TEXT/MTEXT entities matching search_text."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    matches = []
    for ent in msp:
        if ent.dxftype() == 'TEXT':
            t = ent.dxf.text.strip()
            if not t.startswith('\\\\'):
                if search_text.lower() in t.lower():
                    matches.append((ent.dxftype(), t, ent.dxf.handle, (ent.dxf.insert.x, ent.dxf.insert.y)))
        elif ent.dxftype() == 'MTEXT':
            t = ent.text.strip()
            if not t.startswith('\\\\'):
                if search_text.lower() in t.lower():
                    matches.append((ent.dxftype(), t, ent.dxf.handle, (ent.dxf.insert.x, ent.dxf.insert.y)))
    return matches

def find_entities_in_rect(dxf_path: Path, bbox, margin=5.0):
    """Find DXF entities whose center is inside bbox (x0,y0,x1,y1) world coords."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    matches = []
    def inside(pos):
        return bbox[0]-margin <= pos[0] <= bbox[2]+margin and bbox[1]-margin <= pos[1] <= bbox[3]+margin
    for ent in msp:
        try:
            if ent.dxftype() in ('TEXT','MTEXT'):
                pos = (ent.dxf.insert.x, ent.dxf.insert.y) if ent.dxftype()=='TEXT' else (ent.dxf.insert.x, ent.dxf.insert.y)
            elif ent.dxftype() in ('LWPOLYLINE','LINE'):
                bb = ent.get_bbox()
                pos = ((bb[0][0]+bb[1][0])/2, (bb[0][1]+bb[1][1])/2)
            elif hasattr(ent, 'dxf') and hasattr(ent.dxf, 'center'):
                pos = (ent.dxf.center.x, ent.dxf.center.y)
            else:
                continue
            if inside(pos):
                matches.append((ent.dxftype(), getattr(ent.dxf, 'text', getattr(ent, 'text', '')), ent.dxf.handle, pos))
        except Exception:
            continue
    return matches


def execute_text_replace(dxf_path, old_text, new_text):
    """Replace first matching TEXT/MTEXT entity. Returns count."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    count = 0
    for ent in msp:
        if ent.dxftype() == 'TEXT':
            if old_text.lower() in ent.dxf.text.strip().lower():
                ent.dxf.text = ent.dxf.text.replace(old_text, new_text, 1)
                count += 1
                break
        elif ent.dxftype() == 'MTEXT':
            if old_text.lower() in ent.text.lower():
                ent.text = ent.text.replace(old_text, new_text, 1)
                count += 1
                break
    doc.saveas(str(dxf_path).replace('.dxf', '_MODIFIED.dxf'))
    return count


def pdf_to_dxf_scale(pdf_path, dxf_path):
    """Approximate scale factor from PDF page to DXF extents."""
    doc_pdf = fitz.open(str(pdf_path))
    page = doc_pdf[0]
    pdf_w = page.rect.width
    pdf_h = page.rect.height

    doc_dxf = ezdxf.readfile(dxf_path)
    msp = doc_dxf.modelspace()
    # Compute extents manually
    pts = []
    for ent in msp:
        if ent.dxftype() == 'TEXT':
            pts.append((ent.dxf.insert.x, ent.dxf.insert.y))
        elif ent.dxftype() == 'MTEXT':
            pts.append((ent.dxf.insert.x, ent.dxf.insert.y))
        elif ent.dxftype() == 'LWPOLYLINE':
            for v in ent.get_points('xy'):
                pts.append(v)
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        dxf_w = max(xs) - min(xs)
        dxf_h = max(ys) - min(ys)
        return dxf_w / pdf_w, dxf_h / pdf_h, min(xs), min(ys)
    return 1.0, 1.0, 0.0, 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", choices=["1","2","3","all"], default="all")
    parser.add_argument("--live-vlm", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Actually modify DXF files")
    args = parser.parse_args()

    targets = ["1","2","3"] if args.pair == "all" else [args.pair]

    all_results = []
    for pid in targets:
        pdf_p = DATA_DIR / f"{pid}.pdf"
        dxf_p = DATA_DIR / f"{pid}.dxf"
        annots = extract_annots(pdf_p)
        print(f"\n{'='*60}")
        print(f"PAIR {pid}: {pdf_p.name}")
        print(f"{'='*60}")

        scale_x, scale_y, offset_x, offset_y = pdf_to_dxf_scale(pdf_p, dxf_p)
        print(f"  Scale: PDF→DXF = {scale_x:.3f}, {scale_y:.3f}  offset=({offset_x:.1f},{offset_y:.1f})")

        # Tier routing
        router = TierRouter()
        for a in annots:
            if not a.text.strip():
                continue
            route = router.route(a.text)
            scorer = ConfidenceScorer()

            if route.tier.value == 'T1' or route.tier == 'Tier.EZDXF':
                # Try to parse and execute
                if "change" in a.text.lower() and "to" in a.text.lower():
                    # Find text being changed
                    parts = a.text.replace('Change','').strip().split(' to ')
                    old = parts[0].strip().strip('\"').strip("'") if parts else ""
                    new = parts[1].strip().strip('\"').strip("'") if len(parts)>1 else ""
                    matches = find_text_in_dxf(dxf_p, old)
                    if len(matches) == 1:
                        print(f"  [{a.idx}] {a.text[:40]} → CONFIDENT: replace '{matches[0][1]}' with '{new}'")
                        if args.execute:
                            n = execute_text_replace(dxf_p, matches[0][1], new)
                            print(f"       EXECUTED: {n} entity replaced")
                        else:
                            print(f"       DRY-RUN: would replace '{matches[0][1]}' with '{new}'")
                    elif len(matches) > 1:
                        print(f"  [{a.idx}] {a.text[:40]} → AMBIGUOUS: found {len(matches)} matches")
                        for m in matches:
                            print(f"       handle={m[2]} text={m[1]} pos={m[3]}")
                    else:
                        print(f"  [{a.idx}] {a.text[:40]} → NOT FOUND: no match for '{old}'")

                elif "delete" in a.text.lower():
                    # Find entities within annotation box
                    dxf_rect = [a.rect[0]*scale_x+offset_x, a.rect[1]*scale_y+offset_y,
                                a.rect[2]*scale_x+offset_x, a.rect[3]*scale_y+offset_y]
                    inside = find_entities_in_rect(dxf_p, dxf_rect)
                    print(f"  [{a.idx}] {a.text[:40]} → SPATIAL: {len(inside)} entities in bbox")
                    for e in inside:
                        print(f"       {e[0]}: {e[1][:40] if e[1] else '(no text)'} handle={e[2]}")

                elif "remove" in a.text.lower():
                    # Find entities within annotation box (cloud or circle)
                    dxf_rect = [a.rect[0]*scale_x+offset_x, a.rect[1]*scale_y+offset_y,
                                a.rect[2]*scale_x+offset_x, a.rect[3]*scale_y+offset_y]
                    inside = find_entities_in_rect(dxf_p, dxf_rect)
                    print(f"  [{a.idx}] {a.text[:40]} → SPATIAL: {len(inside)} entities in bbox")
                    for e in inside:
                        print(f"       {e[0]}: {e[1][:40] if e[1] else '(no text)'} handle={e[2]}")

                else:
                    print(f"  [{a.idx}] {a.text[:40]} → UNHANDLED: {route.reasoning}")
            else:
                print(f"  [{a.idx}] {a.text[:40]} → TIER={route.tier} (not T1)")

    # Summary
    print(f"\n{'='*60}")
    print(f"Execution complete. Modified files in: {DATA_DIR}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
