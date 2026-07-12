"""Drawing profile: automatic introspection of a DXF file.

Discovers drawing-specific structure so engines don't need hardcoded constants:
- Drawing extents (xmin, xmax, ymin, ymax)
- Revision table: which INSERT block holds it, ATTRIB naming convention
- Terminal blocks: block names and ATTRIB tag for terminal numbers
- Protected block names (terminal blocks that should never be deleted)

Usage:
    profile = DrawingProfile.from_dxf('drawing.dxf')
    print(profile.extents)          # (0.0, 25.31, 0.0, 17.69)
    print(profile.rev_block_name)   # 'PLAINS-D-CAN'
    print(profile.rev_tag_pattern)  # 'REV_{n}'
    print(profile.terminal_blocks) # {'Wlltermn': 'TERMNUM', 'Wlterm1': ...}

Engines call DrawingProfile.from_dxf() (cached) and read from it instead of
hardcoded constants. When a structure isn't found, the corresponding field
is None and the engine falls back to its old hardcoded default.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Dict, List, Optional, Tuple

import ezdxf


@dataclass
class RevisionTableInfo:
    """Discovered revision table structure."""
    block_name: str                    # INSERT block name that holds the revision table
    tag_pattern: str                   # 'REV_{n}' or 'REV{n}' — {n} is the row index
    max_rows: int                       # highest N found (e.g., 8 for REV_1..REV_8)
    date_tag_pattern: Optional[str]     # 'REV_DATE_{n}' or 'REV{n}DATE' etc.
    descr_tag_pattern: Optional[str]    # 'REV_DESCR_{n}' or similar
    draw_tag_pattern: Optional[str]    # 'REV_DRAW_{n}' or similar
    chk_tag_pattern: Optional[str]     # 'REV_CHK_{n}' or similar
    appd_tag_pattern: Optional[str]    # 'REV_APPD_{n}' or similar
    # For drawings like Pair 1 where revision slots are flat (REV1, REV2, ...
    # with no subfield tags), date_tag_pattern etc. are None.


@dataclass 
class TerminalBlockInfo:
    """Discovered terminal block structure."""
    block_name: str
    attrib_tag: str                    # ATTRIB tag holding the terminal number
    count: int                         # number of INSERTs of this block
    spacing: Optional[float]           # Y-spacing between terminals (if uniform)


@dataclass
class DrawingProfile:
    """Auto-discovered drawing structure. Engines read from this instead of
    hardcoded constants. Created by DrawingProfile.from_dxf()."""
    
    extents: Tuple[float, float, float, float]  # (xmin, xmax, ymin, ymax)
    rev_table: Optional[RevisionTableInfo] = None
    terminal_blocks: Dict[str, TerminalBlockInfo] = field(default_factory=dict)
    protected_blocks: set = field(default_factory=set)

    # Class-level cache (not a dataclass field — avoids mutable default error)
    _cache: ClassVar[Dict[str, "DrawingProfile"]] = {}
    
    @classmethod
    def from_dxf(cls, dxf_path: str, use_cache: bool = True) -> "DrawingProfile":
        """Analyze a DXF file and return a DrawingProfile.
        
        Args:
            dxf_path: Path to the DXF file.
            use_cache: Return cached profile if same path was analyzed before.
        """
        if use_cache and dxf_path in cls._cache:
            return cls._cache[dxf_path]
        
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        
        extents = cls._compute_extents(msp)
        rev_table = cls._find_revision_table(msp)
        terminal_blocks = cls._find_terminal_blocks(msp)
        
        # Protected blocks = terminal block names + common ground blocks
        protected = set(terminal_blocks.keys())
        protected.update({"GROUND", "GND"})
        
        profile = cls(
            extents=extents,
            rev_table=rev_table,
            terminal_blocks=terminal_blocks,
            protected_blocks=protected,
        )
        
        if use_cache:
            cls._cache[dxf_path] = profile
        return profile
    
    @staticmethod
    def _compute_extents(msp) -> Tuple[float, float, float, float]:
        """Compute drawing extents from all entity geometry."""
        min_x, max_x = float('inf'), float('-inf')
        min_y, max_y = float('inf'), float('-inf')
        
        def update(x, y):
            nonlocal min_x, max_x, min_y, max_y
            min_x = min(min_x, x); max_x = max(max_x, x)
            min_y = min(min_y, y); max_y = max(max_y, y)
        
        for ent in msp:
            try:
                etype = ent.dxftype()
                if etype in ('TEXT', 'MTEXT') and hasattr(ent.dxf, 'insert'):
                    update(ent.dxf.insert.x, ent.dxf.insert.y)
                elif etype == 'LINE':
                    update(ent.dxf.start.x, ent.dxf.start.y)
                    update(ent.dxf.end.x, ent.dxf.end.y)
                elif etype == 'LWPOLYLINE':
                    for px, py, *_ in ent.get_points():
                        update(px, py)
                elif etype == 'POLYLINE':
                    for v in ent.vertices:
                        update(v.dxf.location.x, v.dxf.location.y)
                elif etype == 'CIRCLE':
                    cx, cy, r = ent.dxf.center.x, ent.dxf.center.y, ent.dxf.radius
                    update(cx - r, cy); update(cx + r, cy)
                    update(cx, cy - r); update(cx, cy + r)
                elif etype == 'ARC':
                    cx, cy, r = ent.dxf.center.x, ent.dxf.center.y, ent.dxf.radius
                    sa, ea = ent.dxf.start_angle, ent.dxf.end_angle
                    for i in range(21):
                        a = math.radians(sa + (ea - sa) * i / 20)
                        update(cx + r * math.cos(a), cy + r * math.sin(a))
                elif etype == 'INSERT':
                    update(ent.dxf.insert.x, ent.dxf.insert.y)
                    # Include ATTRIB positions — they represent the actual
                    # rendered text positions in the block's local space.
                    # This catches title block content that extends beyond
                    # the entity bounding box (e.g., revision table at X=33
                    # when LINE entities only reach X=25).
                    for attrib in getattr(ent, 'attribs', []):
                        if hasattr(attrib.dxf, 'insert') and attrib.dxf.insert:
                            update(attrib.dxf.insert.x, attrib.dxf.insert.y)
                elif etype in ('SPLINE', 'ELLIPSE'):
                    try:
                        for pt in ent.control_points:
                            update(pt[0], pt[1])
                    except:
                        pass
                elif etype == 'HATCH':
                    try:
                        for path in ent.paths:
                            if hasattr(path, 'vertices'):
                                for v in path.vertices:
                                    update(v[0], v[1])
                    except:
                        pass
                elif etype == 'DIMENSION' and hasattr(ent.dxf, 'defpoint'):
                    update(ent.dxf.defpoint.x, ent.dxf.defpoint.y)
            except Exception:
                pass
        
        if min_x < float('inf'):
            return (round(min_x, 4), round(max_x, 4),
                    round(min_y, 4), round(max_y, 4))
        return (0.0, 34.0, 0.0, 22.0)  # fallback
    
    @staticmethod
    def _find_revision_table(msp) -> Optional[RevisionTableInfo]:
        """Find the INSERT block that holds a revision table.
        
        Detection heuristic:
        1. Collect all INSERT entities with ATTRIBs
        2. For each, find ATTRIB tags matching REV pattern (REV_1, REV1, REV_01, etc.)
        3. The INSERT with the most REV-tagged ATTRIBs is the revision table
        4. Detect naming convention from the tag pattern
        """
        best_candidate = None
        best_rev_count = 0
        
        # Patterns to match: REV_1, REV1, REV_01, REV 1 (with optional underscore/space)
        rev_patterns = [
            (re.compile(r'^REV_(\d+)$'), 'REV_{n}'),           # REV_1, REV_2
            (re.compile(r'^REV(\d+)$'), 'REV{n}'),             # REV1, REV2
            (re.compile(r'^REV_(\d{2})$'), 'REV_{n:02d}'),     # REV_01, REV_02
            (re.compile(r'^REVNO$'), None),                    # REVNO (single field)
        ]
        
        # Subfield patterns: date, description, drawn, checked, approved
        subfield_patterns = {
            'date_tag_pattern': [
                (re.compile(r'^REV_DATE_(\d+)$'), 'REV_DATE_{n}'),
                (re.compile(r'^REVDATE(\d+)$'), 'REVDATE{n}'),
                (re.compile(r'^REV_DATE_(\d{2})$'), 'REV_DATE_{n:02d}'),
            ],
            'descr_tag_pattern': [
                (re.compile(r'^REV_DESCR_(\d+)$'), 'REV_DESCR_{n}'),
                (re.compile(r'^REVDESCR(\d+)$'), 'REVDESCR{n}'),
                (re.compile(r'^REV_DESC_(\d+)$'), 'REV_DESC_{n}'),
            ],
            'draw_tag_pattern': [
                (re.compile(r'^REV_DRAW_(\d+)$'), 'REV_DRAW_{n}'),
                (re.compile(r'^REVDRAW(\d+)$'), 'REVDRAW{n}'),
            ],
            'chk_tag_pattern': [
                (re.compile(r'^REV_CHK_(\d+)$'), 'REV_CHK_{n}'),
                (re.compile(r'^REVCHK(\d+)$'), 'REVCHK{n}'),
            ],
            'appd_tag_pattern': [
                (re.compile(r'^REV_APPD_(\d+)$'), 'REV_APPD_{n}'),
                (re.compile(r'^REVAPPD(\d+)$'), 'REVAPPD{n}'),
                (re.compile(r'^REV_APP_(\d+)$'), 'REV_APP_{n}'),
            ],
        }
        
        for ent in msp:
            if ent.dxftype() != 'INSERT':
                continue
            attribs = {a.dxf.tag: a.dxf.text for a in ent.attribs}
            if not attribs:
                continue
            
            tags = list(attribs.keys())
            rev_count = 0
            matched_pattern = None
            max_n = 0
            
            for regex, pattern_str in rev_patterns:
                if pattern_str is None:
                    # Single-field revision (REVNO) — low priority, only use
                    # if no numbered REV tags match. Skip here; handle below.
                    continue
                
                for tag in tags:
                    m = regex.match(tag)
                    if m and m.lastindex:
                        rev_count += 1
                        n = int(m.group(1).lstrip('0') or '0')
                        max_n = max(max_n, n)
                        if matched_pattern is None:
                            matched_pattern = pattern_str
            
            # If no numbered REV tags found, try REVNO as fallback
            if rev_count == 0 and 'REVNO' in tags:
                rev_count = 1
                matched_pattern = 'REVNO'
            
            if rev_count > best_rev_count:
                # Detect subfield patterns
                subfields = {}
                for field_name, patterns in subfield_patterns.items():
                    for regex, pattern_str in patterns:
                        for tag in tags:
                            if regex.match(tag):
                                subfields[field_name] = pattern_str
                                break
                        if field_name in subfields:
                            break
                
                best_candidate = RevisionTableInfo(
                    block_name=ent.dxf.name,
                    tag_pattern=matched_pattern or 'REV_{n}',
                    max_rows=max_n if max_n > 0 else 8,
                    date_tag_pattern=subfields.get('date_tag_pattern'),
                    descr_tag_pattern=subfields.get('descr_tag_pattern'),
                    draw_tag_pattern=subfields.get('draw_tag_pattern'),
                    chk_tag_pattern=subfields.get('chk_tag_pattern'),
                    appd_tag_pattern=subfields.get('appd_tag_pattern'),
                )
                best_rev_count = rev_count
        
        return best_candidate
    
    @staticmethod
    def _find_terminal_blocks(msp) -> Dict[str, TerminalBlockInfo]:
        """Find terminal block INSERTs by detecting blocks with small sequential
        integer ATTRIB values at regular Y-spacing.
        
        Heuristic:
        1. Group INSERT entities by block name
        2. For each block with >3 INSERTs, check if any ATTRIB has small integer
           values (1-100) across all instances
        3. If the values form a sequential sequence (1,2,3... or similar), it's
           a terminal block
        4. Detect Y-spacing from the INSERT positions
        """
        # Group INSERTs by block name
        block_inserts: Dict[str, List] = {}
        for ent in msp:
            if ent.dxftype() != 'INSERT':
                continue
            name = ent.dxf.name
            if name not in block_inserts:
                block_inserts[name] = []
            block_inserts[name].append(ent)
        
        terminal_blocks = {}
        
        for block_name, inserts in block_inserts.items():
            if len(inserts) < 3:
                continue  # Need at least 3 to detect a pattern
            
            # Collect ATTRIB values for each INSERT
            attrib_values: Dict[str, List[str]] = {}  # tag -> list of values
            for ins in inserts:
                for attrib in ins.attribs:
                    tag = attrib.dxf.tag
                    val = (attrib.dxf.text or '').strip()
                    if tag not in attrib_values:
                        attrib_values[tag] = []
                    attrib_values[tag].append(val)
            
            # Check if any tag has sequential small integers
            for tag, values in attrib_values.items():
                int_vals = []
                for v in values:
                    try:
                        n = int(v)
                        if 1 <= n <= 200:
                            int_vals.append(n)
                        else:
                            break
                    except ValueError:
                        break
                
                if len(int_vals) >= 3:
                    # Check if values are sequential (1,2,3... or 4,5,6...)
                    sorted_vals = sorted(int_vals)
                    is_sequential = all(
                        sorted_vals[i+1] - sorted_vals[i] == 1
                        for i in range(len(sorted_vals) - 1)
                    )
                    
                    if is_sequential:
                        # Detect Y-spacing
                        y_positions = sorted([ins.dxf.insert.y for ins in inserts])
                        spacings = [
                            round(y_positions[i+1] - y_positions[i], 4)
                            for i in range(len(y_positions) - 1)
                        ]
                        # Most common spacing
                        if spacings:
                            from collections import Counter
                            spacing_counter = Counter(spacings)
                            most_common_spacing = spacing_counter.most_common(1)[0][0]
                            if most_common_spacing > 0:
                                terminal_blocks[block_name] = TerminalBlockInfo(
                                    block_name=block_name,
                                    attrib_tag=tag,
                                    count=len(inserts),
                                    spacing=most_common_spacing,
                                )
                        else:
                            terminal_blocks[block_name] = TerminalBlockInfo(
                                block_name=block_name,
                                attrib_tag=tag,
                                count=len(inserts),
                                spacing=None,
                            )
                        break  # Found terminal number tag for this block
        
        return terminal_blocks
    
    def to_dict(self) -> dict:
        """Serialize to dict for JSON output / logging."""
        return {
            'extents': self.extents,
            'rev_table': {
                'block_name': self.rev_table.block_name,
                'tag_pattern': self.rev_table.tag_pattern,
                'max_rows': self.rev_table.max_rows,
                'date_tag_pattern': self.rev_table.date_tag_pattern,
                'descr_tag_pattern': self.rev_table.descr_tag_pattern,
                'draw_tag_pattern': self.rev_table.draw_tag_pattern,
                'chk_tag_pattern': self.rev_table.chk_tag_pattern,
                'appd_tag_pattern': self.rev_table.appd_tag_pattern,
            } if self.rev_table else None,
            'terminal_blocks': {
                name: {'attrib_tag': info.attrib_tag, 'count': info.count,
                       'spacing': info.spacing}
                for name, info in self.terminal_blocks.items()
            },
            'protected_blocks': list(self.protected_blocks),
        }
    
    def __repr__(self) -> str:
        return (f"DrawingProfile(extents={self.extents}, "
                f"rev_table={self.rev_table.block_name if self.rev_table else None}, "
                f"terminal_blocks={list(self.terminal_blocks.keys())}, "
                f"protected={self.protected_blocks})")