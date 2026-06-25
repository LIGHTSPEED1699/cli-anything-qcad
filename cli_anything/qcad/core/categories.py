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
    "property_change": ModificationCategory(
        name="property_change",
        description="Change color, layer, line type, or other non-text properties",
        default_tier="T1",
        verbs=["change color", "set color", "change layer", "move to layer"],
        required_params=["property", "new_value"],
        optional_params=["target_text"],
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
    best = CATEGORIES["ambiguous"]
    best_score = 0
    for cat in CATEGORIES.values():
        score = sum(1 for v in cat.verbs if v in text_lower)
        if score > best_score:
            best = cat
            best_score = score
    return best
