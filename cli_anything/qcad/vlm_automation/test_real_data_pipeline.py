#!/usr/bin/env python3
"""
Real-data Phase B pipeline test using actual PDF annotations + DXF drawings.
Reads annotation text from PDFs, routes through tiered pipeline, logs results.
Usage:
    python3 test_real_data_pipeline.py                    # deterministic mock mode (fast)
    python3 test_real_data_pipeline.py --live-vlm          # real Ollama calls (slow)
    python3 test_real_data_pipeline.py --pair 2 --live-vlm # single pair live
"""

import argparse
import base64
import json
import sys
import os
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

import fitz  # PyMuPDF

# --- project imports (add parent to path) ---
PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

from tier_router import TierRouter
from confidence_scorer import ConfidenceScorer
from audit_logger import AuditLogger
from review_queue import ReviewQueue
from vlm_client import VLMClient
from vlm_instruction_parser import InstructionParser

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
DATA_DIR = Path("/home/hongbin/.hermes/kanban/workspaces/testfiles_2026.05.07")
OUT_DIR = DATA_DIR / "pipeline_output"
OUT_DIR.mkdir(exist_ok=True)

PDF_FILES = {n: DATA_DIR / f"{n}.pdf" for n in ["1", "2", "3"]}
DXF_FILES = {n: DATA_DIR / f"{n}.dxf" for n in ["1", "2", "3"]}


# ------------------------------------------------------------------
# PDF annotation extractor
# ------------------------------------------------------------------

@dataclass
class PdfAnnotation:
    annot_index: int
    page: int
    annot_type: str
    text: str
    rect: tuple
    image_path: Optional[str] = None


def extract_annotations(pdf_path: Path) -> list[PdfAnnotation]:
    doc = fitz.open(str(pdf_path))
    out = []
    for page_num, page in enumerate(doc):
        for i, a in enumerate(page.annots()):
            text = a.get_text() or ""
            r = a.rect
            out.append(PdfAnnotation(
                annot_index=i,
                page=page_num,
                annot_type=a.type[1],
                text=text.strip(),
                rect=(r.x0, r.y0, r.x1, r.y1),
            ))
    return out


def render_annot_image(pdf_path: Path, annot: PdfAnnotation, out_path: Path) -> Optional[Path]:
    """Render the annotation region with padding (for VLM input)."""
    x0, y0, x1, y1 = annot.rect
    if x1 - x0 <= 1 or y1 - y0 <= 1:
        return None  # Skip degenerate annotations (circles, lines as markers)
    doc = fitz.open(str(pdf_path))
    page = doc[annot.page]
    r = fitz.Rect(annot.rect)
    expanded = fitz.Rect(r.x0 - max(50, r.width * 0.2), r.y0 - max(50, r.height * 0.2),
                         r.x1 + max(50, r.width * 0.2), r.y1 + max(50, r.height * 0.2)) & page.rect
    if expanded.is_empty:
        return None
    mat = fitz.Matrix(2, 2)
    pix = page.get_pixmap(clip=expanded, matrix=mat)
    pix.save(str(out_path))
    return out_path


# ------------------------------------------------------------------
# Mock / live VLM helpers
# ------------------------------------------------------------------

class MockVLMClient:
    """Deterministic mock that returns structured JSON via the VLMClient.chat interface."""

    def _build_response(self, prompt: str, images=None) -> 'VLMResponse':
        from vlm_client import VLMResponse
        content = self._generate_content(prompt)
        return VLMResponse(raw_text=content, parsed_json=json.loads(content))

    def _generate_content(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        if "change" in prompt_lower and "to" in prompt_lower:
            parts = re.split(r'\s+to\s+', prompt, flags=re.I)
            old = parts[0].replace("Change", "").strip().strip('"').strip("'")
            new = parts[1].strip().strip('"').strip("'") if len(parts) > 1 else ""
            return json.dumps({
                "action_type": "replace_text",
                "target_name": old or "UNKNOWN",
                "replacement_name": new or "UNKNOWN",
                "location_hint": [100.0, 200.0],
                "confidence": 0.85,
                "reasoning": f"Mock: replace '{old}' with '{new}'"
            })
        if "delete" in prompt_lower or "remove" in prompt_lower:
            return json.dumps({
                "action_type": "delete_entities",
                "target_name": "clouded" if "clouded" in prompt_lower else ("circled" if "circled" in prompt_lower else "marked"),
                "replacement_name": "",
                "location_hint": [200.0, 300.0],
                "confidence": 0.78,
                "reasoning": "Mock: delete detected"
            })
        if "add" in prompt_lower and "row" in prompt_lower:
            return json.dumps({
                "action_type": "add_row",
                "target_name": "wire_table",
                "replacement_name": prompt.strip(),
                "location_hint": [300.0, 400.0],
                "confidence": 0.72,
                "reasoning": "Mock: table row addition"
            })
        if "copy" in prompt_lower and "wires" in prompt_lower:
            return json.dumps({
                "action_type": "copy_entities",
                "target_name": "wires",
                "replacement_name": "PLC22, CA-1452",
                "location_hint": [500.0, 600.0],
                "confidence": 0.55,
                "reasoning": "Mock: complex multi-step copy (low confidence)"
            })
        if "mark spare" in prompt_lower:
            return json.dumps({
                "action_type": "modify_text",
                "target_name": "wire_ends",
                "replacement_name": "spare",
                "location_hint": [150.0, 250.0],
                "confidence": 0.80,
                "reasoning": "Mock: mark spare"
            })
        if "blk" in prompt_lower:
            return json.dumps({
                "action_type": "replace_block",
                "target_name": "original_block",
                "replacement_name": "BLK",
                "location_hint": [400.0, 300.0],
                "confidence": 0.65,
                "reasoning": "Mock: add/replace block"
            })
        return json.dumps({
            "action_type": "unknown",
            "target_name": "",
            "replacement_name": "",
            "location_hint": [0.0, 0.0],
            "confidence": 0.40,
            "reasoning": "Mock: unparseable"
        })

    def chat(self, messages, stream=False):
        from vlm_client import VLMResponse
        prompt = messages[0].get("content", "") if messages else ""
        content = self._generate_content(prompt)
        return VLMResponse(raw_text=content, parsed_json=json.loads(content))

    def encode_image(self, image):
        return ""  # no-op for mock


# ------------------------------------------------------------------
# Pipeline runner
# ------------------------------------------------------------------

@dataclass
class PipelineResult:
    pair_id: str
    annotation_index: int
    annotation_text: str
    annot_type: str
    tier: str
    route_reason: str
    instruction: dict = field(default_factory=dict)
    confidence_report: dict = field(default_factory=dict)
    audit_id: Optional[str] = None
    review_id: Optional[int] = None
    passed: bool = False


def run_pipeline(pair_id: str, annot: PdfAnnotation, use_live_vlm: bool = False) -> PipelineResult:
    """Run one annotation through the full Phase B pipeline."""

    result = PipelineResult(
        pair_id=pair_id,
        annotation_index=annot.annot_index,
        annotation_text=annot.text,
        annot_type=annot.annot_type,
        tier="",
        route_reason="",
    )

    # 1. Tier Router
    router = TierRouter()
    route_result = router.route(annot.text)
    result.tier = str(route_result.tier)
    result.route_reason = route_result.reasoning

    # 2. Mock / live VLM
    if use_live_vlm:
        parser = InstructionParser(model="qwen2.5vl:latest")
    else:
        parser = InstructionParser()
        parser.client = MockVLMClient()

    # 3. Parse instruction
    img_path = render_annot_image(
        PDF_FILES[pair_id], annot,
        OUT_DIR / f"pair{pair_id}_annot{annot.annot_index}.png"
    )
    if use_live_vlm and img_path is None:
        print(f"       WARNING: image crop is empty for annot {annot.annot_index}; using text-only VLM call")
        instruction = parser.parse(annot.text)
    elif use_live_vlm:
        instruction = parser.parse(annot.text, image=str(img_path))
    else:
        instruction = parser.parse(annot.text)
    result.instruction = asdict(instruction)

    # 4. Confidence scoring
    scorer = ConfidenceScorer()
    conf = scorer.score(
        annotation_confidence=result.instruction.get("confidence", 0.5),
        metadata_distance_px=result.instruction.get("location_hint", [0, 0])[0],
        coordinate_variance_px=result.instruction.get("location_hint", [0, 0])[1],
        post_action_confidence=result.instruction.get("confidence", 0.5),
    )
    result.confidence_report = asdict(conf)
    result.passed = conf.overall_passed

    # 5. Audit log
    logger = AuditLogger(db_path=str(OUT_DIR / "audit_log.db"), jsonl_path=str(OUT_DIR / "audit_log.jsonl"))
    result.audit_id = logger.log(
        tier=tier_to_int(result.tier),
        annotation=annot.text,
        parsed_instruction=result.instruction,
        confidence_report=result.confidence_report,
        verification_status="PASSED" if result.passed else "REVIEW",
        before_file=str(DATA_DIR / f"{pair_id}.dxf"),
        after_file=None,
    )

    # 6. Human review queue for anything flagged
    if not result.passed or conf.human_review_required:
        q = ReviewQueue(db_path=str(OUT_DIR / "review_queue.db"))
        result.review_id = q.enqueue(
            annotation=annot.text,
            tier=tier_to_int(result.tier),
            parsed_json=result.instruction,
            confidence_report=result.confidence_report,
            original_file=str(DATA_DIR / f"{pair_id}.dxf"),
        )

    return result


def tier_to_int(tier_str: str) -> int:
    """Convert tier string like 'Tier.EZDXF' or 'T1' to integer."""
    if isinstance(tier_str, int):
        return tier_str
    mapping = {
        "EZDXF": 1, "QCAD": 2, "ODA": 3, "VLM_X11": 4,
        "Tier.EZDXF": 1, "Tier.QCAD": 2, "Tier.ODA": 3, "Tier.VLM_X11": 4,
        "T1": 1, "T2": 2, "T3": 3, "T4": 4,
    }
    return mapping.get(tier_str, 4)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", choices=["1", "2", "3", "all"], default="all")
    parser.add_argument("--live-vlm", action="store_true", help="Use real Ollama (slow)")
    parser.add_argument("--annot-index", type=int, default=None, help="Single annotation index")
    args = parser.parse_args()

    targets = ["1", "2", "3"] if args.pair == "all" else [args.pair]

    all_results: list[dict] = []
    for pid in targets:
        pdf_path = PDF_FILES[pid]
        annots = extract_annotations(pdf_path)
        print(f"\n{'='*60}")
        print(f"PAIR {pid}: {pdf_path.name} ({len(annots)} annotations)")
        print(f"{'='*60}")

        indices = [args.annot_index] if args.annot_index is not None else range(len(annots))
        for idx in indices:
            if idx >= len(annots):
                print(f"  SKIP: annot index {idx} out of range ({len(annots)})")
                continue
            a = annots[idx]
            # In live VLM mode, skip empty geometry markers (Polygons/Lines with no text)
            if args.live_vlm and not a.text.strip():
                continue
            res = run_pipeline(pid, a, use_live_vlm=args.live_vlm)
            all_results.append(asdict(res))
            print(f"  [{idx}] {a.annot_type}: {repr(a.text[:50])}")
            print(f"       tier={res.tier}, passed={res.passed}, confidence={res.confidence_report.get('composite_score', 0):.2f}")
            if res.review_id:
                print(f"       REVIEW QUEUE id={res.review_id}")

    # Summary
    total = len(all_results)
    passed = sum(1 for r in all_results if r["passed"])
    queued = sum(1 for r in all_results if r["review_id"] is not None)
    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed}/{total} passed, {queued} queued for review")
    print(f"Output dir: {OUT_DIR}")
    print(f"{'='*60}")

    report_path = OUT_DIR / "pipeline_report.json"
    with open(report_path, "w") as f:
        json.dump({
            "summary": {
                "total": total,
                "passed": passed,
                "queued": queued,
                "mode": "live_vlm" if args.live_vlm else "mock"
            },
            "results": all_results,
        }, f, indent=2, default=str)
    print(f"Report written: {report_path}")


if __name__ == "__main__":
    main()
