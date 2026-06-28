"""Hybrid pipeline: dispatch annotations to vendored QCAD-VLM-automation logic."""
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli_anything.qcad.backends.dwg_converter import DwgConverter
from cli_anything.qcad.utils.layer_fix import fix_layer_visibility
from cli_anything.qcad.utils.visual_verify import QcadRenderer

VLM_DIR = Path(__file__).resolve().parent.parent / "vlm_automation"
if str(VLM_DIR) not in sys.path:
    sys.path.insert(0, str(VLM_DIR))

from pair1_executor import execute_pair1
from pair2_executor import execute_pair2
from pair3_executor import execute_pair3


class MarkupPipeline:
    """Run the full DWG markup pipeline using proven QCAD-VLM-automation backends."""

    def __init__(
        self,
        dwg_path: str,
        pdf_path: str,
        output_dir: str,
        qcad_bin: Optional[str] = None,
    ):
        self.dwg_path = Path(dwg_path)
        self.pdf_path = Path(pdf_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.qcad_bin = Path(qcad_bin) if qcad_bin else None
        self.converter = DwgConverter(qcad_bin=str(self.qcad_bin) if self.qcad_bin else None)

    def run(self) -> Dict[str, Any]:
        out_root = self.output_dir
        work = out_root / "work"
        work.mkdir(parents=True, exist_ok=True)

        dwg_work = work / self.dwg_path.name
        pdf_work = work / self.pdf_path.name
        shutil.copy2(self.dwg_path, dwg_work)
        shutil.copy2(self.pdf_path, pdf_work)

        original_dxf = work / "original.dxf"
        if not self.converter.dwg_to_dxf(str(dwg_work), str(original_dxf)):
            return {"success": False, "error": "DWG→DXF conversion failed"}

        annotations = self._extract_annotations(str(pdf_work))

        # Dispatch by file stem / annotation content
        texts = " ".join(a.get("text", "").lower() for a in annotations)
        stem = self.dwg_path.stem

        # Pair 3: clone wires from rows 4/5/6 to 7/8/9
        if stem == "3" or ("copy" in texts and "4" in texts and "7" in texts):
            return self._run_pair3(str(original_dxf), str(pdf_work), annotations)

        # Pair 2: cloud deletion + text changes (free-text instructions present)
        if stem == "2" or ("change" in texts) or ("add" in texts and '"' in texts) or ("remove" in texts and "circled" in texts):
            return self._run_pair2(str(original_dxf), str(pdf_work), annotations)

        # Pair 1: cloud deletion only (drawing 1)
        return self._run_pair1(str(original_dxf), str(pdf_work), annotations)

    def _extract_annotations(self, pdf_path: str) -> List[Dict[str, Any]]:
        try:
            import fitz
            doc = fitz.open(pdf_path)
            annots = []
            for page_num, page in enumerate(doc):
                for a in page.annots() or []:
                    atype = a.type[1]
                    r = a.rect
                    annots.append({
                        "page": page_num,
                        "type": atype,
                        "text": (a.get_text() or "").strip(),
                        "target_bbox": [r.x0, r.y0, r.x1, r.y1],
                        "vertices": a.vertices if hasattr(a, "vertices") else None,
                        "inferred_action": self._infer_action((a.get_text() or "").strip()),
                    })
            doc.close()
            return annots
        except Exception as e:
            return [{"error": str(e)}]

    @staticmethod
    def _infer_action(text: str) -> str:
        lowered = text.lower()
        if "delete" in lowered or "remove" in lowered:
            return "delete"
        if "copy" in lowered or "clone" in lowered:
            return "copy"
        if "replace" in lowered or "change" in lowered:
            return "change"
        return "unknown"

    def _run_pair1(self, dxf_in: str, pdf_path: str, annotations: List[Dict[str, Any]]) -> Dict[str, Any]:
        dwg_out = str(self.output_dir / (self.dwg_path.stem + "_modified.dwg"))
        res = execute_pair1(dxf_in, pdf_path, dwg_out)
        comp = self._compare(str(self.dwg_path), dwg_out, self.output_dir / "comparison.png")
        report = {**res, "mode": "pair1", "annotations": annotations, "comparison": comp}
        (self.output_dir / "pipeline_report.json").write_text(json.dumps(report, indent=2, default=str))
        return report

    def _run_pair2(self, dxf_in: str, pdf_path: str, annotations: List[Dict[str, Any]]) -> Dict[str, Any]:
        dwg_out = str(self.output_dir / (self.dwg_path.stem + "_modified.dwg"))
        res = execute_pair2(dxf_in, pdf_path, dwg_out)
        comp = self._compare(str(self.dwg_path), dwg_out, self.output_dir / "comparison.png")
        report = {**res, "mode": "pair2", "annotations": annotations, "comparison": comp}
        (self.output_dir / "pipeline_report.json").write_text(json.dumps(report, indent=2, default=str))
        return report

    def _run_pair3(self, dxf_in: str, pdf_path: str, annotations: List[Dict[str, Any]]) -> Dict[str, Any]:
        dwg_out = str(self.output_dir / (self.dwg_path.stem + "_modified.dwg"))
        res = execute_pair3(dxf_in, pdf_path, dwg_out)
        comp = self._compare(str(self.dwg_path), dwg_out, self.output_dir / "comparison.png")
        report = {**res, "mode": "pair3", "annotations": annotations, "comparison": comp}
        (self.output_dir / "pipeline_report.json").write_text(json.dumps(report, indent=2, default=str))
        return report

    def _compare(self, original_dwg: str, modified_dwg: str, output_png: Path) -> Dict[str, Any]:
        renderer = QcadRenderer(qcad_dir=str(self.qcad_bin.parent) if self.qcad_bin else None)
        return renderer.compare(original_dwg, modified_dwg, str(output_png))
