#!/usr/bin/env python3
"""
Annotation Matcher: Extract target text from PDF annotations and find matching DXF entities.

Usage:
    python annotation_matcher.py --dxf /tmp/example_panel_layout.dxf --annotation "change Blu to Wht"
"""

import sys
import re
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent))

from dxf_entity_lookup import DxfEntityIndex, DxfEntity


@dataclass
class MatchResult:
    """Result of matching an annotation to a DXF entity."""
    annotation_text: str
    extracted_target: str
    matched_entity: Optional[DxfEntity]
    match_type: str  # 'exact', 'fuzzy', 'pattern'
    confidence: float
    all_candidates: List[Tuple[DxfEntity, float]]


class AnnotationMatcher:
    """Matches PDF annotation text to DXF entities."""

    # Common patterns to extract target text from annotations
    PATTERNS = [
        # "change X to Y" -> target is X
        r'change\s+(\S+)\s+to\s+\S+',
        # "replace X with Y" -> target is X
        r'replace\s+(\S+)\s+with\s+\S+',
        # "replace X block with Y" -> target is X
        r'replace\s+(\S+(?:\s+\S+)?)\s+(?:block\s+)?with',
        # "move X" -> target is X
        r'move\s+(\S+(?:\s+\S+)?)',
        # "delete X" -> target is X
        r'delete\s+(\S+)',
        # "update X" -> target is X
        r'update\s+(\S+)',
        # "rename X to Y" -> target is X
        r'rename\s+(\S+)\s+to',
        # "swap X and Y" -> targets are X and Y (returns first)
        r'swap\s+(\S+)\s+and\s+\S+',
    ]

    # Common noise words that might appear in annotation targets but shouldn't be matched
    NOISE_WORDS = {'block', 'text', 'label', 'line', 'row', 'this', 'the', 'a', 'an'}

    def __init__(self, dxf_path: str):
        self.dxf_path = dxf_path
        self.index = DxfEntityIndex(dxf_path)
        self.index.load()

    def _clean_target(self, target: str) -> Optional[str]:
        """Clean extracted target by removing noise words."""
        words = target.split()
        cleaned = []
        for word in words:
            word_clean = word.strip('.,;:!?()[]{}}"\'').lower()
            if word_clean not in self.NOISE_WORDS and len(word_clean) >= 2:
                cleaned.append(word.strip('.,;:!?()[]{}}"\''))
        return ' '.join(cleaned) if cleaned else target.strip()

    def _normalize_code(self, target: str) -> str:
        """Normalize product codes like NT111 -> NT-111 or NT 111."""
        # Pattern: 2+ letters followed by 3+ digits -> may be missing hyphen
        match = re.match(r'^([A-Za-z]{2,})(\d{3,})$', target)
        if match:
            prefix = match.group(1)
            number = match.group(2)
            return f"{prefix}-{number}"
        return target

    def extract_target(self, annotation_text: str) -> Optional[str]:
        """
        Extract the target text (the thing to be acted upon) from an annotation.
        Returns the target string or None if no target found.
        """
        text = annotation_text.strip()
        text_lower = text.lower()

        for pattern in self.PATTERNS:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                target = match.group(1).strip()
                # Clean up target
                target = target.strip('"\'')
                target = self._clean_target(target)
                # Normalize product codes
                target = self._normalize_code(target)
                return target

        # Fallback: try to find the first word that looks like a label/code
        # (uppercase alphanumeric, or contains numbers)
        words = re.findall(r'[A-Z][A-Z0-9\-]+|[0-9]+-[A-Z0-9]+|\b[A-Z]{2,}\b', text)
        if words:
            return words[0]

        # Last resort: first significant word
        common_verbs = {'change', 'to', 'from', 'replace', 'with', 'move', 'delete', 'update',
                        'rename', 'swap', 'and', 'the', 'this', 'that', 'is', 'are', 'be',
                        'into', 'as', 'for', 'on', 'in', 'at', 'by', 'of'}
        for word in text.split():
            word_clean = word.strip('.,;:!?()[]{}').lower()
            if len(word_clean) >= 2 and word_clean not in common_verbs:
                return word_clean

        return None

    def _search_dxf(self, target: str) -> Tuple[Optional[DxfEntity], str, float, List[Tuple[DxfEntity, float]]]:
        """Search DXF for target. Returns (entity, match_type, confidence, all_candidates)."""
        # Try exact match first
        exact = self.index.search_exact(target)
        if exact:
            return exact[0], 'exact', 1.0, [(e, 1.0) for e in exact]

        # Try fuzzy match
        fuzzy = self.index.search_fuzzy(target, threshold=0.5)
        if fuzzy:
            return fuzzy[0][0], 'fuzzy', fuzzy[0][1], fuzzy

        # Try pattern/regex
        try:
            pattern_results = self.index.search_by_pattern(re.escape(target))
            if pattern_results:
                return pattern_results[0], 'pattern', 0.7, [(e, 0.7) for e in pattern_results]
        except re.error:
            pass

        return None, 'none', 0.0, []

    def match(self, annotation_text: str, use_fuzzy: bool = True) -> MatchResult:
        """
        Match an annotation to a DXF entity.
        Returns MatchResult with the best match and all candidates.
        """
        target = self.extract_target(annotation_text)
        if not target:
            return MatchResult(
                annotation_text=annotation_text,
                extracted_target=None,
                matched_entity=None,
                match_type='none',
                confidence=0.0,
                all_candidates=[]
            )

        print(f"  Annotation: \"{annotation_text}\"")
        print(f"  Extracted target: \"{target}\"")

        entity, match_type, confidence, all_candidates = self._search_dxf(target)

        if entity:
            return MatchResult(
                annotation_text=annotation_text,
                extracted_target=target,
                matched_entity=entity,
                match_type=match_type,
                confidence=confidence,
                all_candidates=all_candidates
            )

        return MatchResult(
            annotation_text=annotation_text,
            extracted_target=target,
            matched_entity=None,
            match_type='none',
            confidence=0.0,
            all_candidates=[]
        )

    def match_batch(self, annotations: List[str]) -> List[MatchResult]:
        """Match multiple annotations."""
        return [self.match(a) for a in annotations]


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Annotation Matcher')
    parser.add_argument('--dxf', required=True, help='Path to DXF file')
    parser.add_argument('--annotation', '-a', required=True, help='Annotation text')
    parser.add_argument('--json', '-j', action='store_true', help='Output as JSON')
    args = parser.parse_args()

    matcher = AnnotationMatcher(args.dxf)
    result = matcher.match(args.annotation)

    if args.json:
        import json
        print(json.dumps({
            'annotation': result.annotation_text,
            'target': result.extracted_target,
            'match_type': result.match_type,
            'confidence': result.confidence,
            'entity': result.matched_entity.to_dict() if result.matched_entity else None,
            'candidates': [
                {'text': e.text, 'type': e.entity_type, 'coords': list(e.insertion_point), 'score': s}
                for e, s in result.all_candidates[:5]
            ]
        }, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"Match Result")
        print(f"{'='*60}")
        print(f"Annotation: {result.annotation_text}")
        print(f"Target: {result.extracted_target}")
        print(f"Match type: {result.match_type}")
        print(f"Confidence: {result.confidence:.2f}")

        if result.matched_entity:
            e = result.matched_entity
            print(f"\nBest match:")
            print(f"  Handle: {e.handle}")
            print(f"  Type: {e.entity_type}")
            print(f"  Text: \"{e.text}\"")
            print(f"  DXF Coords: ({e.insertion_point[0]:.4f}, {e.insertion_point[1]:.4f})")
            print(f"  Layer: {e.layer}")
        else:
            print("\nNo match found.")

        if result.all_candidates:
            print(f"\nTop candidates ({len(result.all_candidates)}):")
            for e, s in result.all_candidates[:10]:
                print(f"  [{e.handle}] {e.entity_type}: \"{e.text[:40]}\" @ ({e.insertion_point[0]:.2f}, {e.insertion_point[1]:.2f}) score={s:.2f}")


if __name__ == '__main__':
    main()
