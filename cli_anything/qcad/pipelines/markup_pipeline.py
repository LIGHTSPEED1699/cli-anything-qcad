"""
Universal PDF markup → verified DWG pipeline.

Stages:
  1. ingest   - parse PDF annotations
  2. convert  - DWG → DXF (ODA or QCAD)
  3. classify - map annotations to modification categories
  4. route    - choose backend tier per category
  5. execute  - edit DXF/DWG
  6. verify   - render + diff + VLM semantic check
  7. export   - verified DXF → DWG
"""
import json
import uuid
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from cli_anything.qcad.core.session import JobSession
from cli_anything.qcad.core.categories import classify
from cli_anything.qcad.backends.dwg_converter import DwgConverter
from cli_anything.qcad.backends.ezdxf_backend import EzdxfBackend
from cli_anything.qcad.backends.qcad_ecma_backend import QcadEcmaBackend
from cli_anything.qcad.backends.vlm_x11_backend import VlmX11Backend
from cli_anything.qcad.utils.visual_verify import VisualVerifier


class MarkupPipeline:
    def __init__(
        self,
        pdf_parser: Any = None,
        converter: DwgConverter = None,
        verifier: VisualVerifier = None,
    ):
        self.pdf_parser = pdf_parser
        self.converter = converter or DwgConverter()
        self.verifier = verifier or VisualVerifier()
        self.ezdxf = EzdxfBackend()
        self.qcad_ecma = QcadEcmaBackend()
        self.vlm_x11 = VlmX11Backend()

    def run(self, dwg_path: str, pdf_path: str, output_dwg: str = None) -> JobSession:
        job = JobSession(job_id=str(uuid.uuid4())[:8])

        # 1. Ingest
        annotations = self._parse_pdf(pdf_path)
        job.annotations = annotations

        # 2. Convert DWG → DXF
        with tempfile.TemporaryDirectory() as tmpdir:
            working_dxf = str(Path(tmpdir) / "working.dxf")
            self.converter.dwg_to_dxf(dwg_path, working_dxf)
            job.set_project(dwg_path, pdf_path, working_dxf)

            # 3-5. Classify, route, execute
            for idx, annot in enumerate(annotations):
                category = classify(annot.get("text", ""))
                task = self._execute_task(idx, annot, category, working_dxf)
                job.add_task(task)

            # 6. Verify
            original_png = str(Path(tmpdir) / "orig.png")
            modified_png = str(Path(tmpdir) / "mod.png")
            self.converter.dxf_to_dwg(working_dxf, output_dwg or str(Path(tmpdir) / "out.dwg"))
            self._render(dwg_path, original_png)
            self._render(output_dwg or str(Path(tmpdir) / "out.dwg"), modified_png)
            verification = self.verifier.compare(original_png, modified_png, job.annotations)
            job.set_verification(verification.to_dict())

            # 7. Export (already done above)
            job.output_dwg = output_dwg or str(Path(tmpdir) / "out.dwg")

        return job

    def _parse_pdf(self, pdf_path: str) -> List[Dict[str, Any]]:
        if self.pdf_parser is None:
            return []
        return self.pdf_parser.parse(pdf_path)

    def _execute_task(self, idx: int, annot: Dict[str, Any], category, working_dxf: str) -> Dict[str, Any]:
        task = {
            "task_id": idx,
            "text": annot.get("text"),
            "category": category.name,
            "tier": category.default_tier,
        }

        if category.default_tier == "T1":
            result = self.ezdxf.execute(annot, working_dxf)
        elif category.default_tier in ("T2", "T3"):
            result = self.qcad_ecma.execute(annot, working_dxf)
        else:
            result = self.vlm_x11.execute(annot, working_dxf)

        task["result"] = result
        task["success"] = result.get("success", False)
        return task

    def _render(self, file_path: str, output_png: str) -> None:
        self.verifier.render(file_path, output_png)
