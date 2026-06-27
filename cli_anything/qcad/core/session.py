from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pathlib import Path


@dataclass
class JobSession:
    """Tracks one DWG modification job from markup to verified output."""
    job_id: str
    input_dwg: Optional[str] = None
    input_pdf: Optional[str] = None
    working_dxf: Optional[str] = None
    output_dwg: Optional[str] = None
    annotations: List[Dict[str, Any]] = field(default_factory=list)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    verification: Optional[Dict[str, Any]] = None
    artifacts_dir: Optional[str] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    _modified: bool = False

    def has_project(self) -> bool:
        return self.input_dwg is not None

    def set_project(self, dwg_path: str, pdf_path: str, working_dxf: str) -> None:
        self.input_dwg = dwg_path
        self.input_pdf = pdf_path
        self.working_dxf = working_dxf
        self._modified = False

    def add_task(self, task: Dict[str, Any]) -> None:
        self.tasks.append(task)

    def set_verification(self, result: Dict[str, Any]) -> None:
        self.verification = result

    def add_artifact(self, kind: str, path: str, description: str = "") -> None:
        self.artifacts.append({
            "kind": kind,
            "path": str(path),
            "description": description,
        })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "input_dwg": self.input_dwg,
            "input_pdf": self.input_pdf,
            "working_dxf": self.working_dxf,
            "output_dwg": self.output_dwg,
            "annotations": self.annotations,
            "tasks": self.tasks,
            "verification": self.verification,
            "artifacts_dir": self.artifacts_dir,
            "artifacts": self.artifacts,
        }
