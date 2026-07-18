"""
Universal modification categories for PDF markup → DWG pipeline.

Each category maps natural-language annotation text to:
  - category name
  - default backend tier
  - action verb(s)
  - required parameters
"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ModificationCategory:
    name: str
    description: str
    default_tier: str
    verbs: List[str]
    required_params: List[str]
    optional_params: List[str]


CATEGORIES = {
    "text_change": ModificationCategory(
        name="text_change",
        description="Replace or update text, labels, or revision-block entries",
        default_tier="T1",
        verbs=["change", "replace", "update", "revise", "rename", "label"],
        required_params=["target_text", "new_value"],
        optional_params=["entity_handle", "layer"],
    ),
    "delete": ModificationCategory(
        name="delete",
        description="Remove clouded entities, text, blocks, or geometry",
        default_tier="T1",
        verbs=["delete", "remove", "erase", "eliminate"],
        required_params=["target_text"],
        optional_params=["cloud_polygon", "layer"],
    ),
    "move": ModificationCategory(
        name="move",
        description="Move or relocate entities to new coordinates",
        default_tier="T2",
        verbs=["move", "relocate", "shift", "position"],
        required_params=["target_text", "destination"],
        optional_params=["reference_point"],
    ),
    "clone": ModificationCategory(
        name="clone",
        description="Duplicate entities (e.g., copy a row to another y-level)",
        default_tier="T2",
        verbs=["copy", "clone", "duplicate", "replicate"],
        required_params=["source", "destination"],
        optional_params=["count", "spacing"],
    ),
    "reorder": ModificationCategory(
        name="reorder",
        description="Reorder rows, lists, or sequences",
        default_tier="T2",
        verbs=["reorder", "rearrange", "resequence", "move to row"],
        required_params=["target_text", "new_position"],
        optional_params=["source_position"],
    ),
    "block_swap": ModificationCategory(
        name="block_swap",
        description="Replace one block reference with another",
        default_tier="T2",
        verbs=["swap", "replace block", "block"],
        required_params=["old_block", "new_block"],
        optional_params=["location_filter"],
    ),
    "add": ModificationCategory(
        name="add",
        description="Add new geometry, text, dimensions, or blocks",
        default_tier="T2",
        verbs=["add", "insert", "create", "draw"],
        required_params=["entity_type", "content"],
        optional_params=["position", "layer", "style"],
    ),
    "dimension": ModificationCategory(
        name="dimension",
        description="Add, update, or move dimensions and leaders",
        default_tier="T2",
        verbs=["dimension", "leader", "callout", "add dimension", "move dimension"],
        required_params=["target_text", "value_or_position"],
        optional_params=["style", "layer"],
    ),
    "leader": ModificationCategory(
        name="leader",
        description="Add or relocate leader lines and callouts",
        default_tier="T2",
        verbs=["leader", "callout", "point to", "arrow"],
        required_params=["target_text", "end_point"],
        optional_params=["start_point", "style"],
    ),
    "resize": ModificationCategory(
        name="resize",
        description="Resize bounding boxes, tables, or enclosures",
        default_tier="T2",
        verbs=["make smaller", "make larger", "resize", "shrink", "enlarge"],
        required_params=["target_text", "new_size"],
        optional_params=["reference_point"],
    ),
    "ambiguous": ModificationCategory(
        name="ambiguous",
        description="Instructions that are vague, interactive, or require visual reasoning",
        default_tier="T4",
        verbs=["fix", "correct", "adjust", "update", "make it"],
        required_params=["raw_text"],
        optional_params=[],
    ),
}


def get_category(name: str) -> Optional[ModificationCategory]:
    return CATEGORIES.get(name)


def classify(annotation_text: str) -> ModificationCategory:
    """Simple rule-based classifier; replaceable with LLM router."""
    text_lower = annotation_text.lower()

    # Revision-block changes look like "Add REV \"4\" ..." but are text edits, not geometry addition
    if "rev \"" in text_lower or "rev block" in text_lower or "revision" in text_lower:
        return CATEGORIES["text_change"]

    # Explicit deletion requests with cloud keywords
    if any(v in text_lower for v in ["delete", "remove", "erase", "eliminate"]):
        return CATEGORIES["delete"]

    # Clone/reorder takes priority over text_change when "copy" or "duplicate"
    # is present — the annotation often says "copy X and change related texts"
    # but the primary action is cloning, not text editing.
    if any(v in text_lower for v in ["copy", "clone", "duplicate", "replicate"]):
        return CATEGORIES["clone"]

    # "Add labels" / "add label" is an add operation, not a text change.
    # Without this, "label" in text_change verbs wins the scoring tie and
    # routes it to ChangeTextValueEngine, which fails (no target text to
    # change — these are new labels being inserted).
    if "add label" in text_lower or "add labels" in text_lower:
        return CATEGORIES["add"]

    # Explicit add/insert/create at the start of the instruction is a
    # strong add signal — don't let "label" or other secondary verbs
    # pull it into text_change.
    if text_lower.startswith(("add ", "insert ", "create ", "draw ")):
        return CATEGORIES["add"]

    best = CATEGORIES["ambiguous"]
    best_score = 0
    for cat in CATEGORIES.values():
        score = sum(1 for v in cat.verbs if v in text_lower)
        if score > best_score:
            best = cat
            best_score = score
    return best
