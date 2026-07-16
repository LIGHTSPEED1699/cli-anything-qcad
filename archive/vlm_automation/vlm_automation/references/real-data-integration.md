# Real-Data Integration — PDF Annotations + DXF Drawings

Condensed notes from a live session (2026-05-07) wiring three actual engineering drawing pairs through the Phase B pipeline.

## DWG → DXF Conversion

**Tool:** `LibreDWG` built at `/media/sdddata1/libredwg/`

```bash
/media/sdddata1/libredwg/bin/dwg2dxf -o out.dxf in.dwg
```

- Warnings about `HATCH common handles`, `TABLESTYLE`, `MATERIAL`, `MLEADERSTYLE` are **benign** for text-editing pipelines.
- Verify conversion with ezdxf: `len(doc.modelspace())` and `len(doc.layers)` should be > 0.

## PDF Annotation Extraction (PyMuPDF)

```python
import fitz
doc = fitz.open("markup.pdf")
page = doc[0]
for i, a in enumerate(page.annots()):
    text = a.get_text() or ""
    rect = a.rect  # fitz.Rect(x0, y0, x1, y1)
    print(a.type[1], text, rect)
```

### Degenerate Rects (Critical Pitfall)

Cloud markups, revision triangles, and CAD leaders often have **zero-height or zero-width** rects when stored as `Polygon` / `Line` types without text. Calling `fitz.get_pixmap(clip=...)` on a 0×0 rect crashes with:

```
pymupdf.mupdf.FzErrorArgument: code=4: Invalid bandwriter header dimensions/setup
```

**Guard:**
```python
x0, y0, x1, y1 = annot.rect
if x1 - x0 <= 1 or y1 - y0 <= 1:
    return None  # skip degenerate annotation markers
```

### Annotation Type Filtering

Real PDF markups contain mostly **decorative geometry** (Polygons, Lines) and a few **actionable FreeText** annotations.

| Type | Actionable? | Example |
|------|-------------|---------|
| FreeText | ✅ Yes | `"Change to TB-21"`, `"delete clouded objects"` |
| Polygon | ❌ Usually no | Cloud marker, no text |
| Line | ❌ No | Leader arrow, no text |

For pipeline throughput, skip non-FreeText annotations that have empty text. Only route meaningful text to the VLM.

## Module API Quick-Reference

### Tier Router
```python
from tier_router import TierRouter, RouteResult
router = TierRouter()
result = router.route("Change to TB-21")
# result.tier        -> Tier enum
# result.reasoning   -> str  (NOT .reason)
# result.confidence  -> float
```

### VLM Instruction Parser
```python
from vlm_instruction_parser import InstructionParser
parser = InstructionParser(model="qwen2.5vl:latest")
instruction = parser.parse("Change to TB-21", image="/path/to/crop.png")
```

**Note:** Class is `InstructionParser`, not `VLMInstructionParser`.

### Audit Logger
```python
from audit_logger import AuditLogger
logger = AuditLogger(
    db_path="audit_log.db",
    jsonl_path="audit_log.jsonl"
)
aid = logger.log(
    tier=1,  # int (1=T1, 2=T2, 3=T3, 4=T4)
    annotation="Change to TB-21",
    parsed_instruction={"action_type": "replace_text", ...},
    confidence_report={"composite_score": 0.92, ...},
    verification_status="PASSED",
    before_file="/path/to/original.dxf",
    after_file=None,
)
```

### Review Queue
```python
from review_queue import ReviewQueue
q = ReviewQueue(db_path="review_queue.db")
rid = q.enqueue(
    annotation="delete clouded objects",
    tier=1,          # int, not str
    parsed_json={"action_type": "delete_entities", ...},
    confidence_report={"composite_score": 0.55, ...},
    original_file="/path/to/original.dxf",
    modified_file=None,
    before_png=None,
    after_png=None,
)
```

## Mock VLM Client

For deterministic tests, implement the chat interface the `InstructionParser` expects:

```python
class MockVLMClient:
    def chat(self, messages, stream=False):
        from vlm_client import VLMResponse
        content = self._generate_json(messages[0]["content"])
        return VLMResponse(raw_text=content, parsed_json=json.loads(content))

    def encode_image(self, image):
        return ""  # no-op
```

**Do NOT** use a `.call()` interface — `InstructionParser.parse()` calls `self.client.chat(messages)`.

## Confidence Scoring Outcomes on Real Data

qwen2.5vl (Q4_K_M, 8.3B, ~6GB VRAM) on production annotation text:

| Annotation | Parsed Action | Confidence | Pipeline |
|------------|--------------|------------|----------|
| "add BLK" | unknown | 0.30 | REVIEW QUEUE |
| "Change to TB-21" | replace_text | 0.80 | PASSED |
| "remove circled objects; then, make... box smaller" | complex_multi_step | 0.80 | PASSED |

The parser correctly flags genuinely ambiguous annotations ("add BLK" without context) and sends them to human review.

## File Inventory for Reproduction

Test data location: `/home/hongbin/.hermes/kanban/workspaces/testfiles_2026.05.07/`

| Pair | Drawing | Markup | DXF | Entities | Layers | FreeText |
|------|---------|--------|-----|----------|--------|----------|
| 1 | 1.dwg | 1.pdf | 1.dxf | 218 | 14 | 7 |
| 2 | 2.dwg | 2.pdf | 2.dxf | 85 | 15 | 3 |
| 3 | 3.dwg | 3.pdf | 3.dxf | 227 | 15 | 2 |

Total actionable FreeText: 12 out of 38 annotations.
