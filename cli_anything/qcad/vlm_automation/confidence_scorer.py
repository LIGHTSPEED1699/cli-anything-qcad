#!/usr/bin/env python3
"""
Confidence Scorer: Multi-layer scoring + safety gates

Per R4 VAUQ-inspired architecture:
  Layer 1: Annotation parsing confidence       (from VLM Phase 1)
  Layer 2: Metadata-verification match distance  (VLM coords vs DXF actual)
  Layer 3: Coordinate consistency (3× run variance)
  Layer 4: Post-action verification confidence   (from VLM Phase 3)

Each layer contributes a score 0.0–1.0. Final composite is a weighted
product (or weighted sum) with configurable thresholds.

Below threshold on ≥2 layers → human review queue.
"""

import math
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict


@dataclass
class LayerScore:
    name: str
    score: float
    threshold: float
    passed: bool
    details: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConfidenceReport:
    composite_score: float
    composite_method: str  # "weighted_product", "weighted_sum", "min"
    layers: List[LayerScore]
    overall_passed: bool
    human_review_required: bool
    escalation_tier: Optional[int] = None  # T1=1, T2=2, T3=3, T4=4
    recommendation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "composite_score": self.composite_score,
            "composite_method": self.composite_method,
            "layers": [l.to_dict() for l in self.layers],
            "overall_passed": self.overall_passed,
            "human_review_required": self.human_review_required,
            "escalation_tier": self.escalation_tier,
            "recommendation": self.recommendation,
        }


class ConfidenceScorer:
    """Multi-layer confidence scoring with safety gates."""

    # Default thresholds per layer
    DEFAULT_THRESHOLDS = {
        "annotation_parsing": 0.70,
        "metadata_verification": 0.60,
        "coordinate_consistency": 0.70,
        "post_action_verification": 0.80,
    }

    # Weights for composite calculation
    DEFAULT_WEIGHTS = {
        "annotation_parsing": 0.25,
        "metadata_verification": 0.25,
        "coordinate_consistency": 0.20,
        "post_action_verification": 0.30,
    }

    def __init__(
        self,
        thresholds: Optional[Dict[str, float]] = None,
        weights: Optional[Dict[str, float]] = None,
        method: str = "weighted_product",
    ):
        self.thresholds = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.weights = {**self.DEFAULT_WEIGHTS, **(weights or {})}
        self.method = method  # "weighted_product", "weighted_sum", "min"

    def score(
        self,
        annotation_confidence: float,
        metadata_distance_px: Optional[float] = None,
        metadata_threshold_px: float = 100.0,
        coordinate_variance_px: Optional[float] = None,
        coordinate_threshold_px: float = 50.0,
        post_action_confidence: Optional[float] = None,
    ) -> ConfidenceReport:
        """
        Compute multi-layer confidence report.

        Args:
            annotation_confidence: Phase 1 self-reported confidence (0–1)
            metadata_distance_px: Pixel distance between VLM coords and DXF actual coords
            metadata_threshold_px: Max acceptable distance
            coordinate_variance_px: Std dev across 3 runs of VLM coordinate extraction
            coordinate_threshold_px: Max acceptable variance
            post_action_confidence: Phase 3 self-reported confidence (0–1)
        """
        layers: List[LayerScore] = []

        # Layer 1: Annotation parsing
        l1 = LayerScore(
            name="annotation_parsing",
            score=max(0.0, min(1.0, annotation_confidence)),
            threshold=self.thresholds["annotation_parsing"],
            passed=annotation_confidence >= self.thresholds["annotation_parsing"],
            details=f"Phase 1 confidence = {annotation_confidence:.3f}",
        )
        layers.append(l1)

        # Layer 2: Metadata-verification match
        if metadata_distance_px is not None:
            # Score decays linearly from 1.0 at 0px to 0.0 at 2×threshold
            raw_score = max(0.0, 1.0 - (metadata_distance_px / (2 * metadata_threshold_px)))
            l2 = LayerScore(
                name="metadata_verification",
                score=raw_score,
                threshold=self.thresholds["metadata_verification"],
                passed=raw_score >= self.thresholds["metadata_verification"],
                details=f"VLM vs DXF distance = {metadata_distance_px:.1f}px (threshold={metadata_threshold_px}px)",
            )
        else:
            l2 = LayerScore(
                name="metadata_verification",
                score=0.5,
                threshold=self.thresholds["metadata_verification"],
                passed=True,
                details="No DXF metadata available for distance check (skipped).",
            )
        layers.append(l2)

        # Layer 3: Coordinate consistency (3× run variance)
        if coordinate_variance_px is not None:
            raw_score = max(0.0, 1.0 - (coordinate_variance_px / (2 * coordinate_threshold_px)))
            l3 = LayerScore(
                name="coordinate_consistency",
                score=raw_score,
                threshold=self.thresholds["coordinate_consistency"],
                passed=raw_score >= self.thresholds["coordinate_consistency"],
                details=f"3-run stddev = {coordinate_variance_px:.1f}px (threshold={coordinate_threshold_px}px)",
            )
        else:
            l3 = LayerScore(
                name="coordinate_consistency",
                score=0.5,
                threshold=self.thresholds["coordinate_consistency"],
                passed=True,
                details="No multi-run variance data (skipped).",
            )
        layers.append(l3)

        # Layer 4: Post-action verification
        if post_action_confidence is not None:
            l4 = LayerScore(
                name="post_action_verification",
                score=max(0.0, min(1.0, post_action_confidence)),
                threshold=self.thresholds["post_action_verification"],
                passed=post_action_confidence >= self.thresholds["post_action_verification"],
                details=f"Phase 3 confidence = {post_action_confidence:.3f}",
            )
        else:
            l4 = LayerScore(
                name="post_action_verification",
                score=0.5,
                threshold=self.thresholds["post_action_verification"],
                passed=True,
                details="No post-action verification data (skipped).",
            )
        layers.append(l4)

        # Composite calculation
        if self.method == "weighted_product":
            composite = 1.0
            for layer in layers:
                w = self.weights.get(layer.name, 0.25)
                composite *= (layer.score ** w)
        elif self.method == "weighted_sum":
            composite = 0.0
            for layer in layers:
                w = self.weights.get(layer.name, 0.25)
                composite += layer.score * w
        else:  # min
            composite = min(l.score for l in layers)

        # Determine outcome
        failed_count = sum(1 for l in layers if not l.passed)
        overall_passed = failed_count == 0
        human_review = failed_count >= 2

        # Escalation recommendation
        if failed_count >= 2:
            recommendation = "HUMAN_REVIEW"
            escalation = 4
        elif failed_count == 1:
            recommendation = "ESCALATE_TIER"
            # Escalate one tier up (if T1 → T2, etc)
            escalation = None  # caller should bump
        else:
            recommendation = "PROCEED"
            escalation = None

        return ConfidenceReport(
            composite_score=round(composite, 4),
            composite_method=self.method,
            layers=layers,
            overall_passed=overall_passed,
            human_review_required=human_review,
            escalation_tier=escalation,
            recommendation=recommendation,
        )

    @staticmethod
    def quick_check(confidence: float, threshold: float = 0.70) -> bool:
        """Single-layer quick pass/fail."""
        return confidence >= threshold


if __name__ == "__main__":
    import json
    scorer = ConfidenceScorer()
    report = scorer.score(
        annotation_confidence=0.85,
        metadata_distance_px=45.0,
        coordinate_variance_px=12.0,
        post_action_confidence=0.92,
    )
    print(json.dumps(report.to_dict(), indent=2))
