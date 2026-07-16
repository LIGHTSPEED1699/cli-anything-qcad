#!/usr/bin/env python3
"""
VLM Phase 1: Instruction Parser

Converts a raw PDF annotation text (and optional screenshot crop) into a
structured JSON action plan.

Input:  Annotation text + optional image crop of the marked-up area
Output: {
    "action_type": "replace_text" | "change_color" | "move_entity" | "delete_entity" | "replace_block" | "rearrange_layout" | "unknown",
    "target_name": "original text / block name / entity description",
    "replacement_name": "new text / new color / new position",
    "target_layer": null | "layer name",
    "confidence": 0.0–1.0,
    "reasoning": "explanation of the parse",
    "needs_human_review": false
}

Threshold: confidence < 0.70 → needs_human_review = true
"""

import sys
import json
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict

from vlm_client import VLMClient, VLMResponse


@dataclass
class ParsedInstruction:
    action_type: str
    target_name: str
    replacement_name: str
    target_layer: Optional[str] = None
    confidence: float = 0.0
    reasoning: str = ""
    needs_human_review: bool = False
    raw_response: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class InstructionParser:
    """Phase 1: Parse annotation text into structured action plan."""

    # Low-confidence action types that always need review
    AMBIGUOUS_ACTIONS = {"rearrange_layout", "unknown", "complex_multi_step"}

    PROMPT_TEMPLATE = """You are a CAD annotation parser. Convert the user's markup instruction into a structured JSON action plan.

The CAD drawing contains entities like TEXT, MTEXT, DIMENSION, BLOCK (INSERT), LINE, CIRCLE, ARC, LWPOLYLINE.
Common annotation types:
- "Change Blu to Wht" → replace_text (target "Blu", replacement "Wht")
- "NT111 → NT-110" → replace_text (target "NT111", replacement "NT-110")
- "Red" (arrow pointing to a line) → change_color (target entity at arrow tip, replacement "Red")
- "Move BlockA to top right" → move_entity (target "BlockA", replacement "top right")
- "Delete old_label" → delete_entity (target "old_label")
- "Rearrange layout" → rearrange_layout (no specific target, complex)

Rules:
1. If the instruction is ambiguous, set action_type to "unknown" and explain why.
2. If multiple distinct edits are mentioned, set action_type to "complex_multi_step".
3. Always include your reasoning.
4. Output ONLY a JSON object with these exact keys: action_type, target_name, replacement_name, target_layer, confidence, reasoning, needs_human_review.

User instruction: {instruction}

Respond with JSON only."""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.2):
        self.client = VLMClient(model=model or VLMClient.auto_select("instruction_parse"))
        self.client.temperature = temperature

    def parse(self, instruction: str, image: Optional[Any] = None) -> ParsedInstruction:
        """
        Parse a raw annotation instruction.

        Args:
            instruction: The annotation text (e.g., "Change Blu to Wht")
            image: Optional PIL Image or path — not used in Phase 1 unless text is ambiguous
        """
        prompt = self.PROMPT_TEMPLATE.format(instruction=instruction)

        messages = [
            {"role": "user", "content": prompt}
        ]

        # If image provided and model supports vision, attach it
        if image and self.client.model in VLMClient.MODEL_REGISTRY:
            if VLMClient.MODEL_REGISTRY.get(self.client.model, {}).get("vision", False):
                b64 = self.client.encode_image(image)
                messages[0]["images"] = [b64]
                messages[0]["content"] = f"{prompt}\n\n[Image of marked-up area attached for context]"

        result: VLMResponse = self.client.chat(messages)

        if result.error:
            return ParsedInstruction(
                action_type="unknown",
                target_name="",
                replacement_name="",
                confidence=0.0,
                reasoning=f"VLM error: {result.error}",
                needs_human_review=True,
                raw_response=result.raw_text,
            )

        parsed = result.parsed_json or {}
        if not parsed:
            # Fallback: try to infer from raw text heuristically
            parsed = self._heuristic_parse(instruction)

        def _parse_confidence(val):
            if val is None:
                return 0.0
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                val_lower = val.lower().strip()
                mapping = {"high": 0.9, "medium": 0.6, "low": 0.3, "very high": 0.95}
                if val_lower in mapping:
                    return mapping[val_lower]
                try:
                    return float(val)
                except ValueError:
                    return 0.0
            return 0.0

        confidence = _parse_confidence(parsed.get("confidence", parsed.get("confidence_score", 0.0)))
        action = parsed.get("action_type", "unknown")

        # Force human review for ambiguous actions or low confidence
        needs_review = (
            confidence < 0.70
            or action in self.AMBIGUOUS_ACTIONS
            or parsed.get("needs_human_review", False)
        )

        return ParsedInstruction(
            action_type=action,
            target_name=parsed.get("target_name", ""),
            replacement_name=parsed.get("replacement_name", ""),
            target_layer=parsed.get("target_layer") or None,
            confidence=confidence,
            reasoning=parsed.get("reasoning", ""),
            needs_human_review=needs_review,
            raw_response=result.raw_text,
        )

    @staticmethod
    def _heuristic_parse(instruction: str) -> Dict[str, Any]:
        """When VLM returns no JSON, attempt rule-based extraction."""
        text = instruction.strip()
        lower = text.lower()

        # Simple replace patterns: "Change X to Y", "X → Y", "X -> Y"
        patterns = [
            (r"change\s+(.+?)\s+to\s+(.+)", "replace_text"),
            (r"replace\s+(.+?)\s+with\s+(.+)", "replace_text"),
            (r"(.+?)\s*[-–→]\s*(.+)", "replace_text"),
        ]

        import re
        for pat, action in patterns:
            m = re.search(pat, lower)
            if m:
                return {
                    "action_type": action,
                    "target_name": m.group(1).strip(),
                    "replacement_name": m.group(2).strip(),
                    "target_layer": None,
                    "confidence": 0.60,
                    "reasoning": f"Heuristic rule matched pattern: {pat}",
                    "needs_human_review": True,
                }

        # Delete
        if lower.startswith("delete "):
            return {
                "action_type": "delete_entity",
                "target_name": text[7:].strip(),
                "replacement_name": "",
                "target_layer": None,
                "confidence": 0.75,
                "reasoning": "Heuristic: starts with 'Delete'",
                "needs_human_review": False,
            }

        # Move
        if "move " in lower and " to " in lower:
            parts = lower.split(" to ")
            target = parts[0].replace("move ", "").strip()
            dest = parts[1].strip() if len(parts) > 1 else ""
            return {
                "action_type": "move_entity",
                "target_name": target,
                "replacement_name": dest,
                "target_layer": None,
                "confidence": 0.65,
                "reasoning": "Heuristic: contains 'move ... to ...'",
                "needs_human_review": True,
            }

        # Rearrange / complex
        if any(w in lower for w in ("rearrange", "reorder", "re-layout", "fix layout")):
            return {
                "action_type": "rearrange_layout",
                "target_name": "",
                "replacement_name": "",
                "target_layer": None,
                "confidence": 0.50,
                "reasoning": "Heuristic: ambiguous layout instruction",
                "needs_human_review": True,
            }

        return {
            "action_type": "unknown",
            "target_name": text,
            "replacement_name": "",
            "target_layer": None,
            "confidence": 0.30,
            "reasoning": "No heuristic matched; needs human review",
            "needs_human_review": True,
        }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Phase 1: Parse a CAD annotation")
    ap.add_argument("instruction", help="Annotation text")
    ap.add_argument("--image", help="Optional image path")
    ap.add_argument("--model", default=None, help="Ollama model")
    args = ap.parse_args()

    parser = InstructionParser(model=args.model)
    result = parser.parse(args.instruction, image=args.image)
    print(json.dumps(result.to_dict(), indent=2))
