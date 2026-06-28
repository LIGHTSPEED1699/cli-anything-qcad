#!/usr/bin/env python3
"""
VLM Phase 3: Post-Action Verifier

After X11 actions (or any edit), takes a screenshot and asks the VLM:
"Did the intended change occur? Are there unintended changes?"

Input:  Post-action screenshot + original instruction + before/after images
Output: VerificationVerdict with confidence, unintended_changes list, recommendation

Threshold: confidence < 0.80 → recommend rollback + human review
"""

import sys
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict

from vlm_client import VLMClient, VLMResponse
from vlm_instruction_parser import ParsedInstruction


@dataclass
class VerificationVerdict:
    status: str  # "PASSED", "WARNING", "FAILED"
    confidence: float
    intended_change_detected: bool
    unintended_changes: List[str]
    recommendation: str
    reasoning: str
    raw_response: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PostActionVerifier:
    """Phase 3: Verify that the edit worked and nothing broke."""

    PROMPT_TEMPLATE = """You are a CAD edit verification assistant.

The original instruction was: "{instruction}"

You are shown TWO images:
- Image 1: BEFORE the edit
- Image 2: AFTER the edit

Your task:
1. Did the intended change occur? (e.g., text changed, color changed, entity moved)
2. Are there any UNINTENDED changes? (e.g., other text distorted, geometry shifted, layout broken)

Return a JSON object with exactly these keys:
- "intended_change_detected": true / false
- "confidence": 0.0–1.0 (how sure you are)
- "unintended_changes": [list of strings describing any problems, or empty list]
- "status": "PASSED" if intended change occurred AND no unintended changes
            "WARNING" if intended change occurred BUT minor unintended changes
            "FAILED" if intended change did NOT occur OR major unintended changes
- "reasoning": brief explanation

Output JSON only."""

    SINGLE_IMAGE_PROMPT = """You are verifying a CAD edit.

Instruction: "{instruction}"

Look at this screenshot of the CAD application AFTER the edit.

Did the intended change occur? Are there any unintended changes or artifacts?

Return JSON:
- "intended_change_detected": true / false
- "confidence": 0.0–1.0
- "unintended_changes": [list of strings]
- "status": "PASSED" / "WARNING" / "FAILED"
- "reasoning": explanation

Output JSON only."""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.2):
        self.client = VLMClient(model=model or VLMClient.auto_select("vision"))
        self.client.temperature = temperature

    def verify(
        self,
        instruction: ParsedInstruction,
        after_image: Any,
        before_image: Optional[Any] = None,
    ) -> VerificationVerdict:
        """
        Verify an edit by comparing before/after screenshots.

        Args:
            instruction: ParsedInstruction from Phase 1
            after_image: PIL Image or path — screenshot after edit
            before_image: Optional before screenshot for comparison
        """
        action_desc = f"{instruction.action_type}: {instruction.target_name} → {instruction.replacement_name}"

        if before_image:
            prompt = self.PROMPT_TEMPLATE.format(instruction=action_desc)
            b64_before = self.client.encode_image(before_image)
            b64_after = self.client.encode_image(after_image)
            messages = [{
                "role": "user",
                "content": f"{prompt}\n\n[Image 1: BEFORE]\n[Image 2: AFTER]",
                "images": [b64_before, b64_after],
            }]
        else:
            prompt = self.SINGLE_IMAGE_PROMPT.format(instruction=action_desc)
            b64_after = self.client.encode_image(after_image)
            messages = [{
                "role": "user",
                "content": prompt,
                "images": [b64_after],
            }]

        result = self.client.chat(messages)
        parsed = result.parsed_json or {}

        if result.error:
            return VerificationVerdict(
                status="FAILED",
                confidence=0.0,
                intended_change_detected=False,
                unintended_changes=[f"VLM error: {result.error}"],
                recommendation="Rollback and send to human review.",
                reasoning="Verification call failed.",
                raw_response=result.raw_text,
            )

        status = parsed.get("status", "FAILED")
        confidence = float(parsed.get("confidence", 0.0))
        intended = parsed.get("intended_change_detected", False)
        unintended = parsed.get("unintended_changes", [])
        if isinstance(unintended, str):
            unintended = [unintended] if unintended else []
        reasoning = parsed.get("reasoning", "")

        # Override status based on confidence
        if confidence < 0.60:
            status = "FAILED"
        elif confidence < 0.80 and status != "FAILED":
            status = "WARNING"

        if status == "PASSED":
            recommendation = "No action needed. Edit successful."
        elif status == "WARNING":
            recommendation = "Log warning. Continue but flag for audit."
        else:
            recommendation = "Rollback to original. Queue for human review."

        return VerificationVerdict(
            status=status,
            confidence=confidence,
            intended_change_detected=intended,
            unintended_changes=unintended,
            recommendation=recommendation,
            reasoning=reasoning,
            raw_response=result.raw_text,
        )

    def verify_text_only(
        self,
        instruction: str,
        before_text: str,
        after_text: str,
    ) -> VerificationVerdict:
        """
        Lightweight verification when only text content changed (no screenshot needed).
        Used for T1/T2 when edit is simple text replacement.
        """
        # Exact match check
        if instruction.lower().startswith("change ") and " to " in instruction.lower():
            parts = instruction.lower().split(" to ")
            expected_old = parts[0].replace("change ", "").strip()
            expected_new = parts[1].strip() if len(parts) > 1 else ""

            old_found = expected_old in before_text
            new_found = expected_new in after_text
            old_gone = expected_old not in after_text

            if old_found and new_found and old_gone:
                return VerificationVerdict(
                    status="PASSED",
                    confidence=0.95,
                    intended_change_detected=True,
                    unintended_changes=[],
                    recommendation="No action needed.",
                    reasoning=f"Text '{expected_old}' replaced by '{expected_new}' confirmed.",
                    raw_response="",
                )

        # Fallback: VLM text-only compare
        prompt = f"""Compare these two text snippets from a CAD drawing.

Instruction: "{instruction}"

BEFORE:
{before_text}

AFTER:
{after_text}

Did the intended change occur? Any unintended changes?

Return JSON:
- "intended_change_detected": true / false
- "confidence": 0.0–1.0
- "unintended_changes": [list]
- "status": "PASSED" / "WARNING" / "FAILED"
- "reasoning": explanation

Output JSON only."""

        result = self.client.chat([{"role": "user", "content": prompt}])
        parsed = result.parsed_json or {}

        status = parsed.get("status", "FAILED")
        confidence = float(parsed.get("confidence", 0.0))
        intended = parsed.get("intended_change_detected", False)
        unintended = parsed.get("unintended_changes", [])
        if isinstance(unintended, str):
            unintended = [unintended] if unintended else []
        reasoning = parsed.get("reasoning", "")

        if confidence < 0.60:
            status = "FAILED"
        elif confidence < 0.80 and status != "FAILED":
            status = "WARNING"

        return VerificationVerdict(
            status=status,
            confidence=confidence,
            intended_change_detected=intended,
            unintended_changes=unintended,
            recommendation="Rollback + human review" if status == "FAILED" else "Log warning" if status == "WARNING" else "No action",
            reasoning=reasoning,
            raw_response=result.raw_text,
        )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Phase 3: Verify a CAD edit")
    ap.add_argument("--instruction", required=True, help="Instruction text or JSON")
    ap.add_argument("--after", required=True, help="After-image path")
    ap.add_argument("--before", default=None, help="Before-image path")
    ap.add_argument("--model", default=None, help="Ollama model")
    args = ap.parse_args()

    try:
        inst_dict = json.loads(args.instruction)
        parsed = ParsedInstruction(**inst_dict)
    except json.JSONDecodeError:
        from vlm_instruction_parser import InstructionParser
        parsed = InstructionParser(model=args.model).parse(args.instruction)

    verifier = PostActionVerifier(model=args.model)
    result = verifier.verify(parsed, after_image=args.after, before_image=args.before)
    print(json.dumps(result.to_dict(), indent=2))
