#!/usr/bin/env python3
"""
VLM Phase 2: Target Disambiguator

Resolves ambiguous targets by combining DXF metadata with vision.

Key problem from R4: Block names are metadata invisible to vision.
If Phase 1 says "replace_block BlockA", we must:
  1. Search DXF metadata for INSERT entities matching "BlockA"
  2. Present candidates (coordinates, layer, attributes) to VLM
  3. VLM confirms via screenshot which instance is the intended target

Also handles:
  - Multiple text entities with same content (which "Blu" to change?)
  - Fuzzy block name matching ("Block A" vs "BlockA" vs "Block_A")
  - Coordinate sanity checks (VLM grounding vs DXF actual)

Input:  ParsedInstruction (from Phase 1) + DXF path + optional screenshot
Output: VerifiedTarget with resolved coordinates, entity handle, confidence
"""

import sys
import json
import math
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict

try:
    import ezdxf
except ImportError:
    ezdxf = None

from vlm_client import VLMClient, VLMResponse
from vlm_instruction_parser import ParsedInstruction


@dataclass
class EntityCandidate:
    """A candidate entity from DXF metadata."""
    handle: str
    entity_type: str
    text: Optional[str] = None
    insert: Optional[Tuple[float, float]] = None
    layer: str = ""
    block_name: Optional[str] = None
    color: Optional[int] = None
    confidence_match: float = 0.0  # fuzzy match score

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VerifiedTarget:
    """Final verified target after metadata + vision disambiguation."""
    action_type: str
    target_handle: Optional[str] = None
    target_text: Optional[str] = None
    target_block: Optional[str] = None
    target_insert: Optional[Tuple[float, float]] = None
    target_layer: Optional[str] = None
    replacement_name: str = ""
    confidence: float = 0.0
    metadata_match_score: float = 0.0
    vision_confirm_score: float = 0.0
    needs_human_review: bool = False
    reasoning: str = ""
    candidates_considered: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TargetDisambiguator:
    """Phase 2: Resolve ambiguous targets using DXF metadata + optional vision."""

    PROMPT_TEMPLATE = """You are a CAD target verification assistant.

The user wants to perform this action on a CAD drawing:
  Action type: {action_type}
  Target name: {target_name}
  Replacement: {replacement_name}

From the DXF metadata, we found these candidate entities:
{candidates_json}

Your job:
1. Identify which candidate is the intended target based on the action and target name.
2. Consider proximity to annotation location if coordinates are provided.
3. Return a JSON object with:
   - "selected_handle": the handle of the chosen candidate (or "" if unsure)
   - "confidence": 0.0–1.0 (how sure you are)
   - "reasoning": explanation
   - "needs_human_review": true if confidence < 0.75 or multiple equally good candidates

Output JSON only."""

    VISION_PROMPT_TEMPLATE = """You are verifying a CAD edit target visually.

The instruction is: "{instruction}"

Candidate locations in the drawing (from metadata):
{candidates_desc}

Look at the provided screenshot and tell us:
1. Which candidate matches the annotation arrow/marker?
2. Are there multiple instances of the same text/block?

Return JSON:
   - "selected_handle": handle of visually confirmed target
   - "confidence": 0.0–1.0
   - "reasoning": why this one
   - "needs_human_review": true if ambiguous

Output JSON only."""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.2):
        self.client = VLMClient(model=model or VLMClient.auto_select("vision"))
        self.client.temperature = temperature

    def disambiguate(
        self,
        parsed: ParsedInstruction,
        dxf_path: Optional[str] = None,
        screenshot: Optional[Any] = None,
        annotation_coords: Optional[Tuple[float, float]] = None,
    ) -> VerifiedTarget:
        """
        Resolve target from parsed instruction.

        Args:
            parsed: Phase 1 output
            dxf_path: Path to DXF for metadata search
            screenshot: PIL Image or path — for visual confirmation
            annotation_coords: (x, y) of the annotation leader/arrow tip in drawing units
        """
        action = parsed.action_type
        target_name = parsed.target_name
        replacement = parsed.replacement_name

        # Step 1: Gather candidates from DXF metadata
        candidates: List[EntityCandidate] = []
        if dxf_path and ezdxf and Path(dxf_path).exists():
            candidates = self._search_dxf(dxf_path, target_name, action)

        # Step 2: If no candidates found, mark for human review
        if not candidates:
            return VerifiedTarget(
                action_type=action,
                replacement_name=replacement,
                confidence=0.3,
                metadata_match_score=0.0,
                vision_confirm_score=0.0,
                needs_human_review=True,
                reasoning=f"No DXF candidates found for target '{target_name}'.",
                candidates_considered=0,
            )

        # Step 3: If exactly one candidate with high fuzzy match → fast path
        if len(candidates) == 1 and candidates[0].confidence_match > 0.90:
            c = candidates[0]
            return VerifiedTarget(
                action_type=action,
                target_handle=c.handle,
                target_text=c.text,
                target_block=c.block_name,
                target_insert=c.insert,
                target_layer=c.layer,
                replacement_name=replacement,
                confidence=c.confidence_match,
                metadata_match_score=c.confidence_match,
                vision_confirm_score=0.0,
                needs_human_review=False,
                reasoning=f"Single strong metadata match (score={c.confidence_match:.2f}).",
                candidates_considered=1,
            )

        # Step 4: VLM disambiguation via text (metadata only)
        metadata_conf, selected_handle, reasoning = self._vlm_metadata_disambiguate(
            action, target_name, replacement, candidates
        )

        # Step 5: If screenshot available, visual confirmation
        vision_conf = 0.0
        if screenshot and len(candidates) > 1:
            vision_conf, selected_handle, v_reasoning = self._vlm_visual_confirm(
                parsed, candidates, screenshot, annotation_coords
            )
            if vision_conf > metadata_conf:
                metadata_conf = vision_conf
                reasoning = v_reasoning

        # Step 6: Find selected candidate details
        selected = next((c for c in candidates if c.handle == selected_handle), None)
        if not selected:
            selected = candidates[0]  # fallback to highest match

        final_conf = max(metadata_conf, vision_conf)
        needs_review = final_conf < 0.75 or len(candidates) > 3

        return VerifiedTarget(
            action_type=action,
            target_handle=selected.handle,
            target_text=selected.text,
            target_block=selected.block_name,
            target_insert=selected.insert,
            target_layer=selected.layer,
            replacement_name=replacement,
            confidence=final_conf,
            metadata_match_score=candidates[0].confidence_match if candidates else 0.0,
            vision_confirm_score=vision_conf,
            needs_human_review=needs_review,
            reasoning=reasoning,
            candidates_considered=len(candidates),
        )

    def _search_dxf(self, dxf_path: str, target_name: str, action_type: str) -> List[EntityCandidate]:
        """Search DXF for candidate entities matching target_name."""
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        candidates = []
        target_lower = target_name.lower().strip()

        for entity in msp:
            etype = entity.dxftype()
            handle = entity.dxf.handle
            layer = entity.dxf.layer

            # TEXT / MTEXT
            if etype in ("TEXT", "MTEXT"):
                text = entity.dxf.text if etype == "TEXT" else entity.text
                if text:
                    score = self._fuzzy_match(text, target_name)
                    if score > 0.50:
                        insert = tuple(entity.dxf.insert) if hasattr(entity.dxf, "insert") else None
                        candidates.append(EntityCandidate(
                            handle=handle,
                            entity_type=etype,
                            text=text,
                            insert=insert,
                            layer=layer,
                            confidence_match=score,
                        ))

            # INSERT (block reference)
            elif etype == "INSERT" and action_type in ("replace_block", "move_entity"):
                block_name = entity.dxf.name
                score = self._fuzzy_match(block_name, target_name)
                if score > 0.50:
                    insert = tuple(entity.dxf.insert)
                    candidates.append(EntityCandidate(
                        handle=handle,
                        entity_type=etype,
                        block_name=block_name,
                        insert=insert,
                        layer=layer,
                        confidence_match=score,
                    ))

            # DIMENSION — target might be dimension text
            elif etype == "DIMENSION":
                dim_text = entity.dxf.text_override or getattr(entity, "text", "")
                if dim_text:
                    score = self._fuzzy_match(dim_text, target_name)
                    if score > 0.50:
                        insert = tuple(entity.dxf.insert) if hasattr(entity.dxf, "insert") else None
                        candidates.append(EntityCandidate(
                            handle=handle,
                            entity_type=etype,
                            text=dim_text,
                            insert=insert,
                            layer=layer,
                            confidence_match=score,
                        ))

        # Sort by confidence descending
        candidates.sort(key=lambda c: c.confidence_match, reverse=True)
        return candidates

    @staticmethod
    def _fuzzy_match(a: str, b: str) -> float:
        """Simple fuzzy match score 0.0–1.0."""
        if not a or not b:
            return 0.0
        a_norm = a.lower().strip().replace(" ", "").replace("_", "")
        b_norm = b.lower().strip().replace(" ", "").replace("_", "")

        if a_norm == b_norm:
            return 1.0
        if a_norm in b_norm or b_norm in a_norm:
            return 0.85
        # Levenshtein-ish ratio
        import difflib
        return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()

    def _vlm_metadata_disambiguate(
        self,
        action_type: str,
        target_name: str,
        replacement_name: str,
        candidates: List[EntityCandidate],
    ) -> Tuple[float, str, str]:
        """Ask VLM to pick best candidate from metadata only."""
        candidates_json = json.dumps([c.to_dict() for c in candidates[:5]], indent=2)
        prompt = self.PROMPT_TEMPLATE.format(
            action_type=action_type,
            target_name=target_name,
            replacement_name=replacement_name,
            candidates_json=candidates_json,
        )

        result = self.client.chat([{"role": "user", "content": prompt}])
        parsed = result.parsed_json or {}

        handle = parsed.get("selected_handle", "")
        confidence = float(parsed.get("confidence", 0.0))
        reasoning = parsed.get("reasoning", "VLM metadata disambiguation.")

        # Validate handle exists
        valid_handles = {c.handle for c in candidates}
        if handle not in valid_handles:
            # fallback to highest-confidence candidate
            handle = candidates[0].handle
            confidence *= 0.8
            reasoning += " (VLM picked invalid handle; defaulted to best metadata match.)"

        return confidence, handle, reasoning

    def _vlm_visual_confirm(
        self,
        parsed: ParsedInstruction,
        candidates: List[EntityCandidate],
        screenshot: Any,
        annotation_coords: Optional[Tuple[float, float]],
    ) -> Tuple[float, str, str]:
        """Ask VLM to confirm target by looking at screenshot."""
        desc_lines = []
        for c in candidates[:5]:
            loc = f"at {c.insert}" if c.insert else ""
            if c.text:
                desc_lines.append(f"  {c.handle}: TEXT '{c.text}' {loc}")
            elif c.block_name:
                desc_lines.append(f"  {c.handle}: BLOCK '{c.block_name}' {loc}")

        prompt = self.VISION_PROMPT_TEMPLATE.format(
            instruction=parsed.target_name,
            candidates_desc="\n".join(desc_lines),
        )

        b64 = self.client.encode_image(screenshot)
        messages = [{
            "role": "user",
            "content": prompt,
            "images": [b64],
        }]

        result = self.client.chat(messages)
        parsed = result.parsed_json or {}

        handle = parsed.get("selected_handle", "")
        confidence = float(parsed.get("confidence", 0.0))
        reasoning = parsed.get("reasoning", "VLM visual confirmation.")

        valid_handles = {c.handle for c in candidates}
        if handle not in valid_handles:
            handle = candidates[0].handle
            confidence *= 0.7
            reasoning += " (VLM picked invalid handle visually; defaulted.)"

        return confidence, handle, reasoning


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Phase 2: Disambiguate a CAD edit target")
    ap.add_argument("--dxf", required=True, help="DXF file path")
    ap.add_argument("--instruction", required=True, help="Parsed instruction JSON or raw text")
    ap.add_argument("--screenshot", default=None, help="Screenshot path for visual confirmation")
    ap.add_argument("--model", default=None, help="Ollama model")
    args = ap.parse_args()

    # Parse instruction
    try:
        inst_dict = json.loads(args.instruction)
        parsed = ParsedInstruction(**inst_dict)
    except json.JSONDecodeError:
        # Treat as raw text — do Phase 1 inline
        from vlm_instruction_parser import InstructionParser
        parsed = InstructionParser(model=args.model).parse(args.instruction)

    disambiguator = TargetDisambiguator(model=args.model)
    result = disambiguator.disambiguate(parsed, dxf_path=args.dxf, screenshot=args.screenshot)
    print(json.dumps(result.to_dict(), indent=2))
