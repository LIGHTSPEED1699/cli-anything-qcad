#!/usr/bin/env python3
"""
Visual collision detector for DXF files.
Runs BEFORE CAD export to catch text/tag overlaps, intersecting leaders,
and other spatial conflicts that only show up in rendered previews.

Usage:
    python scripts/check_text_collision.py drawing.dxf --margin 0.02 --region 18,18.5,22,22
"""
import argparse
import sys
from pathlib import Path

def bbox_text(entity):
    """Approx bounding box for TEXT/MTEXT."""
    try:
        ix, iy = entity.dxf.insert.x, entity.dxf.insert.y
    except AttributeError:
        return None
    th = getattr(entity.dxf, 'height', 0.125)
    xscale = getattr(entity.dxf, 'xscale', 1.0)
    text = entity.dxf.text if hasattr(entity.dxf, 'text') else ''
    width = len(text) * th * 0.6 * xscale
    return (ix - th*0.1, iy - th*0.2, ix + width + th*0.1, iy + th*1.2)

def bbox_insert(entity, all_blocks):
    """Approx bounding box for INSERT via block definition."""
    bx = getattr(entity.dxf, 'insert', (0,0))
    sx = getattr(entity.dxf, 'xscale', 1.0)
    sy = getattr(entity.dxf, 'yscale', 1.0)
    bname = getattr(entity.dxf, 'name', '')
    # crude default
    bw, bh = 0.5, 0.5
    block = all_blocks.get(bname)
    if block:
        verts = []
        for e in block:
            b = estimate_bbox(e)
            if b: verts.append((b[0], b[1])); verts.append((b[2], b[3]))
        if verts:
            import statistics
            bw = max(v[0] for v in verts) - min(v[0] for v in verts)
            bh = max(v[1] for v in verts) - min(v[1] for v in verts)
    return (bx[0], bx[1], bx[0] + bw*sx, bx[1] + bh*sy)

def bbox_lw(entity):
    pts = list(entity.vertices_in_wcs())
    if not pts: return None
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))

def bbox_line(entity):
    p1 = entity.dxf.start
    p2 = entity.dxf.end
    return (min(p1.x,p2.x), min(p1.y,p2.y), max(p1.x,p2.x), max(p1.y,p2.y))

def bbox_arc(entity):
    c = entity.dxf.center
    r = entity.dxf.radius
    return (c.x-r, c.y-r, c.x+r, c.y+r)

def estimate_bbox(entity):
    t = entity.dxftype()
    if t in ('TEXT','MTEXT','ATTRIB','ATTDEF'):
        return bbox_text(entity)
    if t == 'INSERT':
        return bbox_insert(entity, {})
    if t == 'LWPOLYLINE':
        return bbox_lw(entity)
    if t == 'LINE':
        return bbox_line(entity)
    if t == 'ARC':
        return bbox_arc(entity)
    return None

def overlaps(a, b, margin=0.02):
    return not (a[2] < b[0] - margin or a[0] > b[2] + margin or
                a[3] < b[1] - margin or a[1] > b[3] + margin)

def main():
    parser = argparse.ArgumentParser(description='DXF visual collision detector')
    parser.add_argument('dxf', help='Input DXF file')
    parser.add_argument('--margin', type=float, default=0.02)
    parser.add_argument('--region', help='minx,miny,maxx,maxy filter')
    args = parser.parse_args()

    try:
        import ezdxf
    except ImportError:
        print("ERROR: ezdxf is required. pip install ezdxf")
        sys.exit(1)

    doc = ezdxf.readfile(args.dxf)
    msp = doc.modelspace()

    region = None
    if args.region:
        region = tuple(map(float, args.region.split(',')))
        assert len(region) == 4

    entities = []
    for e in msp:
        b = estimate_bbox(e)
        if not b:
            continue
        if region:
            if not (b[2] >= region[0] and b[0] <= region[2] and b[3] >= region[1] and b[1] <= region[3]):
                continue
        entities.append((e.dxf.handle, e.dxftype(), b))

    collisions = []
    for i in range(len(entities)):
        for j in range(i+1, len(entities)):
            h1, t1, b1 = entities[i]
            h2, t2, b2 = entities[j]
            if overlaps(b1, b2, args.margin):
                collisions.append((h1, t1, b1, h2, t2, b2))

    if not collisions:
        print(f"PASS: No collisions found (margin={args.margin}, {len(entities)} entities checked)")
        sys.exit(0)

    print(f"FAIL: {len(collisions)} collision(s) found (margin={args.margin})")
    for h1, t1, b1, h2, t2, b2 in collisions:
        print(f"  {h1}({t1}) [{b1[0]:.3f},{b1[1]:.3f},{b1[2]:.3f},{b1[3]:.3f}]  vs  {h2}({t2}) [{b2[0]:.3f},{b2[1]:.3f},{b2[2]:.3f},{b2[3]:.3f}]")
    sys.exit(1)

if __name__ == '__main__':
    main()
