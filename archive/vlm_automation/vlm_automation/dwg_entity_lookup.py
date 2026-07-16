#!/usr/bin/env python3
"""
DWG Entity Lookup: Parse DWG directly via QCAD's ECMAScript API.
Produces output compatible with dxf_entity_lookup.py format.

Usage:
    python dwg_entity_lookup.py /tmp/drawing.dwg
    python dwg_entity_lookup.py /tmp/drawing.dwg --export /tmp/dwg_out.json
    python dwg_entity_lookup.py /tmp/drawing.dwg --search "NT-110" --fuzzy

This avoids DXF conversion loss by using QCAD's native DWG reader.
Requires QCAD Professional (or trial) with DWG plugin.
"""

import sys
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher

@dataclass
class DwgEntity:
    """Represents a searchable DWG entity (same as DxfEntity for compatibility)."""
    handle: str
    entity_type: str
    text: str
    insertion_point: Tuple[float, float]
    layer: str
    text_height: Optional[float] = None
    rotation: Optional[float] = None
    block_name: Optional[str] = None
    attachment_point: Optional[int] = None
    dimension_type: Optional[int] = None
    source_block: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['insertion_point'] = list(d['insertion_point'])
        return d


class DwgEntityIndex:
    """Index DWG entities for fast text search (same interface as DxfEntityIndex)."""

    QCAD_EXE = "/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad"
    QCAD_BIN = "/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad-bin"
    QCAD_DIR = "/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64"
    SCRIPT_PATH = "/home/hongbin/.openclaw/workspace/vlm-gui-automation/dwg_entity_export.js"

    def __init__(self, dwg_path: str):
        self.dwg_path = dwg_path
        self.entities: List[DwgEntity] = []
        self._text_index: Dict[str, List[int]] = {}
        self._handle_map: Dict[str, int] = {}
        self._loaded = False

    def load(self) -> None:
        """Run QCAD ECMAScript to export DWG entities to JSON, then index them."""
        if self._loaded:
            return

        # Run QCAD script to extract entities
        json_path = self._run_qcad_export()

        # Parse JSON output
        self._parse_json(json_path)

        # Build search index
        self._build_index()
        self._loaded = True

    def _run_qcad_export(self) -> str:
        """Execute QCAD ECMAScript to export DWG entities to JSON."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode='w') as f:
            json_path = f.name

        if not Path(self.QCAD_EXE).exists():
            raise RuntimeError(f"QCAD not found at {self.QCAD_EXE}")
        if not Path(self.SCRIPT_PATH).exists():
            raise RuntimeError(f"Export script not found at {self.SCRIPT_PATH}")
        if not Path(self.dwg_path).exists():
            raise FileNotFoundError(f"DWG file not found: {self.dwg_path}")

        cmd = [
            self.QCAD_BIN,
            "-platform", "offscreen",
            "-allow-multiple-instances",
            "-exec", self.SCRIPT_PATH,
            self.dwg_path, json_path
        ]

        env = {
            **__import__('os').environ,
            "LD_LIBRARY_PATH": f"{self.QCAD_DIR}:{self.QCAD_DIR}/plugins",
            "QT_QPA_PLATFORM": "offscreen",
            "QT_AUTO_SCREEN_SCALE_FACTOR": "1",
        }

        print(f"Running QCAD export...")
        print(f"  QCAD: {self.QCAD_BIN}")
        print(f"  DWG:  {self.dwg_path}")

        # Run with timeout
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("QCAD export timed out after 120s")

        # QCAD exits with code 0 on success, but trial may print to stderr
        # Check if output JSON was created
        if not Path(json_path).exists():
            print(f"STDOUT:\n{result.stdout}")
            print(f"STDERR:\n{result.stderr}")
            raise RuntimeError("QCAD export failed: no JSON output generated")

        # Verify JSON is valid
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            print(f"  Loaded {data.get('entity_count', 0)} entities from QCAD")
        except json.JSONDecodeError as e:
            raise RuntimeError(f"QCAD output is not valid JSON: {e}")

        return json_path

    def _parse_json(self, json_path: str) -> None:
        """Parse QCAD JSON output into DwgEntity objects."""
        with open(json_path, 'r') as f:
            data = json.load(f)

        for ent in data.get('entities', []):
            self.entities.append(DwgEntity(
                handle=ent.get('handle', ''),
                entity_type=ent.get('entity_type', ''),
                text=ent.get('text', ''),
                insertion_point=tuple(ent.get('insertion_point', [0, 0])),
                layer=ent.get('layer', ''),
                text_height=ent.get('text_height'),
                rotation=ent.get('rotation'),
                block_name=ent.get('block_name'),
                attachment_point=ent.get('attachment_point'),
                dimension_type=ent.get('dimension_type'),
                source_block=ent.get('source_block'),
            ))

        # Clean up temp file
        try:
            Path(json_path).unlink()
        except OSError:
            pass

    def _build_index(self) -> None:
        """Build text search index."""
        self._text_index = {}
        self._handle_map = {}

        for i, ent in enumerate(self.entities):
            self._handle_map[ent.handle] = i
            text_lower = ent.text.lower()
            words = re.findall(r'\b\w+\b', text_lower)

            for word in words:
                if word not in self._text_index:
                    self._text_index[word] = []
                self._text_index[word].append(i)

            if text_lower not in self._text_index:
                self._text_index[text_lower] = []
            self._text_index[text_lower].append(i)

    def search_exact(self, query: str) -> List[DwgEntity]:
        """Exact case-insensitive text match."""
        query_lower = query.lower().strip()
        indices = self._text_index.get(query_lower, [])
        return [self.entities[i] for i in indices]

    def search_fuzzy(self, query: str, threshold: float = 0.7) -> List[Tuple[DwgEntity, float]]:
        """Fuzzy text search with similarity score."""
        query_lower = query.lower().strip()
        results = []
        seen = set()

        for ent in self.search_exact(query_lower):
            if ent.handle not in seen:
                results.append((ent, 1.0))
                seen.add(ent.handle)

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

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def search_by_pattern(self, pattern: str) -> List[DwgEntity]:
        """Regex pattern search."""
        regex = re.compile(pattern, re.IGNORECASE)
        results = []
        seen = set()
        for ent in self.entities:
            if regex.search(ent.text) and ent.handle not in seen:
                results.append(ent)
                seen.add(ent.handle)
        return results

    def get_all_text_entities(self) -> List[DwgEntity]:
        """Return all TEXT and MTEXT entities."""
        return [e for e in self.entities if e.entity_type in ('TEXT', 'MTEXT')]

    def get_all_blocks(self) -> List[DwgEntity]:
        """Return all INSERT entities."""
        return [e for e in self.entities if e.entity_type == 'INSERT']

    def export_json(self, path: str) -> None:
        """Export entity index to JSON."""
        data = {
            'dwg_path': self.dwg_path,
            'entity_count': len(self.entities),
            'entities': [e.to_dict() for e in self.entities]
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Exported {len(self.entities)} entities to {path}")

    def get_entity_by_handle(self, handle: str) -> Optional[DwgEntity]:
        """Get entity by handle."""
        idx = self._handle_map.get(handle)
        if idx is not None:
            return self.entities[idx]
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description='DWG Entity Lookup (via QCAD ECMAScript)')
    parser.add_argument('dwg', help='Path to DWG file')
    parser.add_argument('--search', '-s', help='Search query text')
    parser.add_argument('--fuzzy', '-f', action='store_true', help='Use fuzzy matching')
    parser.add_argument('--pattern', '-p', help='Regex pattern search')
    parser.add_argument('--export', '-o', help='Export all entities to JSON')
    parser.add_argument('--type', '-t', choices=['TEXT', 'MTEXT', 'INSERT', 'DIMENSION', 'ALL'],
                        default='ALL', help='Filter by entity type')
    args = parser.parse_args()

    index = DwgEntityIndex(args.dwg)
    index.load()

    if args.export:
        index.export_json(args.export)
        return

    if args.type == 'ALL':
        entities = index.entities
    else:
        entities = [e for e in index.entities if e.entity_type == args.type]

    print(f"\n{'='*60}")
    print(f"DWG Entity Summary ({args.dwg})")
    print(f"{'='*60}")
    print(f"Total entities: {len(entities)}")

    type_counts = {}
    for e in entities:
        type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1
    print("\nBy type:")
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c}")

    text_ents = [e for e in entities if e.entity_type in ('TEXT', 'MTEXT')]
    print(f"\nText/MTEXT entities ({len(text_ents)}):")
    for e in text_ents[:50]:
        print(f'  [{e.handle}] {e.entity_type}: "{e.text[:60]}" @ ({e.insertion_point[0]:.2f}, {e.insertion_point[1]:.2f}) layer={e.layer}')
    if len(text_ents) > 50:
        print(f"  ... and {len(text_ents) - 50} more")

    blocks = [e for e in entities if e.entity_type == 'INSERT']
    print(f"\nINSERT entities ({len(blocks)}):")
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
                print(f'  [{ent.handle}] {ent.entity_type}: "{ent.text[:60]}" @ ({ent.insertion_point[0]:.2f}, {ent.insertion_point[1]:.2f}) score={score:.2f}')
        else:
            results = index.search_exact(args.search)
            print(f"Exact matches ({len(results)}):")
            for ent in results[:20]:
                print(f'  [{ent.handle}] {ent.entity_type}: "{ent.text[:60]}" @ ({ent.insertion_point[0]:.2f}, {ent.insertion_point[1]:.2f})')

    if args.pattern:
        print(f"\n{'='*60}")
        print(f"Pattern: \"{args.pattern}\"")
        print(f"{'='*60}")
        results = index.search_by_pattern(args.pattern)
        print(f"Pattern matches ({len(results)}):")
        for ent in results[:20]:
            print(f'  [{ent.handle}] {ent.entity_type}: "{ent.text[:60]}" @ ({ent.insertion_point[0]:.2f}, {ent.insertion_point[1]:.2f})')


if __name__ == '__main__':
    main()
