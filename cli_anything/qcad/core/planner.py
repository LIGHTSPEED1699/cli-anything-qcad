"""Hybrid PDF markup planner: rules + VLM -> reusable task list."""
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import re
import os

try:
    import fitz  # PyMuPDF
except ImportError as e:  # pragma: no cover
    raise ImportError("PyMuPDF is required") from e

from cli_anything.qcad.core.categories import ModificationCategory, classify
from cli_anything.qcad.utils.dxf_entity_index import DxfEntityIndex


class TaskType(Enum):
    DELETE_CLOUDED_ENTITIES = "delete_clouded_entities"
    CHANGE_TEXT_VALUE = "change_text_value"
    ADD_TEXT_LABEL = "add_text_label"
    CLONE_TERMINAL_WIRES = "clone_terminal_wires"
    CLOUD_CLONE = "cloud_clone"
    RESIZE_BOUNDING_BOX = "resize_bounding_box"
    MARK_SPARE_WIRES = "mark_spare_wires"
    ADD_DIMENSION = "add_dimension"
    ADD_LEADER = "add_leader"
    MOVE_ENTITY = "move_entity"
    UNKNOWN = "unknown"


@dataclass
class Task:
    task_id: str
    task_type: str
    text: str
    confidence: float
    pdf_region: Optional[Dict[str, Any]] = None
    dxf_region: Optional[Dict[str, Any]] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    constraints: List[str] = field(default_factory=list)
    source_annotation: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# VLM prompt for ambiguous annotations.
_VLM_PLANNER_PROMPT = """You are a CAD drawing modification planner. Given a PDF markup annotation, produce a structured task specification.

Annotation text: {text}
Nearby context (if any): {context}
Available task types:
- delete_clouded_entities: remove entities inside a cloud/region
- change_text_value: replace existing text/label
- add_text_label: insert new text at a location
- clone_terminal_wires: copy terminal row wiring to another row
- resize_bounding_box: shrink a box around a component
- mark_spare_wires: mark wires as spare at both ends
- add_dimension: add linear/angular dimension between two points
- add_leader: add leader line with text callout
- move_entity: move an entity to a new location

Return ONLY a JSON object with no markdown formatting:
{{
  "task_type": "one of the above",
  "target_description": "what object or region to modify",
  "new_value": "for text changes or added label",
  "anchor": {{"pdf_x": float, "pdf_y": float}},
  "constraints": ["list of preservation rules or instructions"],
  "confidence": float 0.0-1.0
}}
If the annotation is too ambiguous, set task_type to "unknown" and confidence below 0.5."""


def _vlm_parse_annotation(text: str, context: str = "",
                            model: str = "gemma4:31b-cloud",
                            base_url: str = "http://192.168.2.15:11434",
                            timeout: int = 60) -> Dict[str, Any]:
    """Ask a local Ollama VLM to parse an ambiguous annotation."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _VLM_PLANNER_PROMPT.format(text=text, context=context)}],
        "stream": False,
        "options": {"num_predict": 1024, "temperature": 0.1},
    }
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
        content = result.get("message", {}).get("content", "")
        # Strip markdown fences
        cleaned = re.sub(r"```json\s*|\s*```", "", content).strip()
        parsed = json.loads(cleaned)
        return parsed
    except Exception as e:
        return {"task_type": "unknown", "confidence": 0.0, "error": str(e)}


def _extract_pdf_text_spans(pdf_path: str) -> Dict[str, List[Tuple[float, float, float, float]]]:
    """Extract text spans from PDF, normalized to page.rect (rotated) space.

    On rotated pages (e.g. 270°), PyMuPDF's get_text("dict") returns span
    bounding boxes in mediabox (unrotated) coordinate space.  We transform
    every span bbox to page.rect (rotated) space using the page's
    rotation_matrix so that the affine calibration sees a consistent
    coordinate system: DXF_x correlates with page.rect X (positive), and
    DXF_y correlates with page.rect Y (negative, i.e. Y flip).  This
    avoids the axis-swap ambiguity of mediabox coordinates and produces
    calibration residuals ~7x smaller than raw mediabox fitting.
    """
    doc = fitz.open(pdf_path)
    spans: Dict[str, List[Tuple[float, float, float, float]]] = {}
    for page in doc:
        rm = page.rotation_matrix
        has_rotation = page.rotation != 0
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    txt = span.get("text", "").strip()
                    if not txt or len(txt) < 3:
                        continue
                    bbox = span.get("bbox")
                    if not bbox:
                        continue
                    if has_rotation:
                        # Transform all 4 corners to page.rect space
                        corners = [
                            (bbox[0], bbox[1]),
                            (bbox[2], bbox[1]),
                            (bbox[0], bbox[3]),
                            (bbox[2], bbox[3]),
                        ]
                        tc = [
                            (p[0] * rm.a + p[1] * rm.c + rm.e,
                             p[0] * rm.b + p[1] * rm.d + rm.f)
                            for p in corners
                        ]
                        xs = [p[0] for p in tc]
                        ys = [p[1] for p in tc]
                        spans.setdefault(txt, []).append(
                            (min(xs), min(ys), max(xs), max(ys))
                        )
                    else:
                        spans.setdefault(txt, []).append(
                            (bbox[0], bbox[1], bbox[2], bbox[3])
                        )
    doc.close()
    return spans


def _calibrate_affine(pdf_spans: Dict[str, List[Tuple[float, float, float, float]]],
                      dxf_index: DxfEntityIndex,
                      min_matches: int = 6,
                      max_median_residual: float = 0.5) -> Optional[Any]:
    """Compute affine transform from PDF display coords to DXF coords using text label correspondences.

    Returns None if:
    - Fewer than *min_matches* unique text matches exist, OR
    - The median fitting residual exceeds *max_median_residual* DXF units,
      indicating the affine transform is unreliable (e.g. all matches are
      in one corner of a large drawing).  Callers should fall back to
      border calibration in this case.
    """
    import numpy as np
    pairs_pdf = []
    pairs_dxf = []
    for txt, dxf_ents in [(e.text, [e]) for e in dxf_index.get_all_text_entities()]:
        pdf_list = pdf_spans.get(txt, [])
        if len(dxf_ents) == 1 and len(pdf_list) == 1:
            px = (pdf_list[0][0] + pdf_list[0][2]) / 2
            py = (pdf_list[0][1] + pdf_list[0][3]) / 2
            pairs_pdf.append((px, py))
            pairs_dxf.append((dxf_ents[0].insertion_point[0], dxf_ents[0].insertion_point[1]))

    if len(pairs_dxf) < min_matches:
        return None

    A = np.array([[px, py, 1.0] for px, py in pairs_pdf])
    B = np.array(pairs_dxf)

    # Iterative outlier rejection
    for _ in range(3):
        M, *_ = np.linalg.lstsq(A, B, rcond=None)
        residuals = np.hypot(
            A[:, 0] * M[0, 0] + A[:, 1] * M[1, 0] + M[2, 0] - B[:, 0],
            A[:, 0] * M[0, 1] + A[:, 1] * M[1, 1] + M[2, 1] - B[:, 1],
        )
        if len(residuals) > 2:
            thresh = np.median(residuals) + 2.0 * np.std(residuals)
        else:
            thresh = 1.0
        mask = residuals <= thresh
        if mask.all():
            break
        A = A[mask]
        B = B[mask]
        if len(B) < min_matches:
            return None

    M, *_ = np.linalg.lstsq(A, B, rcond=None)

    # Quality check: reject affine if (a) median residual is too high, OR
    # (b) calibration points cover too little of the page in either axis.
    # Sparse coverage means the affine is only valid within the calibration
    # range and extrapolates poorly to the rest of the drawing.
    final_residuals = np.hypot(
        A[:, 0] * M[0, 0] + A[:, 1] * M[1, 0] + M[2, 0] - B[:, 0],
        A[:, 0] * M[0, 1] + A[:, 1] * M[1, 1] + M[2, 1] - B[:, 1],
    )
    median_residual = float(np.median(final_residuals))
    if median_residual > max_median_residual:
        return None

    # Spatial coverage check: if the DXF drawing is large and calibration
    # points cover too little of the page, the affine extrapolates unreliably.
    # For small drawings (both dims < 15 DXF units), even narrow coverage
    # can produce a usable affine because extrapolation distances are small.
    # For large drawings, require at least 25% X and 15% Y page coverage.
    # We compare calibration PDF range to page size, AND calibration DXF
    # range to full DXF extents (computed from all entities).
    if len(A) >= 3:
        import ezdxf as _ezdxf
        page_w = 1224.0
        page_h = 792.0
        pdf_x_range = float(A[:, 0].max() - A[:, 0].min())
        pdf_y_range = float(A[:, 1].max() - A[:, 1].min())
        dxf_match_x = float(B[:, 0].max() - B[:, 0].min())
        dxf_match_y = float(B[:, 1].max() - B[:, 1].min())

        # Compute full DXF extents
        try:
            _doc = _ezdxf.readfile(dxf_index.dxf_path)
            _msp = _doc.modelspace()
            _dxmin_x = _dxmin_y = float('inf')
            _dxmax_x = _dxmax_y = float('-inf')
            for _e in _msp:
                try:
                    if _e.dxftype() in ('TEXT', 'MTEXT', 'INSERT'):
                        _x, _y = _e.dxf.insert.x, _e.dxf.insert.y
                    elif _e.dxftype() == 'LINE':
                        _x, _y = _e.dxf.start.x, _e.dxf.start.y
                    elif _e.dxftype() == 'LWPOLYLINE':
                        _pts = list(_e.get_points("xy"))
                        _x, _y = _pts[0] if _pts else (0, 0)
                    elif _e.dxftype() in ('CIRCLE', 'ARC'):
                        _x, _y = _e.dxf.center.x, _e.dxf.center.y
                    else:
                        continue
                    _dxmin_x = min(_dxmin_x, _x)
                    _dxmax_x = max(_dxmax_x, _x)
                    _dxmin_y = min(_dxmin_y, _y)
                    _dxmax_y = max(_dxmax_y, _y)
                except Exception:
                    pass
            full_dxf_w = _dxmax_x - _dxmin_x
            full_dxf_h = _dxmax_y - _dxmin_y
        except Exception:
            full_dxf_w = dxf_match_x
            full_dxf_h = dxf_match_y

        # Reject if: DXF is large (>15 units) AND calibration covers <25% of
        # the DXF in that axis.  This catches title-block-only calibration on
        # large drawings where extrapolation would be unreliable.
        if full_dxf_w > 15.0 and dxf_match_x / full_dxf_w < 0.25:
            return None
        if full_dxf_h > 15.0 and dxf_match_y / full_dxf_h < 0.15:
            return None

    return M


def _map_pdf_point_to_dxf(pt: Tuple[float, float], affine: Optional[Any]) -> Tuple[float, float]:
    if affine is None:
        # Fallback: assume 72 PDF points per DXF unit (inches/scale)
        return (pt[0] / 72.0, pt[1] / 72.0)
    x = affine[0, 0] * pt[0] + affine[1, 0] * pt[1] + affine[2, 0]
    y = affine[0, 1] * pt[0] + affine[1, 1] * pt[1] + affine[2, 1]
    return (float(x), float(y))


def _border_calibration(pdf_path: str, dxf_path: str) -> Any:
    """Compute a border calibration affine matrix mapping PDF page.rect coords to DXF coords.

    This is used as a fallback when text-label affine calibration fails or is
    unreliable.  It maps the PDF content bounding box to the DXF model space
    extents, accounting for the Y-axis flip (PDF Y goes down, DXF Y goes up).
    """
    import numpy as np
    from cli_anything.qcad.utils.cloud_overlay import _pdf_content_bbox, _compute_dxf_extents

    pdf_left, pdf_top, pdf_right, pdf_bottom = _pdf_content_bbox(pdf_path)
    min_x, min_y, max_x, max_y = _compute_dxf_extents(dxf_path)

    pdf_w = pdf_right - pdf_left
    pdf_h = pdf_bottom - pdf_top
    dxf_w = max_x - min_x
    dxf_h = max_y - min_y

    if pdf_w < 1 or pdf_h < 1 or dxf_w < 0.01 or dxf_h < 0.01:
        return None

    # We want a matrix M such that:
    #   dxf_x = M[0,0] * pdf_x + M[1,0] * pdf_y + M[2,0]
    #   dxf_y = M[0,1] * pdf_x + M[1,1] * pdf_y + M[2,1]
    #
    # Border mapping:
    #   dxf_x = (pdf_x - pdf_left) / pdf_w * dxf_w + min_x
    #   dxf_y = (pdf_bottom - pdf_y) / pdf_h * dxf_h + min_y
    #
    # So:
    #   M[0,0] = dxf_w / pdf_w        M[0,1] = 0
    #   M[1,0] = 0                    M[1,1] = -dxf_h / pdf_h
    #   M[2,0] = min_x - pdf_left * dxf_w / pdf_w
    #   M[2,1] = min_y + pdf_bottom * dxf_h / pdf_h

    sx = dxf_w / pdf_w
    sy = -dxf_h / pdf_h
    tx = min_x - pdf_left * sx
    ty = min_y + pdf_bottom * (-sy)

    M = np.array([
        [sx, 0.0],
        [0.0, sy],
        [tx, ty],
    ])
    return M


def _map_pdf_region_to_dxf(region: Dict[str, Any], affine: Optional[Any]) -> Dict[str, Any]:
    """Map a PDF region dict (polygon or bbox) into DXF coordinates."""
    if region is None:
        return None
    kind = region.get("type", "polygon")
    if kind == "polygon":
        verts = region.get("verts", [])
        mapped = [_map_pdf_point_to_dxf(v, affine) for v in verts]
        xs = [p[0] for p in mapped]
        ys = [p[1] for p in mapped]
        return {"type": "polygon", "verts": mapped,
                "bbox": (min(xs), max(xs), min(ys), max(ys))}
    elif kind == "bbox":
        x0, y0, x1, y1 = region.get("coords", (0, 0, 0, 0))
        p0 = _map_pdf_point_to_dxf((x0, y0), affine)
        p1 = _map_pdf_point_to_dxf((x1, y1), affine)
        return {"type": "bbox", "coords": (p0[0], p1[0], p0[1], p1[1])}
    return region


def _extract_change_value(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract (target, new_value) from common text patterns."""
    lowered = text.lower()
    # "Change X to Y", "Change X -> Y"
    m = re.search(r"change\s+(.+?)\s+(?:to|->|→)\s+(.+)", lowered)
    if m:
        return m.group(1).strip().upper(), m.group(2).strip().upper()
    # "Change to Y" (no explicit target — use near_point to find the text to change)
    m = re.search(r"change\s+to\s+(.+)", lowered)
    if m:
        new_val = m.group(1).strip().upper()
        # Don't derive a search prefix from a single-character new_value
        # (e.g. "change to B" would set target="B" which matches everything
        # containing B).  Return None target so the engine falls back to
        # near_point proximity search.
        if len(new_val) <= 2:
            return None, new_val
        # Derive a search prefix (e.g. "TB-" from "TB-21", "F" from "F175")
        prefix_match = re.match(r"([A-Z]+[- ]?)", new_val)
        target = prefix_match.group(1).strip() if prefix_match else None
        return target, new_val
    # "Replace X with Y"
    m = re.search(r"replace\s+(.+?)\s+with\s+(.+)", lowered)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # "Add 'BLK'"
    m = re.search(r"add\s+['\"]?(.+?)['\"]?\s*$", lowered)
    if m:
        return None, m.group(1).strip().upper()
    return None, None


def _infer_task_from_annotation(annot: Dict[str, Any], affine: Optional[Any],
                                  vlm_model: str = "gemma4:31b-cloud",
                                  vlm_base_url: str = "http://192.168.2.15:11434",
                                  vlm_timeout: int = 60) -> List[Task]:
    """Combine rule-based classification and VLM parsing into one or more Tasks."""
    text = annot.get("text", "").strip()
    category = classify(text)
    pdf_region = None
    verts = annot.get("arrow_vertices") or annot.get("cloud_vertices")
    if verts:
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        pdf_region = {"type": "polygon", "verts": verts,
                      "bbox": (min(xs), max(xs), min(ys), max(ys))}
    elif annot.get("target_bbox"):
        b = annot["target_bbox"]
        pdf_region = {"type": "bbox", "coords": tuple(b)}

    dxf_region = _map_pdf_region_to_dxf(pdf_region, affine)

    # Rule-based confidence
    confidence = annot.get("confidence", 0.5)
    task_type = TaskType.UNKNOWN.value
    parameters: Dict[str, Any] = {}
    constraints: List[str] = []

    target, new_value = _extract_change_value(text)

    # Map category to task type
    cat_name = category.name if hasattr(category, 'name') else str(category)
    if cat_name == "delete":
        task_type = TaskType.DELETE_CLOUDED_ENTITIES.value
        constraints = ["preserve terminal blocks", "preserve ground references", "preserve title block"]
    elif cat_name == "text_change":
        task_type = TaskType.CHANGE_TEXT_VALUE.value
        if target and new_value:
            parameters = {"target_text": target, "new_value": new_value}
        elif new_value:
            # No explicit target text. If we have a region (arrow/cloud),
            # keep as CHANGE_TEXT_VALUE so the engine uses near_point to
            # find the nearest text. Only fall back to ADD_TEXT_LABEL if
            # we truly have no location info.
            if dxf_region:
                parameters = {"new_value": new_value}
            else:
                task_type = TaskType.ADD_TEXT_LABEL.value
                parameters = {"text": new_value}
        constraints = ["match text style of nearby labels"]
    elif cat_name == "add":
        task_type = TaskType.ADD_TEXT_LABEL.value
        if new_value:
            parameters = {"text": new_value}
        constraints = ["match text style of nearby labels"]
    elif cat_name in ("clone", "reorder"):
        # If we have a cloud polygon, use cloud-based clone (more accurate).
        # Otherwise fall back to text-based row clone.
        has_cloud = bool(annot.get("cloud_vertices") or annot.get("arrow_vertices"))
        if has_cloud:
            task_type = TaskType.CLOUD_CLONE.value
            parameters["target_description"] = text
            parameters["text"] = text
        else:
            task_type = TaskType.CLONE_TERMINAL_WIRES.value
        constraints = ["do not clone terminal INSERT blocks", "deduplicate geometry"]
    elif cat_name == "resize":
        task_type = TaskType.RESIZE_BOUNDING_BOX.value
    elif cat_name == "dimension":
        task_type = TaskType.ADD_DIMENSION.value
        parameters.setdefault("style", "Standard")
    elif cat_name == "leader":
        task_type = TaskType.ADD_LEADER.value
    elif cat_name == "move":
        task_type = TaskType.MOVE_ENTITY.value

    # If rule confidence is low or parameters missing, call VLM
    vlm_needed = confidence < 0.7 or not parameters or task_type == TaskType.UNKNOWN.value
    vlm_results: List[Dict[str, Any]] = []
    if vlm_needed:
        vlm = _vlm_parse_annotation(text, context=str(pdf_region),
                                    model=vlm_model, base_url=vlm_base_url,
                                    timeout=vlm_timeout)
        if isinstance(vlm, list):
            vlm_results = vlm
        elif isinstance(vlm, dict):
            vlm_results = [vlm]

    base_tasks: List[Task] = []
    if not vlm_results:
        base_tasks.append(Task(
            task_id="",
            task_type=task_type,
            text=text,
            confidence=round(confidence, 2),
            pdf_region=pdf_region,
            dxf_region=dxf_region,
            parameters=parameters,
            constraints=constraints,
            source_annotation=annot,
        ))
    else:
        for vlm in vlm_results:
            tt = vlm.get("task_type", task_type)
            if tt not in [t.value for t in TaskType]:
                tt = task_type if task_type != TaskType.UNKNOWN.value else TaskType.UNKNOWN.value
            params = dict(parameters)
            if vlm.get("new_value") and not params.get("new_value"):
                params["new_value"] = vlm["new_value"]
            if vlm.get("target_description") and not params.get("target_text"):
                params["target_description"] = vlm["target_description"]
            reg = dxf_region
            if vlm.get("anchor") and reg is None:
                anchor = vlm["anchor"]
                dxf_pt = _map_pdf_point_to_dxf((anchor.get("pdf_x", 0), anchor.get("pdf_y", 0)), affine)
                reg = {"type": "point", "coords": dxf_pt}
            cons = vlm.get("constraints", constraints)
            vlm_conf = vlm.get("confidence", 0.5)
            conf = round(min((confidence + vlm_conf) / 2 + 0.1, 0.95), 2)
            base_tasks.append(Task(
                task_id="",
                task_type=tt,
                text=text,
                confidence=conf,
                pdf_region=pdf_region,
                dxf_region=reg,
                parameters=params,
                constraints=cons,
                source_annotation=annot,
            ))
    return base_tasks


def _bbox_overlap(a: Optional[Tuple[float, float, float, float]],
                  b: Optional[Tuple[float, float, float, float]],
                  tolerance: float = 0.2) -> bool:
    if a is None or b is None:
        return False
    return not (a[1] < b[0] - tolerance or a[0] > b[1] + tolerance or
                a[3] < b[2] - tolerance or a[2] > b[3] + tolerance)


def _merge_tasks(tasks: List[Task]) -> List[Task]:
    """Merge duplicate tasks of the same type on the same region.
    Do NOT merge different task types (e.g. delete + mark spare) even if they
    share a region; they are distinct operations.

    Two tasks are merged only if ALL of:
    - Same task_type
    - Overlapping bboxes (tight, tolerance=0.05 to catch float jitter only)
    - Same source polygon (or both have no source polygon)
    This prevents merging tasks from adjacent but distinct cloud regions
    that happen to have marginally overlapping bboxes.
    """
    merged: List[Task] = []
    for t in tasks:
        bbox = t.dxf_region.get("bbox") if t.dxf_region else None
        found = False
        for m in merged:
            if t.task_type != m.task_type:
                continue
            mbbox = m.dxf_region.get("bbox") if m.dxf_region else None
            if _bbox_overlap(bbox, mbbox, tolerance=0.05):
                # Check they come from the same source polygon
                t_source = t.source_annotation or {}
                m_source = m.source_annotation or {}
                t_verts = t_source.get("arrow_vertices") or t_source.get("cloud_vertices")
                m_verts = m_source.get("arrow_vertices") or m_source.get("cloud_vertices")
                if t_verts and m_verts:
                    # Compare vertex lists — only merge if from same polygon
                    if len(t_verts) != len(m_verts):
                        continue
                    same = all(
                        abs(t_verts[i][0] - m_verts[i][0]) < 1.0 and
                        abs(t_verts[i][1] - m_verts[i][1]) < 1.0
                        for i in range(len(t_verts))
                    )
                    if not same:
                        continue
                # Same type + overlapping region + same source: merge
                m.parameters.update(t.parameters)
                for c in t.constraints:
                    if c not in m.constraints:
                        m.constraints.append(c)
                m.confidence = max(m.confidence, t.confidence)
                found = True
                break
        if not found:
            merged.append(t)
    return merged


class MarkupPlanner:
    """Plan DWG modifications from PDF markup annotations."""

    def __init__(self, vlm_model: str = "gemma4:31b-cloud",
                 vlm_base_url: str = "http://192.168.2.15:11434",
                 vlm_timeout: int = 60):
        self.vlm_model = vlm_model or os.environ.get("VISION_MODEL", "gemma4:31b-cloud")
        self.vlm_base_url = vlm_base_url or os.environ.get("OLLAMA_URL", "http://192.168.2.15:11434")
        self.vlm_timeout = vlm_timeout

    def plan(self, pdf_path: str, dxf_path: str) -> List[Task]:
        from cli_anything.qcad.utils.pdf_parser import PdfAnnotationParser
        parser = PdfAnnotationParser()
        annotations = parser.parse(pdf_path)

        # Build DXF text index for calibration
        index = DxfEntityIndex(dxf_path)
        index.load()
        pdf_spans = _extract_pdf_text_spans(pdf_path)
        affine = _calibrate_affine(pdf_spans, index)

        # If affine calibration failed (too few matches or high residual),
        # fall back to border calibration: map PDF content bbox → DXF extents.
        if affine is None:
            affine = _border_calibration(pdf_path, dxf_path)

        tasks: List[Task] = []
        for annot in annotations:
            subtasks = _infer_task_from_annotation(annot, affine,
                                                    vlm_model=self.vlm_model,
                                                    vlm_base_url=self.vlm_base_url,
                                                    vlm_timeout=self.vlm_timeout)
            tasks.extend(subtasks)
        # Merge overlapping subtasks before sorting
        tasks = _merge_tasks(tasks)
        # Sort top-to-bottom, left-to-right by PDF bbox y
        tasks.sort(key=lambda t: (
            -(t.pdf_region.get("bbox", (0, 0, 0, 0))[3] if t.pdf_region and "bbox" in t.pdf_region else 0),
            t.pdf_region.get("bbox", (0, 0, 0, 0))[0] if t.pdf_region and "bbox" in t.pdf_region else 0
        ))
        # Reassign sequential IDs
        for i, task in enumerate(tasks):
            task.task_id = f"t{i+1:03d}"
        return tasks
