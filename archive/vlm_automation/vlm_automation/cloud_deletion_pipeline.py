#!/usr/bin/env python3
"""
Cloud Annotation → DXF Entity Deletion → DWG Pipeline (V12, 2026-05-12)
=======================================================================

End-to-end production pipeline for CAD/DWG cleanup from PDF cloud markups.

AUTOMATES (~90% of work):
  Phase 1 — PDF cloud extraction (swap_xy coordinate mapping)
  Phase 2 — DXF entity indexing (all 11 entity types, HATCH edge testing)
  Phase 3 — 4-tier matching (strict PIP, bbox, cloud-bbox, boundary)
  Phase 4 — Filtering (label boxes, arrows, content sweep)
  Phase 5 — Text-based entity deletion
  Phase 6 — Layer color fix
  Phase 7 — QCAD Pro ODA → DWG export
  Phase 8 — Verification + summary

MANUAL OVERRIDES (~10% edge cases):
  Use --overrides JSON: {"add":["4BB8"], "remove":["4672"], "restore":["4067"]}
  add    = force-include handles in deletion list
  remove = force-exclude handles from deletion list
  restore = exclude AND track as restored (verification checks presence)

Requirements:
  pip: ezdxf, PyMuPDF, matplotlib
  QCAD Pro 3.32+ at default path or via --qcad

Usage:
  python3 cloud_deletion_pipeline.py \\
      --pdf 1.pdf --dxf 1.dxf \\
      --out-dwg 1_FINAL.dwg \\
      [--overrides overrides.json]
"""

import argparse, json, math, os, re, subprocess, sys, tempfile, textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: PDF cloud extraction (swap_xy coordinate mapping)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Cloud:
    label: str       # e.g. "C0 (LEFT-TOP)"
    side: str        # LEFT or RIGHT
    verts: list      # swap_xy-mapped DXF vertices
    bbox: tuple      # (xmin, xmax, ymin, ymax)
    height: float    # bbox height (for thin-cloud detection)


def extract_clouds(pdf_path: Path, scale: float = 72.0) -> list[Cloud]:
    """Extract Polygon/Cloud annotations from PDF, classify by spatial position.

    Uses swap_xy mapping: x_dxf = y_pdf / 72, y_dxf = x_pdf / 72
    Clouds classified as LEFT if center_x < 7, else RIGHT.
    """
    import fitz
    doc = fitz.open(str(pdf_path))
    page = doc[0]

    raw = []
    for annot in page.annots() or []:
        if annot.info.get('subject', '') != 'Cloud':
            continue
        verts = annot.vertices
        dxf = [(v[1]/scale, v[0]/scale) for v in verts]
        xs = [p[0] for p in dxf]; ys = [p[1] for p in dxf]
        cx = sum(xs)/len(xs); cy = sum(ys)/len(ys)
        raw.append({
            'side': 'LEFT' if cx < 7 else 'RIGHT',
            'verts': dxf,
            'bbox': (min(xs), max(xs), min(ys), max(ys)),
            'height': max(ys) - min(ys),
        })
    doc.close()

    raw.sort(key=lambda c: (0 if c['side']=='LEFT' else 1,
                             0 if c['bbox'][2] < 7 else 1))

    clouds = []
    for i, rc in enumerate(raw):
        vert_label = 'TOP' if rc['bbox'][2] > 7 else 'BOTTOM'
        clouds.append(Cloud(
            label=f"C{i} ({rc['side']}-{vert_label})",
            side=rc['side'], verts=rc['verts'],
            bbox=rc['bbox'], height=rc['height'],
        ))
    return clouds


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: DXF entity index (ezdxf-based, handles all entity types)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class EntityInfo:
    handle: str
    etype: str
    points: list     # all test points for polygon containment
    bbox: Optional[tuple]  # (xmin, xmax, ymin, ymax)
    text: str = ''
    color: int = 0
    meta: dict = field(default_factory=dict)


def build_entity_index(dxf_path: Path) -> list[EntityInfo]:
    """Index all entities that could be cloud deletion targets.

    Handles: TEXT, MTEXT, LINE, CIRCLE, ARC, POLYLINE, LWPOLYLINE, HATCH, INSERT, ELLIPSE.
    CRITICAL for HATCH: tests edges (ArcEdge/LineEdge) not just vertices.
    """
    import ezdxf

    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    entities = []

    for e in msp:
        try:
            etype = e.dxftype()
            handle = e.dxf.handle.upper()
            pts = []; bb = None; meta = {}; text = ''; color = 0

            if hasattr(e.dxf, 'color'):
                color = e.dxf.color
            elif hasattr(e.dxf, 'color_code'):
                color = e.dxf.color_code

            if etype in ('TEXT', 'MTEXT'):
                x, y = e.dxf.insert.x, e.dxf.insert.y
                pts = [(x, y)]
                text = getattr(e.dxf, 'text', getattr(e, 'text', '')).strip()

            elif etype == 'LINE':
                x1,y1 = e.dxf.start.x, e.dxf.start.y
                x2,y2 = e.dxf.end.x, e.dxf.end.y
                pts = [(x1,y1), (x2,y2), ((x1+x2)/2, (y1+y2)/2)]
                bb = (min(x1,x2), max(x1,x2), min(y1,y2), max(y1,y2))

            elif etype == 'CIRCLE':
                cx,cy,r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
                pts = [(cx,cy), (cx+r,cy), (cx-r,cy), (cx,cy+r), (cx,cy-r)]
                bb = (cx-r, cx+r, cy-r, cy+r)

            elif etype == 'ARC':
                cx,cy,r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
                pts = [(cx,cy), (cx+r,cy)]
                bb = (cx-r, cx+r, cy-r, cy+r)

            elif etype == 'ELLIPSE':
                cx,cy = e.dxf.center.x, e.dxf.center.y
                pts = [(cx, cy)]

            elif etype == 'POLYLINE':
                verts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if verts:
                    pts = list(verts)
                    pts.append((sum(v[0] for v in verts)/len(verts),
                                sum(v[1] for v in verts)/len(verts)))
                    xs = [p[0] for p in verts]; ys = [p[1] for p in verts]
                    bb = (min(xs), max(xs), min(ys), max(ys))
                    meta['n_vertices'] = len(verts)
                    meta['vertices'] = verts

            elif etype == 'LWPOLYLINE':
                verts = list(e.get_points(format='xy'))
                if verts:
                    pts = list(verts)
                    pts.append((sum(v[0] for v in verts)/len(verts),
                                sum(v[1] for v in verts)/len(verts)))
                    xs = [p[0] for p in verts]; ys = [p[1] for p in verts]
                    bb = (min(xs), max(xs), min(ys), max(ys))
                    meta['n_vertices'] = len(verts)

            elif etype == 'HATCH':
                test_pts = []; n_paths = 0
                for path in e.paths:
                    n_paths += 1
                    if hasattr(path, 'edges'):
                        for edge in path.edges:
                            for attr_name in ('start', 'end', 'center'):
                                if not hasattr(edge, attr_name):
                                    continue
                                val = getattr(edge, attr_name)
                                if hasattr(val, 'x'):
                                    test_pts.append((val.x, val.y))
                                elif isinstance(val, (tuple, list)) and len(val) >= 2:
                                    test_pts.append((float(val[0]), float(val[1])))
                    if hasattr(path, 'vertices') and path.vertices:
                        test_pts.extend(path.vertices)

                if test_pts:
                    pts = test_pts
                    xs = [p[0] for p in test_pts]; ys = [p[1] for p in test_pts]
                    bb = (min(xs), max(xs), min(ys), max(ys))
                    meta['n_paths'] = n_paths

            elif etype == 'INSERT':
                x, y = e.dxf.insert.x, e.dxf.insert.y
                pts = [(x, y)]
                if hasattr(e.dxf, 'name'):
                    meta['name'] = e.dxf.name

            if pts:
                entities.append(EntityInfo(
                    handle=handle, etype=etype, points=pts, bbox=bb,
                    text=text, color=color, meta=meta))

        except Exception:
            continue

    return entities


# ═══════════════════════════════════════════════════════════════════════
# Geometry utilities
# ═══════════════════════════════════════════════════════════════════════

def pip_raycast(x: float, y: float, verts: list) -> bool:
    """Ray-casting point-in-polygon."""
    inside, n = False, len(verts)
    if n < 3: return False
    j = n - 1
    for i in range(n):
        xi,yi = verts[i]; xj,yj = verts[j]
        if ((yi > y) != (yj > y)) and \
           (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def strict_pip(x: float, y: float, verts: list, margin: float) -> bool:
    """Strict inside with inward contraction margin."""
    from matplotlib.path import Path as MplPath
    return MplPath(verts).contains_point((x, y), radius=margin)


def bboxes_overlap(a: tuple, b: tuple) -> bool:
    return a[0] <= b[1] and a[1] >= b[0] and a[2] <= b[3] and a[3] >= b[2]


def point_in_bbox(x: float, y: float, bb: tuple) -> bool:
    return bb[0] <= x <= bb[1] and bb[2] <= y <= bb[3]


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: 4-tier matching engine
# ═══════════════════════════════════════════════════════════════════════

def match_entities(entities: list[EntityInfo], clouds: list[Cloud],
                   strict_margin: float = -0.08) -> tuple[set, set, dict]:
    """Match entities to clouds using 4 tiers:

    T1 — Strict PIP (inward contraction): entity point definitively inside cloud polygon
    T2 — Bbox intersection: entity bbox overlaps cloud bbox (for HATCHes, thin-strip lines)
    T3 — Point-in-cloud-bbox: for thin clouds (<1 unit tall) where polygon is unreliable
    T4 — Expanded PIP: boundary-touching — excluded from deletion, flagged for review

    Returns (deletion_handles, boundary_handles, per_cloud_statistics)
    """
    deletion = set()
    boundary = set()
    stats = defaultdict(lambda: {'t1':0, 't2':0, 't3':0, 't4':0})

    for e in entities:
        if not e.points:
            continue

        for ci, cloud in enumerate(clouds):
            is_thin = cloud.height < 1.0

            # T1: Strict inside-PIP (negative margin = inward contraction)
            for pt in e.points:
                if strict_pip(pt[0], pt[1], cloud.verts, margin=strict_margin):
                    deletion.add(e.handle)
                    stats[cloud.label]['t1'] += 1
                    break
            if e.handle in deletion:
                break

            # T2: Bbox intersection
            if e.bbox and (
                e.etype == 'HATCH' or
                (is_thin and e.etype in ('POLYLINE', 'LINE', 'LWPOLYLINE'))
            ):
                if bboxes_overlap(e.bbox, cloud.bbox):
                    deletion.add(e.handle)
                    stats[cloud.label]['t2'] += 1
                    break

            # T3: Point-in-cloud-bbox (thin clouds)
            if is_thin and e.etype in (
                'TEXT', 'MTEXT', 'LINE', 'POLYLINE', 'LWPOLYLINE', 'CIRCLE'
            ):
                for pt in e.points:
                    if point_in_bbox(pt[0], pt[1], cloud.bbox):
                        deletion.add(e.handle)
                        stats[cloud.label]['t3'] += 1
                        break
            if e.handle in deletion:
                break

            # T4: Expanded PIP (boundary-touching — excluded)
            for pt in e.points:
                if pip_raycast(pt[0], pt[1], cloud.verts):
                    boundary.add(e.handle)
                    stats[cloud.label]['t4'] += 1
                    break

    return deletion, boundary, dict(stats)


# ═══════════════════════════════════════════════════════════════════════
# Phase 4: Filtering (preservations, content sweep)
# ═══════════════════════════════════════════════════════════════════════

def content_sweep(entities: list[EntityInfo], deletion: set,
                  clouds: list[Cloud]) -> set:
    """Catch texts past cloud boundaries whose content mirrors deleted texts.

    Strategy: if a TEXT entity on the SAME drawing side as a cloud has words
    matching content found in deleted texts, AND is within 3 DXF units of the
    cloud bbox boundary, include it. This captures multi-word texts like
    "Hydrogen Peroxide Tank Level Display" that extend past the cloud boundary.
    """
    deleted_words = set()
    for e in entities:
        if e.handle in deletion and e.text:
            for w in e.text.lower().split():
                if len(w) > 2:
                    deleted_words.add(w)

    additions = set()
    for e in entities:
        if e.handle in deletion or e.etype not in ('TEXT', 'MTEXT'):
            continue
        if not e.text:
            continue

        tx, ty = e.points[0]
        side = 'LEFT' if tx < 7 else 'RIGHT'

        for cloud in clouds:
            if cloud.side != side:
                continue
            cb = cloud.bbox
            # Within 3 DXF units of any cloud on same side
            if (cb[0]-3 <= tx <= cb[1]+3 and cb[2]-3 <= ty <= cb[3]+3):
                ewords = set(e.text.lower().split())
                if ewords & deleted_words:
                    additions.add(e.handle)
                    break
    return additions


def preserve_label_boxes(entities: list[EntityInfo], deletion: set) -> set:
    """Keep POLYLINE/LWPOLYLINE n=5 boxes around KEPT text labels.

    Bidirectional: for each kept TEXT, find the nearest n=5 box within 0.30 DXF units.
    """
    kept_texts = [(e.points[0][0], e.points[0][1])
                  for e in entities
                  if e.handle not in deletion and e.etype in ('TEXT', 'MTEXT')]

    preservations = set()
    for e in entities:
        if e.handle in deletion and e.meta.get('n_vertices') == 5:
            verts = e.meta.get('vertices', [])
            if len(verts) < 4:
                continue
            cx = sum(v[0] for v in verts[:4]) / 4
            cy = sum(v[1] for v in verts[:4]) / 4
            for tx, ty in kept_texts:
                if math.hypot(tx-cx, ty-cy) < 0.30:
                    preservations.add(e.handle)
                    break
    return preservations


def preserve_ground_refs(entities: list[EntityInfo], deletion: set,
                         kept_handles: set) -> set:
    """Keep POLYLINE n=3 L-shapes connecting instrument tags to ground symbols.

    Identifies short L-shapes (total length < 1.0 DXF units) starting at a kept
    tag box's right edge.
    """
    kept_boxes = [e for e in entities
                  if e.handle in kept_handles
                  and e.meta.get('n_vertices') == 5]

    preservations = set()
    for e in entities:
        if e.handle not in deletion:
            continue
        if e.meta.get('n_vertices') != 3:
            continue

        verts = e.meta.get('vertices', [])
        if len(verts) != 3:
            continue

        seg1 = math.hypot(verts[1][0]-verts[0][0], verts[1][1]-verts[0][1])
        seg2 = math.hypot(verts[2][0]-verts[1][0], verts[2][1]-verts[1][1])
        if seg1 + seg2 >= 1.0:
            continue

        v0x, v0y = verts[0]
        for box in kept_boxes:
            bverts = box.meta.get('vertices', [])
            if len(bverts) < 4:
                continue
            bx_max = max(v[0] for v in bverts[:4])
            by_min = min(v[1] for v in bverts[:4])
            by_max = max(v[1] for v in bverts[:4])
            if (abs(v0x - bx_max) < 0.05 and
                by_min - 0.05 <= v0y <= by_max + 0.05):
                preservations.add(e.handle)
                break
    return preservations


def preserve_arrow_triangles(entities: list[EntityInfo], deletion: set,
                             clouds: list[Cloud]) -> set:
    """Keep small red HATCH arrowhead triangles near but outside cloud boundaries."""
    preservations = set()
    for e in entities:
        if not (e.handle in deletion and e.etype == 'HATCH' and e.color == 1):
            continue
        if not (e.bbox and (e.bbox[1]-e.bbox[0]) < 0.2 and (e.bbox[3]-e.bbox[2]) < 0.2):
            continue

        # Check if any point is inside a polygon
        inside_poly = False
        for cloud in clouds:
            for pt in e.points:
                if pip_raycast(pt[0], pt[1], cloud.verts):
                    inside_poly = True; break
            if inside_poly: break

        if not inside_poly:
            # Near a cloud bbox edge
            for cloud in clouds:
                cb = cloud.bbox
                if (abs(e.bbox[2]-cb[3]) < 0.3 or abs(e.bbox[3]-cb[2]) < 0.3 or
                    abs(e.bbox[0]-cb[1]) < 0.3 or abs(e.bbox[1]-cb[0]) < 0.3):
                    preservations.add(e.handle)
                    break
    return preservations


def exclude_structural_verts(entities: list[EntityInfo],
                             deletion: set) -> set:
    """Exclude POLYLINEs that are just VERTEX containers with no useful points.

    POLYLINEs whose vertices don't add meaningful new test points beyond
    what individual VERTEX entities already cover.
    """
    # In Pair 1, the POLYLINE entities with n_vertices are meaningful shapes
    # (circles n=100, L-shapes n=3, boxes n=5). We keep them.
    # VERTEX sub-entities are separate from POLYLINE — handled by ezdxf already.
    return set()


# ═══════════════════════════════════════════════════════════════════════
# Phase 5-7: Deletion, layer fix, DWG export
# ═══════════════════════════════════════════════════════════════════════

def run_deletion(dxf: Path, handles: set, out_dxf: Path, delete_script: Path):
    """Execute text-based entity deletion by handle."""
    hj = out_dxf.parent / f'{out_dxf.stem}_handles.json'
    with open(hj, 'w') as f:
        json.dump(sorted(list(handles)), f)

    r = subprocess.run(
        ['python3', str(delete_script), str(dxf), str(out_dxf), str(hj)],
        capture_output=True, text=True)
    for line in r.stdout.strip().split('\n'):
        if any(k in line for k in ('Del', 'Kep', 'Fix', 'Writ', 'Top', 'lev')):
            print(f"  {line}")
    if r.returncode != 0:
        print(f"  ERROR: {r.stderr[-300:]}")
        sys.exit(1)


def fix_layers(dxf: Path, out_dxf: Path, script: Path):
    """Fix negative layer color 62 values → positive."""
    r = subprocess.run(
        ['python3', str(script), str(dxf), str(out_dxf)],
        capture_output=True, text=True)
    print(f"  {r.stdout.strip()}")
    if r.returncode != 0:
        print(f"  ERROR: {r.stderr[-300:]}")


def export_dwg(dxf: Path, dwg: Path, qcad_bin: Path):
    """Export DXF → DWG using QCAD Pro headless with force-visible layers."""
    qd = qcad_bin.parent
    js = textwrap.dedent("""\
    "use strict";
    include("scripts/library.js");
    function main() {
        var args = RSettings.getArgumentsList();
        if (args.length < 4) { return; }
        var inputFile = args[2], outputFile = args[3];
        var storage = new RMemoryStorage();
        var doc = new RDocument(storage, new RSpatialIndexSimple());
        var di = new RDocumentInterface(doc);
        if (di.importFile(inputFile) !== RDocumentInterface.IoErrorNoError)
            { qcad.quit(1); return; }
        var op = new RModifyObjectsOperation();
        doc.queryAllLayers().forEach(function(l) {
            if (l.isFrozen()) l.setFrozen(false);
            if (l.isOff()) l.setOff(false);
            var c = l.getColor(); if (c && c.value < 0) l.setColor(Math.abs(c.value));
            op.addObject(l, false);
        });
        di.applyOperation(op);
        var ok = di.exportFile(outputFile, "R32 (2018) DWG");
        print(ok ? "SUCCESS: " + outputFile : "ERROR: export failed");
        qcad.quit(ok ? 0 : 1);
    }
    if (typeof(including)==="undefined"||!including) main();
    """)

    js_path = dwg.parent / 'qcad_export.js'
    js_path.write_text(js)

    subprocess.run(['pkill', '-9', '-f', 'qcad'], capture_output=True)
    env = {
        'HOME': os.environ.get('HOME', '/home/user'),
        'LD_LIBRARY_PATH': f'{qd}:{qd}/plugins',
        'PATH': '/usr/bin:/bin',
    }
    r = subprocess.run(
        [str(qcad_bin), '-no-gui', '-platform', 'offscreen',
         '-allow-multiple-instances', '-autostart', str(js_path),
         str(dxf), str(dwg)],
        capture_output=True, text=True, timeout=120, env=env)

    for line in r.stdout.strip().split('\n'):
        if any(k in line for k in ('SUCCESS', 'ERROR', 'failed')):
            print(f"  {line.strip()}")
    if not dwg.exists():
        print("  QCAD ERROR: DWG not created")
        for line in (r.stderr or '').split('\n')[-5:]:
            print(f"    {line}")
        sys.exit(1)
    js_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════
# Phase 8: Verification
# ═══════════════════════════════════════════════════════════════════════

def verify(dxf: Path, deletion: set, restored: set):
    """Verify deletions took effect and restored entities are present."""
    import ezdxf
    doc = ezdxf.readfile(str(dxf))
    msp = doc.modelspace()
    live = {e.dxf.handle.upper() for e in msp}

    errors = []
    for h in deletion:
        if h in live:
            errors.append(f"{h} should be deleted")
    for h in restored:
        if h not in live:
            errors.append(f"{h} should be present")

    n = sum(1 for _ in msp)
    ok = "PASSED" if not errors else f"{len(errors)} ERRORS"
    print(f"  Verification: {ok}")
    if errors:
        for e in errors[:15]:
            print(f"    - {e}")
    return n, errors


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def run(args):
    ws = args.workspace; ws.mkdir(parents=True, exist_ok=True)
    skill_dir = Path(__file__).parent.resolve()
    del_script = skill_dir / 'delete_entities_text.py'
    fix_script = skill_dir / 'fix_layer_visibility.py'

    HEADER = "=" * 52
    print(f"{HEADER}\n  Cloud Deletion Pipeline V12\n{HEADER}")

    # ── Phase 1 ──
    print("\n── Phase 1: PDF clouds ──")
    clouds = extract_clouds(args.pdf)
    for c in clouds:
        b = c.bbox
        print(f"  {c.label}: {c.side} "
              f"x=[{b[0]:.2f},{b[1]:.2f}] y=[{b[2]:.2f},{b[3]:.2f}] "
              f"h={c.height:.2f}")

    # ── Phase 2 ──
    print("\n── Phase 2: DXF entity index ──")
    entities = build_entity_index(args.dxf)
    tc = defaultdict(int)
    for e in entities: tc[e.etype] += 1
    print(f"  {len(entities)} entities: {dict(sorted(tc.items()))}")

    # ── Phase 3 ──
    print("\n── Phase 3: Matching ──")
    deletion, boundary, stats = match_entities(entities, clouds, args.strict_margin)
    print(f"  Candidates: {len(deletion)}, boundary-touching: {len(boundary)}")
    for lbl, s in stats.items():
        t = sum(s.values())
        print(f"  {lbl}: {t} (T1={s['t1']} T2={s['t2']} T3={s['t3']} T4={s['t4']})")

    # ── Phase 4 ──
    print("\n── Phase 4: Filtering ──")
    sw = content_sweep(entities, deletion, clouds); deletion |= sw
    if sw: print(f"  Content sweep +{len(sw)}: {sorted(sw)[:8]}...")

    lbs = preserve_label_boxes(entities, deletion); deletion -= lbs
    if lbs: print(f"  Label boxes -{len(lbs)}: {sorted(lbs)}")

    kept = {e.handle for e in entities} - deletion
    grs = preserve_ground_refs(entities, deletion, kept); deletion -= grs
    if grs: print(f"  Ground refs -{len(grs)}: {sorted(grs)}")

    ars = preserve_arrow_triangles(entities, deletion, clouds); deletion -= ars
    if ars: print(f"  Arrow triangles -{len(ars)}: {sorted(ars)}")

    restored = lbs | grs | ars

    if args.overrides and args.overrides.exists():
        with open(args.overrides) as f: ov = json.load(f)
        for h in ov.get('add', []): deletion.add(h.upper())
        for h in ov.get('remove', []): deletion.discard(h.upper())
        for h in ov.get('restore', []):
            deletion.discard(h.upper()); restored.add(h.upper())
        print(f"  Overrides: +{len(ov.get('add',[]))}"
              f" -{len(ov.get('remove',[]))}"
              f" ~{len(ov.get('restore',[]))}")

    print(f"  → Final: {len(deletion)} deletions, {len(restored)} preserved")

    # ── Phase 5 ──
    print("\n── Phase 5: Entity deletion ──")
    run_deletion(args.dxf, deletion, ws/'deleted.dxf', del_script)

    # ── Phase 6 ──
    print("\n── Phase 6: Layer color fix ──")
    fix_layers(ws/'deleted.dxf', ws/'deleted_fixed.dxf', fix_script)

    # ── Phase 7 ──
    print("\n── Phase 7: DWG export ──")
    if args.qcad.exists():
        export_dwg(ws/'deleted_fixed.dxf', args.out_dwg, args.qcad)
        sz = args.out_dwg.stat().st_size
        print(f"  {args.out_dwg.name}: {sz:,}B ({sz/1024:.1f}KB)")
    else:
        print(f"  SKIP: qcad-bin not found")
        print(f"  DXF: {ws/'deleted_fixed.dxf'}")

    # ── Phase 8 ──
    if not args.no_verify:
        print("\n── Phase 8: Verification ──")
        n, errs = verify(ws/'deleted_fixed.dxf', deletion, restored)

        summary = {
            'version': 'V12',
            'pdf': str(args.pdf),
            'dxf': str(args.dxf),
            'dwg': str(args.out_dwg),
            'deletions': len(deletion),
            'restored': len(restored),
            'entities_remaining': n,
            'matching': {k: dict(v) for k, v in stats.items()},
            'filters': {
                'content_sweep': len(sw),
                'label_boxes_preserved': len(lbs),
                'ground_refs_preserved': len(grs),
                'arrow_triangles_preserved': len(ars),
            },
            'boundary_excluded': len(boundary),
            'errors': len(errs),
        }
        sp = ws / 'summary.json'
        with open(sp, 'w') as f: json.dump(summary, f, indent=2)
        print(f"  Summary: {sp}")

        if errs:
            return 1

    print(f"\n  \u2713 Pipeline complete")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Cloud \u2192 DXF Deletion \u2192 DWG Pipeline (V12)")

    ap.add_argument('--pdf', required=True, type=Path,
                    help='PDF with cloud annotations')
    ap.add_argument('--dxf', required=True, type=Path,
                    help='Original DXF (NOT previously modified)')
    ap.add_argument('--out-dwg', required=True, type=Path,
                    help='Output DWG path')
    ap.add_argument('--qcad', type=Path,
                    default=Path(os.environ.get('QCAD_DIR', 'qcad') + '/qcad-bin'))
    ap.add_argument('--workspace', type=Path,
                    default=Path(tempfile.mkdtemp(prefix='cdp_')))
    ap.add_argument('--overrides', type=Path,
                    help='JSON: {"add":[],"remove":[],"restore":[]}')
    ap.add_argument('--no-verify', action='store_true')
    ap.add_argument('--strict-margin', type=float, default=-0.08)

    args = ap.parse_args()

    for p, n in [(args.pdf, 'PDF'), (args.dxf, 'DXF')]:
        if not p.exists():
            sys.exit(f"ERROR: {n} not found: {p}")

    sys.exit(run(args))


if __name__ == '__main__':
    main()
