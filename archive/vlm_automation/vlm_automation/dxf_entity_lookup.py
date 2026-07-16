#!/usr/bin/env python3
"""
DXF Entity Lookup: Parse DXF and extract text/MTEXT/INSERT/DIMENSION entities.
Builds a fast text index for annotation matching.

Usage:
    python dxf_entity_lookup.py /tmp/example_panel_layout.dxf
    python dxf_entity_lookup.py /tmp/example_panel_layout.dxf --search "Blu"
"""

import sys
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher

try:
    import ezdxf
except ImportError:
    print("ERROR: ezdxf not installed. Run: pip install ezdxf")
    sys.exit(1)


@dataclass
class DxfEntity:
    """Represents a searchable DXF entity."""
    handle: str
    entity_type: str  # TEXT, MTEXT, INSERT, DIMENSION, etc.
    text: str
    insertion_point: Tuple[float, float]  # (x, y) in model space
    layer: str
    text_height: Optional[float] = None
    rotation: Optional[float] = None
    # For INSERT (block references):
    block_name: Optional[str] = None
    # For MTEXT:
    attachment_point: Optional[int] = None
    # For DIMENSION:
    dimension_type: Optional[int] = None
    # For entities inside blocks (expanded):
    source_block: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['insertion_point'] = list(d['insertion_point'])
        return d


class DxfEntityIndex:
    """Index DXF entities for fast text search."""

    def __init__(self, dxf_path: str):
        self.dxf_path = dxf_path
        self.entities: List[DxfEntity] = []
        self._text_index: Dict[str, List[int]] = {}  # lowercase text -> entity indices
        self._handle_map: Dict[str, int] = {}  # handle -> entity index
        self._loaded = False

    def load(self) -> None:
        """Parse the DXF and build the entity index."""
        if self._loaded:
            return

        print(f"Loading DXF: {self.dxf_path}")
        doc = ezdxf.readfile(self.dxf_path)
        msp = doc.modelspace()

        print(f"  Modelspace entities: {len(msp)}")

        # First pass: collect block definitions for INSERT expansion
        block_entities: Dict[str, List[DxfEntity]] = {}
        for block in doc.blocks:
            block_ents = []
            for entity in block:
                ent = self._parse_entity(entity, block_name=block.name)
                if ent:
                    block_ents.append(ent)
            if block_ents:
                block_entities[block.name] = block_ents

        # Second pass: modelspace entities
        for entity in msp:
            ent = self._parse_entity(entity)
            if ent:
                self.entities.append(ent)
                # For INSERT entities, also add the block's text entities
                if ent.entity_type == 'INSERT' and ent.block_name in block_entities:
                    for block_ent in block_entities[ent.block_name]:
                        # Clone with the INSERT's insertion point as base
                        shifted = DxfEntity(
                            handle=f"{ent.handle}#{block_ent.handle}",
                            entity_type=block_ent.entity_type,
                            text=block_ent.text,
                            insertion_point=(
                                ent.insertion_point[0] + block_ent.insertion_point[0],
                                ent.insertion_point[1] + block_ent.insertion_point[1]
                            ),
                            layer=ent.layer,
                            text_height=block_ent.text_height,
                            rotation=block_ent.rotation,
                            block_name=ent.block_name,
                            source_block=ent.block_name,
                        )
                        self.entities.append(shifted)

        self._build_index()
        self._loaded = True
        print(f"  Indexed {len(self.entities)} searchable entities")

    def _parse_entity(self, entity, block_name: Optional[str] = None) -> Optional[DxfEntity]:
        """Parse a single DXF entity into a DxfEntity."""
        etype = entity.dxftype()

        if etype == 'TEXT':
            return DxfEntity(
                handle=entity.dxf.handle,
                entity_type='TEXT',
                text=entity.dxf.text.strip(),
                insertion_point=(entity.dxf.insert[0], entity.dxf.insert[1]),
                layer=entity.dxf.layer,
                text_height=getattr(entity.dxf, 'height', None),
                rotation=getattr(entity.dxf, 'rotation', None),
            )

        elif etype == 'MTEXT':
            # MTEXT text may contain formatting codes like \P (paragraph) and \H (height)
            raw_text = entity.dxf.text
            # Remove common MTEXT formatting codes
            cleaned = re.sub(r'\\[Hh]\d+(?:\.\d+)?[;\s]', '', raw_text)
            cleaned = cleaned.replace('\\P', ' ').replace('\\p', ' ')
            cleaned = re.sub(r'\\[LlOoKkQqWw]\d+;?', '', cleaned)
            cleaned = re.sub(r'\\[Ff]\w+;?', '', cleaned)
            cleaned = re.sub(r'\\[Ss]\d+x\d+;?', '', cleaned)
            cleaned = cleaned.replace('\\~', ' ').replace('\\L', '')
            cleaned = cleaned.strip()

            return DxfEntity(
                handle=entity.dxf.handle,
                entity_type='MTEXT',
                text=cleaned,
                insertion_point=(entity.dxf.insert[0], entity.dxf.insert[1]),
                layer=entity.dxf.layer,
                text_height=getattr(entity.dxf, 'text_height', None),
                attachment_point=getattr(entity.dxf, 'attachment_point', None),
            )

        elif etype == 'INSERT':
            return DxfEntity(
                handle=entity.dxf.handle,
                entity_type='INSERT',
                text=f"[BLOCK: {entity.dxf.name}]",
                insertion_point=(entity.dxf.insert[0], entity.dxf.insert[1]),
                layer=entity.dxf.layer,
                block_name=entity.dxf.name,
            )

        elif etype == 'DIMENSION':
            # Dimensions have text stored in dxf.text
            text = getattr(entity.dxf, 'text', '')
            # If text is '<>', it's the default measurement
            if text == '<>':
                text = '[DIM]'
            # Dimension text override
            return DxfEntity(
                handle=entity.dxf.handle,
                entity_type='DIMENSION',
                text=text.strip() if text else '[DIM]',
                insertion_point=(entity.dxf.text_midpoint[0], entity.dxf.text_midpoint[1]),
                layer=entity.dxf.layer,
                dimension_type=getattr(entity.dxf, 'dimtype', None),
            )

        return None

    def _build_index(self) -> None:
        """Build text search index."""
        self._text_index = {}
        self._handle_map = {}

        for i, ent in enumerate(self.entities):
            self._handle_map[ent.handle] = i

            # Index full text
            text_lower = ent.text.lower()
            words = re.findall(r'\b\w+\b', text_lower)

            # Index words
            for word in words:
                if word not in self._text_index:
                    self._text_index[word] = []
                self._text_index[word].append(i)

            # Also index the full text as a key
            if text_lower not in self._text_index:
                self._text_index[text_lower] = []
            self._text_index[text_lower].append(i)

    def search_exact(self, query: str) -> List[DxfEntity]:
        """Exact case-insensitive text match."""
        query_lower = query.lower().strip()
        indices = self._text_index.get(query_lower, [])
        return [self.entities[i] for i in indices]

    def search_fuzzy(self, query: str, threshold: float = 0.7) -> List[Tuple[DxfEntity, float]]:
        """Fuzzy text search with similarity score."""
        query_lower = query.lower().strip()
        results = []
        seen = set()

        # Try exact first
        for ent in self.search_exact(query_lower):
            if ent.handle not in seen:
                results.append((ent, 1.0))
                seen.add(ent.handle)

        # Fuzzy match against all indexed text
        for text, indices in self._text_index.items():
            if len(text) < 2:
                continue
            ratio = SequenceMatcher(None, query_lower, text).ratio()
            if ratio >= threshold:
                for i in indices:
                    ent = self.entities[i]
                    if ent.handle not in seen:
                        results.append((ent, ratio))
                        seen.add(ent.handle)

        # Sort by similarity score
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def search_by_pattern(self, pattern: str) -> List[DxfEntity]:
        """Regex pattern search."""
        regex = re.compile(pattern, re.IGNORECASE)
        results = []
        seen = set()
        for ent in self.entities:
            if regex.search(ent.text) and ent.handle not in seen:
                results.append(ent)
                seen.add(ent.handle)
        return results

    def get_all_text_entities(self) -> List[DxfEntity]:
        """Return all TEXT and MTEXT entities."""
        return [e for e in self.entities if e.entity_type in ('TEXT', 'MTEXT')]

    def get_all_blocks(self) -> List[DxfEntity]:
        """Return all INSERT (block reference) entities."""
        return [e for e in self.entities if e.entity_type == 'INSERT']

    def export_json(self, path: str) -> None:
        """Export entity index to JSON."""
        data = {
            'dxf_path': self.dxf_path,
            'entity_count': len(self.entities),
            'entities': [e.to_dict() for e in self.entities]
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Exported {len(self.entities)} entities to {path}")

    def get_entity_by_handle(self, handle: str) -> Optional[DxfEntity]:
        """Get entity by handle."""
        idx = self._handle_map.get(handle)
        if idx is not None:
            return self.entities[idx]
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='DXF Entity Lookup')
    parser.add_argument('dxf', help='Path to DXF file')
    parser.add_argument('--search', '-s', help='Search query text')
    parser.add_argument('--fuzzy', '-f', action='store_true', help='Use fuzzy matching')
    parser.add_argument('--pattern', '-p', help='Regex pattern search')
    parser.add_argument('--export', '-o', help='Export all entities to JSON')
    parser.add_argument('--type', '-t', choices=['TEXT', 'MTEXT', 'INSERT', 'DIMENSION', 'ALL'],
                        default='ALL', help='Filter by entity type')
    args = parser.parse_args()

    index = DxfEntityIndex(args.dxf)
    index.load()

    if args.export:
        index.export_json(args.export)
        return

    # Filter by type
    if args.type == 'ALL':
        entities = index.entities
    else:
        entities = [e for e in index.entities if e.entity_type == args.type]

    print(f"\n{'='*60}")
    print(f"DXF Entity Summary ({args.dxf})")
    print(f"{'='*60}")
    print(f"Total entities: {len(entities)}")

    # Count by type
    type_counts = {}
    for e in entities:
        type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1
    print("\nBy type:")
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c}")

    # Show all text entities
    text_ents = [e for e in entities if e.entity_type in ('TEXT', 'MTEXT')]
    print(f"\nText/MTEXT entities ({len(text_ents)}):")
    for e in text_ents[:50]:  # Limit output
        print(f"  [{e.handle}] {e.entity_type}: \"{e.text[:60]}\" @ ({e.insertion_point[0]:.2f}, {e.insertion_point[1]:.2f}) layer={e.layer}")
    if len(text_ents) > 50:
        print(f"  ... and {len(text_ents) - 50} more")

    # Show block inserts
    blocks = [e for e in entities if e.entity_type == 'INSERT']
    print(f"\nINSERT (block reference) entities ({len(blocks)}):")
    for e in blocks[:30]:
        print(f"  [{e.handle}] INSERT: {e.block_name} @ ({e.insertion_point[0]:.2f}, {e.insertion_point[1]:.2f}) layer={e.layer}")
    if len(blocks) > 30:
        print(f"  ... and {len(blocks) - 30} more")

    if args.search:
        print(f"\n{'='*60}")
        print(f"Search: \"{args.search}\"")
        print(f"{'='*60}")

        if args.fuzzy:
            results = index.search_fuzzy(args.search)
            print(f"Fuzzy matches ({len(results)}):")
            for ent, score in results[:20]:
                print(f"  [{ent.handle}] {ent.entity_type}: \"{ent.text[:60]}\" @ ({ent.insertion_point[0]:.2f}, {ent.insertion_point[1]:.2f}) score={score:.2f}")
        else:
            results = index.search_exact(args.search)
            print(f"Exact matches ({len(results)}):")
            for ent in results[:20]:
                print(f"  [{ent.handle}] {ent.entity_type}: \"{ent.text[:60]}\" @ ({ent.insertion_point[0]:.2f}, {ent.insertion_point[1]:.2f})")

    if args.pattern:
        print(f"\n{'='*60}")
        print(f"Pattern: \"{args.pattern}\"")
        print(f"{'='*60}")
        results = index.search_by_pattern(args.pattern)
        print(f"Pattern matches ({len(results)}):")
        for ent in results[:20]:
            print(f"  [{ent.handle}] {ent.entity_type}: \"{ent.text[:60]}\" @ ({ent.insertion_point[0]:.2f}, {ent.insertion_point[1]:.2f})")


if __name__ == '__main__':
    main()
