"""Visual verification: render and compare DWG/DXF outputs."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VerificationResult:
    status: str
    pixel_change_pct: float = 0.0
    vlm_confidence: Optional[float] = None
    vlm_reasoning: Optional[str] = None
    original_png: Optional[str] = None
    modified_png: Optional[str] = None
    diff_png: Optional[str] = None
    error: Optional[str] = None
    renderer_used: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "pixel_change_pct": self.pixel_change_pct,
            "vlm_confidence": self.vlm_confidence,
            "vlm_reasoning": self.vlm_reasoning,
            "original_png": self.original_png,
            "modified_png": self.modified_png,
            "diff_png": self.diff_png,
            "error": self.error,
            "renderer_used": self.renderer_used,
        }


class VisualVerifier:
    """Render DWG/DXF to PNG and verify edits."""

    def __init__(self, renderer: str = "auto", dpi: int = 150):
        self.renderer = renderer
        self.dpi = dpi

    def render(self, file_path: str, output_png: str) -> bool:
        """Render a DWG/DXF to PNG. Stub: integrate visual_verifier.py."""
        return False

    def compare(
        self,
        original_png: str,
        modified_png: str,
        annotations: List[Dict[str, Any]],
    ) -> VerificationResult:
        """Compare original and modified renders."""
        # TODO: integrate pixel diff + VLM semantic check
        return VerificationResult(
            status="UNKNOWN",
            error="Visual verifier stub: implement pixel diff and VLM check",
        )
