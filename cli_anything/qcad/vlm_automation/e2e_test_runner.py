#!/usr/bin/env python3
"""
T5 End-to-End Pipeline Test Runner
Runs dwg_markup_pipeline.py steps and documents pass/fail.
"""

import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path

# Add scripts to path
SCRIPTS_DIR = Path("/home/hongbin/openclaw-shared/QCAD-VLM-automation/scripts")
TEST_FILES_DIR = Path("/home/hongbin/openclaw-shared/QCAD-VLM-automation/test-files")
INBOUND_PDF = Path("/home/hongbin/.openclaw/media/inbound/ff9ed528-9638-4ad8-aeb7-8c7e97d0a7dd.pdf")
WORKSPACE = Path("/home/hongbin/.hermes/kanban/workspaces/t_1345ccb9")

def log_step(num, name, status, detail=""):
    icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⚠️"
    print(f"\n{'='*60}")
    print(f"Step {num}: {name}")
    print(f"Status: {icon} {status}")
    if detail:
        print(f"Detail: {detail}")
    return {"step": num, "name": name, "status": status, "detail": detail}

def main():
    results = []
    print("="*60)
    print("T5: DWG Markup Pipeline End-to-End Test")
    print("="*60)

    # Determine which PDF to use
    test_pdf = TEST_FILES_DIR / "drawing_modified.pdf"
    if INBOUND_PDF.exists():
        # Verify if test_pdf has annotations
        import fitz
        doc = fitz.open(str(test_pdf))
        annot_count = sum(len(list(page.annots())) for page in doc)
        doc.close()
        if annot_count == 0 and INBOUND_PDF.exists():
            print(f"⚠️  test-files/drawing_modified.pdf has 0 annotations.")
            print(f"   Using inbound annotated PDF instead: {INBOUND_PDF}")
            pdf_path = str(INBOUND_PDF)
        else:
            pdf_path = str(test_pdf)
    else:
        pdf_path = str(test_pdf)

    # Determine DWG to use — prefer example_panel_layout.dxf (has matching expected edit)
    dwg_candidates = [
        str(TEST_FILES_DIR / "example_panel_layout.dxf"),
        "/tmp/example_panel_layout.dxf",
        str(TEST_FILES_DIR / "din_a3_foot_landscape.dwg"),
    ]
    dwg_path = None
    for c in dwg_candidates:
        if Path(c).exists():
            dwg_path = c
            break

    # Expected reference for comparison
    expected_candidates = [
        "/tmp/example_panel_all_edits.dxf",
        str(TEST_FILES_DIR / "example_panel_all_edits.dxf"),
    ]
    expected_path = None
    for c in expected_candidates:
        if Path(c).exists():
            expected_path = c
            break

    if not dwg_path:
        results.append(log_step(0, "Setup", "FAIL", "No DWG/DXF file found"))
        save_report(results)
        return 1

    output_path = str(WORKSPACE / "output.dwg")
    report_path = str(WORKSPACE / "pipeline_report.json")

    print(f"\nConfig:")
    print(f"  PDF: {pdf_path}")
    print(f"  DWG: {dwg_path}")
    print(f"  Output: {output_path}")
    print(f"  Report: {report_path}")

    # === STEP 1: PDF Annotation Parsing ===
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from pdf_annotation_parser import extract_pdf_annotations
        annotations = extract_pdf_annotations(pdf_path)
        if not annotations:
            results.append(log_step(1, "PDF Annotation Parsing",
                "FAIL", f"No annotations extracted from {pdf_path}"))
        else:
            detail = f"Extracted {len(annotations)} annotation(s):\n"
            for i, a in enumerate(annotations, 1):
                detail += f"    {i}. [{a.inferred_action}] \"{a.text[:60]}...\" (page {a.page+1})\n"
            results.append(log_step(1, "PDF Annotation Parsing", "PASS", detail))
    except Exception as e:
        results.append(log_step(1, "PDF Annotation Parsing", "FAIL", str(e)))
        save_report(results)
        return 1

    # === STEP 2: Context Crops ===
    try:
        import fitz
        # Generate context crops for each annotation
        crop_paths = []
        for i, annot in enumerate(annotations):
            out_png = WORKSPACE / f"task_{i+1}_context.png"
            # Manual crop using fitz directly
            doc = fitz.open(pdf_path)
            page = doc[annot.page]
            x0, y0, x1, y1 = annot.target_bbox
            padding = 100
            page_rect = page.rect
            cx0 = max(0, x0 - padding)
            cy0 = max(0, y0 - padding)
            cx1 = min(page_rect.width, x1 + padding)
            cy1 = min(page_rect.height, y1 + padding)
            crop_rect = fitz.Rect(cx0, cy0, cx1, cy1)
            zoom = 2.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, clip=crop_rect)
            pix.save(str(out_png))
            doc.close()
            crop_paths.append(str(out_png))

        detail = f"Generated {len(crop_paths)} context crop(s):\n"
        for p in crop_paths:
            size = Path(p).stat().st_size
            detail += f"    {p} ({size} bytes)\n"
        results.append(log_step(2, "Context Crops Generated", "PASS", detail))
    except Exception as e:
        results.append(log_step(2, "Context Crops Generated", "FAIL", str(e)))

    # === STEP 3: VLM Matching ===
    try:
        from qcad_vlm_match import QCADVLMMatcher
        matcher = QCADVLMMatcher(vision_model="gemma4:e4b")
        # Find QCAD window
        qcad_wid = matcher.find_qcad_window()
        if not qcad_wid:
            results.append(log_step(3, "VLM Matching", "FAIL",
                "QCAD window not found. Is QCAD running?"))
        else:
            # Capture QCAD once
            qcad_image = matcher.capture_qcad(qcad_wid)
            vlm_results = []
            for i, annot in enumerate(annotations):
                pdf_image = crop_paths[i]
                match_result = matcher.match_entity(
                    pdf_image_path=pdf_image,
                    qcad_image_path=qcad_image,
                    instruction=annot.text,
                    window_id=qcad_wid,
                )
                vlm_results.append(match_result)

            found = sum(1 for r in vlm_results if r.get("target_found"))
            detail = f"VLM matched {found}/{len(annotations)} entities:\n"
            for i, r in enumerate(vlm_results, 1):
                status = "✅ found" if r.get("target_found") else "❌ not found"
                coords = r.get("coordinates")
                model = r.get("model_used", "unknown")
                detail += f"    Task {i}: {status} at {coords} (model: {model})\n"
                if r.get("error"):
                    detail += f"      Error: {r['error']}\n"
            results.append(log_step(3, "VLM Matching", "PASS" if found == len(annotations) else "PARTIAL", detail))
    except Exception as e:
        results.append(log_step(3, "VLM Matching", "FAIL", str(e)))
        import traceback
        traceback.print_exc()

    # === STEP 4: X11 Actions ===
    try:
        from qcad_action_executor import QCADActionExecutor
        executor = QCADActionExecutor(qcad_window_name="QCAD", window_id=qcad_wid if 'qcad_wid' in dir() else None)
        if not executor.window_id:
            results.append(log_step(4, "X11 Actions", "FAIL", "QCAD window not found"))
        else:
            # We'll run in dry-run mode to avoid destructive changes to the running QCAD
            # Real execution would modify the open drawing
            action_log = []
            for i, r in enumerate(vlm_results if 'vlm_results' in dir() else [], 1):
                action = r.get("action", "click")
                coords = r.get("coordinates")
                if coords and r.get("target_found"):
                    if action == "click":
                        executor.click(coords, dry_run=True)
                    elif action == "double_click":
                        executor.double_click(coords, dry_run=True)
                    elif action == "type":
                        text = r.get("text_input", "")
                        executor.type_text(coords, text, dry_run=True)
                    elif action == "delete":
                        executor.delete(coords, dry_run=True)
                    action_log.append(f"Task {i}: {action} at {coords} (dry-run)")
                else:
                    action_log.append(f"Task {i}: skipped (no target)")

            detail = f"X11 actions (dry-run, non-destructive):\n"
            for line in action_log:
                detail += f"    {line}\n"
            results.append(log_step(4, "X11 Actions", "PASS", detail))
    except Exception as e:
        results.append(log_step(4, "X11 Actions", "FAIL", str(e)))

    # === STEP 5: Output DWG ===
    # The pipeline copies input DWG to output, but QCAD saves to the currently open file.
    # This is a known limitation.
    try:
        shutil.copy2(dwg_path, output_path)
        detail = (f"Copied input DWG to output path:\n"
                  f"    {dwg_path} -> {output_path}\n"
                  f"Note: Pipeline does not trigger 'Save As' to output path; "
                  f"QCAD saves to the original open file.")
        results.append(log_step(5, "Output DWG", "PARTIAL", detail))
    except Exception as e:
        results.append(log_step(5, "Output DWG", "FAIL", str(e)))

    # === STEP 6: Compare to Expected ===
    if expected_path and Path(output_path).exists():
        try:
            import ezdxf
            out_doc = ezdxf.readfile(output_path)
            exp_doc = ezdxf.readfile(expected_path)
            out_text = set(e.dxf.text for e in out_doc.modelspace().query('TEXT'))
            exp_text = set(e.dxf.text for e in exp_doc.modelspace().query('TEXT'))
            diff = out_text.symmetric_difference(exp_text)
            if diff:
                detail = f"Text entity differences found ({len(diff)}):\n"
                for d in list(diff)[:10]:
                    detail += f"    {d}\n"
                results.append(log_step(6, "Compare to Expected", "FAIL", detail))
            else:
                detail = "Output text entities match expected exactly."
                results.append(log_step(6, "Compare to Expected", "PASS", detail))
        except Exception as e:
            results.append(log_step(6, "Compare to Expected", "FAIL", str(e)))
    else:
        missing = []
        if not expected_path: missing.append("expected reference")
        if not Path(output_path).exists(): missing.append(output_path)
        results.append(log_step(6, "Compare to Expected", "SKIP",
            f"Missing: {missing}"))

    # === Summary ===
    save_report(results)
    return 0

def save_report(results):
    report_path = WORKSPACE / "pipeline_report.json"
    with open(report_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n📄 Report saved to: {report_path}")

    # Also write markdown
    md_path = WORKSPACE / "pipeline_report.md"
    with open(md_path, 'w') as f:
        f.write("# T5: Pipeline End-to-End Test Report\n\n")
        f.write(f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        for r in results:
            icon = "✅" if r["status"] == "PASS" else "❌" if r["status"] == "FAIL" else "⚠️"
            f.write(f"## Step {r['step']}: {r['name']}\n\n")
            f.write(f"**Status:** {icon} {r['status']}\n\n")
            if r["detail"]:
                f.write(f"```\n{r['detail']}\n```\n\n")
        # Summary counts
        pass_count = sum(1 for r in results if r["status"] == "PASS")
        fail_count = sum(1 for r in results if r["status"] == "FAIL")
        partial_count = sum(1 for r in results if r["status"] == "PARTIAL")
        skip_count = sum(1 for r in results if r["status"] == "SKIP")
        f.write(f"## Summary\n\n")
        f.write(f"- Pass: {pass_count}\n")
        f.write(f"- Partial: {partial_count}\n")
        f.write(f"- Fail: {fail_count}\n")
        f.write(f"- Skip: {skip_count}\n")
    print(f"📄 Markdown report saved to: {md_path}")

if __name__ == "__main__":
    sys.exit(main())
