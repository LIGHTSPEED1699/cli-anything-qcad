#!/usr/bin/env python3
"""
Tier Router: Rule-based classifier that routes PDF annotations to execution tiers.

Tiers:
    T1: ezdxf DXF edit (fastest, most reliable, DXF-only)
    T2: QCAD ECMAScript headless (DWG-native fidelity)
    T3: ODA File Converter bridge (DWG→DXF→edit→DWG)
    T4: VLM + X11 automation (last resort, slowest)

Routing logic is rule-based — no VLM needed for classification.
"""

import re
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Dict, Any


class Tier(Enum):
    """Execution tier for a given annotation."""
    EZDXF = "T1"           # ezdxf Python DXF edit
    QCAD_ECMA = "T2"     # QCAD ECMAScript headless
    ODA_BRIDGE = "T3"    # ODA round-trip conversion
    VLM_X11 = "T4"       # VLM + X11 automation


@dataclass
class RouteResult:
    """Result of routing an annotation to a tier."""
    tier: Tier
    action_type: str
    target_name: Optional[str] = None
    replacement_name: Optional[str] = None
    confidence: float = 1.0
    reasoning: str = ""
    requires_verification: bool = True


class TierRouter:
    """Routes PDF annotations to the most reliable execution tier."""

    # Action type keywords that map to specific tiers
    ACTION_PATTERNS = {
        # T1 actions: simple text/entity edits that ezdxf handles well
        "T1": {
            "change_text": [
                r"change\s+([A-Za-z0-9_-]+)\s+to\s+([A-Za-z0-9_-]+)",
                r"replace\s+text\s+([A-Za-z0-9_-]+)\s+with\s+([A-Za-z0-9_-]+)",
                r"text\s+([A-Za-z0-9_-]+)\s+→\s+([A-Za-z0-9_-]+)",
                r"label\s+([A-Za-z0-9_-]+)\s+as\s+([A-Za-z0-9_-]+)",
            ],
            "change_color": [
                r"(?:change\s+)?(?:color|colour)\s+(?:change\s+)?(?:to\s+)?([A-Za-z]+)",
                r"make\s+(?:it\s+)?([A-Za-z]+)",
                r"set\s+(?:color|colour)\s+to\s+([A-Za-z0-9#]+)",
            ],
            "move_layer": [
                r"move\s+to\s+layer\s+([A-Za-z0-9_-]+)",
                r"layer\s+([A-Za-z0-9_-]+)",
            ],
            "delete_entity": [
                r"delete\s+([A-Za-z0-9_-]+)",
                r"remove\s+([A-Za-z0-9_-]+)",
                r"del\s+([A-Za-z0-9_-]+)",
            ],
        },
        # T2 actions: DWG-native edits requiring QCAD ECMAScript
        "T2": {
            "replace_block": [
                r"replace\s+([A-Za-z0-9_-]+)\s+(?:block\s+)?with\s+([A-Za-z0-9_-]+)",
                r"swap\s+([A-Za-z0-9_-]+)\s+for\s+([A-Za-z0-9_-]+)",
                r"block\s+([A-Za-z0-9_-]+)\s+→\s+([A-Za-z0-9_-]+)",
            ],
            "move_entity": [
                r"move\s+([A-Za-z0-9_-]+)\s+to\s+\(?\s*([\d.]+)\s*,\s*([\d.]+)\s*\)?",
                r"relocate\s+([A-Za-z0-9_-]+)\s+to\s+\(?\s*([\d.]+)\s*,\s*([\d.]+)\s*\)?",
            ],
            "add_dimension": [
                r"add\s+dimension",
                r"dimension\s+([A-Za-z0-9_-]+)",
            ],
            "complex_edit": [
                r"modify\s+block\s+([A-Za-z0-9_-]+)",
                r"edit\s+block\s+([A-Za-z0-9_-]+)",
                r"update\s+block\s+([A-Za-z0-9_-]+)",
            ],
        },
        # T3 actions: require DWG round-trip (when input is DWG)
        "T3": {
            "round_trip": [
                r"convert\s+and\s+edit",
                r"batch\s+convert",
            ],
        },
        # T4 actions: ambiguous, interactive, or no API path
        "T4": {
            "interactive": [
                r"rearrange",
                r"reorder",
                r"resize",
                r"rotate",
                r"explode",
                r"trim",
                r"extend",
            ],
            "unclear": [
                r"fix",
                r"correct",
                r"adjust",
                r"update",
            ],
        },
    }

    def __init__(self, input_format: str = "dxf"):
        """
        Args:
            input_format: "dxf" or "dwg" — affects routing for T1 vs T3
        """
        self.input_format = input_format.lower()

    def route(self, annotation_text: str, annotation_type: str = "FreeText") -> RouteResult:
        """
        Route an annotation to the best execution tier.

        Args:
            annotation_text: The text content of the PDF annotation
            annotation_type: PyMuPDF annotation type (FreeText, Highlight, etc.)

        Returns:
            RouteResult with tier, action_type, and extracted parameters
        """
        text = annotation_text.strip()
        text_lower = text.lower()

        # Priority 1: Check T2 (DWG-native) patterns first for block operations
        # These are the most specific and should not be misrouted to T1
        for action_type, patterns in self.ACTION_PATTERNS["T2"].items():
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    return self._build_t2_result(action_type, match, text)

        # Priority 2: Check T4 (ambiguous/interactive) patterns
        for action_type, patterns in self.ACTION_PATTERNS["T4"].items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return RouteResult(
                        tier=Tier.VLM_X11,
                        action_type=action_type,
                        reasoning=f"Annotation '{text}' contains interactive/ambiguous keyword matching '{action_type}'. Requires VLM+X11.",
                        confidence=0.7,
                    )

        # Priority 3: Check T1 (ezdxf) patterns for text/color/layer edits
        for action_type, patterns in self.ACTION_PATTERNS["T1"].items():
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    return self._build_t1_result(action_type, match, text)

        # Priority 4: Default routing based on input format
        if self.input_format == "dxf":
            # For DXF: default to T1 with a generic text search
            return RouteResult(
                tier=Tier.EZDXF,
                action_type="unknown_text_edit",
                reasoning=f"DXF input: defaulting to T1 (ezdxf) for annotation '{text}'. Will attempt entity search by text content.",
                confidence=0.6,
            )
        else:
            # For DWG: T2 is safer (native DWG fidelity)
            return RouteResult(
                tier=Tier.QCAD_ECMA,
                action_type="unknown_dwg_edit",
                reasoning=f"DWG input: defaulting to T2 (QCAD ECMAScript) for annotation '{text}'. Native DWG fidelity required.",
                confidence=0.6,
            )

    def _build_t1_result(self, action_type: str, match: re.Match, text: str) -> RouteResult:
        """Build a RouteResult for T1 (ezdxf) actions."""
        groups = match.groups()

        if action_type == "change_text":
            return RouteResult(
                tier=Tier.EZDXF,
                action_type=action_type,
                target_name=groups[0] if len(groups) > 0 else None,
                replacement_name=groups[1] if len(groups) > 1 else None,
                reasoning=f"Text replacement: '{groups[0]}' → '{groups[1]}'. ezdxf can edit TEXT/MTEXT entities directly.",
                confidence=0.95,
            )
        elif action_type == "change_color":
            return RouteResult(
                tier=Tier.EZDXF,
                action_type=action_type,
                target_name=groups[0] if len(groups) > 0 else None,
                reasoning=f"Color change to '{groups[0]}'. ezdxf can modify entity color attribute.",
                confidence=0.9,
            )
        elif action_type == "move_layer":
            return RouteResult(
                tier=Tier.EZDXF,
                action_type=action_type,
                target_name=groups[0] if len(groups) > 0 else None,
                reasoning=f"Move to layer '{groups[0]}'. ezdxf can change entity layer.",
                confidence=0.9,
            )
        elif action_type == "delete_entity":
            return RouteResult(
                tier=Tier.EZDXF,
                action_type=action_type,
                target_name=groups[0] if len(groups) > 0 else None,
                reasoning=f"Delete entity '{groups[0]}'. ezdxf can remove entities by handle.",
                confidence=0.9,
            )

        return RouteResult(
            tier=Tier.EZDXF,
            action_type=action_type,
            reasoning=f"Matched T1 pattern '{action_type}' for annotation '{text}'.",
            confidence=0.8,
        )

    def _build_t2_result(self, action_type: str, match: re.Match, text: str) -> RouteResult:
        """Build a RouteResult for T2 (QCAD ECMAScript) actions."""
        groups = match.groups()
        
        # Safely get group values (some patterns have 0 capture groups)
        g0 = groups[0] if len(groups) > 0 else None
        g1 = groups[1] if len(groups) > 1 else None
        g2 = groups[2] if len(groups) > 2 else None

        if action_type == "replace_block":
            return RouteResult(
                tier=Tier.QCAD_ECMA,
                action_type=action_type,
                target_name=g0,
                replacement_name=g1,
                reasoning=f"Block replacement: '{g0}' → '{g1}'. Requires DWG-native block table access. QCAD ECMAScript preserves all block attributes.",
                confidence=0.95,
                requires_verification=True,
            )
        elif action_type == "move_entity":
            return RouteResult(
                tier=Tier.QCAD_ECMA,
                action_type=action_type,
                target_name=g0,
                reasoning=f"Move entity '{g0}' to ({g1}, {g2}). QCAD ECMAScript has full coordinate control.",
                confidence=0.9,
            )
        elif action_type == "add_dimension":
            return RouteResult(
                tier=Tier.QCAD_ECMA,
                action_type=action_type,
                target_name=g0,
                reasoning=f"Add dimension{f' for {g0}' if g0 else ''}. Requires QCAD dimension API.",
                confidence=0.85,
            )
        elif action_type == "complex_edit":
            return RouteResult(
                tier=Tier.QCAD_ECMA,
                action_type=action_type,
                target_name=g0,
                reasoning=f"Complex block edit{f' for {g0}' if g0 else ''}. QCAD ECMAScript can modify block definitions.",
                confidence=0.85,
            )

        return RouteResult(
            tier=Tier.QCAD_ECMA,
            action_type=action_type,
            reasoning=f"Matched T2 pattern '{action_type}' for annotation '{text}'.",
            confidence=0.8,
        )

    def batch_route(self, annotations: List[Dict[str, Any]]) -> List[RouteResult]:
        """Route multiple annotations and return results."""
        results = []
        for ann in annotations:
            text = ann.get("text", "")
            ann_type = ann.get("type", "FreeText")
            result = self.route(text, ann_type)
            results.append(result)
        return results

    def summarize_routing(self, results: List[RouteResult]) -> Dict[str, Any]:
        """Summarize routing decisions for reporting."""
        tier_counts = {}
        for r in results:
            tier_counts[r.tier.value] = tier_counts.get(r.tier.value, 0) + 1

        return {
            "total_annotations": len(results),
            "tier_distribution": tier_counts,
            "t1_ezdxf_count": tier_counts.get("T1", 0),
            "t2_qcad_count": tier_counts.get("T2", 0),
            "t3_oda_count": tier_counts.get("T3", 0),
            "t4_vlm_count": tier_counts.get("T4", 0),
            "avg_confidence": sum(r.confidence for r in results) / len(results) if results else 0,
            "requires_verification_count": sum(1 for r in results if r.requires_verification),
        }


def demo():
    """Demonstrate routing with example annotations."""
    router = TierRouter(input_format="dxf")

    test_annotations = [
        "Change Blu to Wht",
        "Replace NT111 with NT-110",
        "Delete old_label",
        "Move component_A to (150.5, 200.3)",
        "Rearrange the layout",
        "Add dimension for line_AB",
        "Color change to Red",
        "Update block footer_v1",
    ]

    print("=" * 60)
    print("Tier Router Demo")
    print("=" * 60)

    results = []
    for text in test_annotations:
        result = router.route(text)
        results.append(result)
        print(f"\nAnnotation: '{text}'")
        print(f"  → Tier: {result.tier.value} ({result.tier.name})")
        print(f"  → Action: {result.action_type}")
        print(f"  → Target: {result.target_name}")
        print(f"  → Replacement: {result.replacement_name}")
        print(f"  → Confidence: {result.confidence:.2f}")
        print(f"  → Reasoning: {result.reasoning}")

    print("\n" + "=" * 60)
    print("Summary:")
    summary = router.summarize_routing(results)
    for key, value in summary.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    demo()
