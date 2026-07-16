#!/usr/bin/env python3
"""Verify cloned DXF: check source row integrity, left-side contamination, 
target-row clones, EPAC duplicates, and T10+ contamination.

Usage:
    python3 verify_clone.py <source.dxf> <clone.dxf> <source_rows> <target_rows>
"""
import sys, json, re
from collections import Counter, defaultdict
try:
    import ezdxf
except ImportError:
    print("FAIL: ezdxf required"); sys.exit(1)

def entity_y(ent):
    et = ent.dxftype()
    if et in ("TEXT", "MTEXT"): return ent.dxf.insert.y
    elif et == "LINE": return (ent.dxf.start.y + ent.dxf.end.y) / 2.0
    elif et == "ARC": return ent.dxf.center.y
    elif et in ("CIRCLE",): return ent.dxf.center.y
    elif et == "LWPOLYLINE":
        pts = list(ent.get_points("xy"))
        return sum(p[1] for p in pts)/len(pts) if pts else 0
    return 0

def entity_x_range(ent):
    """Return (min_x, max_x) for an entity."""
    et = ent.dxftype()
    if et in ("TEXT", "MTEXT"): x = ent.dxf.insert.x; return (x, x)
    elif et == "LINE": 
        return (min(ent.dxf.start.x, ent.dxf.end.x), max(ent.dxf.start.x, ent.dxf.end.x))
    elif et == "ARC":
        cx, r = ent.dxf.center.x, ent.dxf.radius
        return (cx - r, cx + r)
    elif et == "LWPOLYLINE":
        pts = list(ent.get_points("xy"))
        xs = [p[0] for p in pts]
        return (min(xs), max(xs))
    return (0, 0)

def entity_text(ent):
    if ent.dxftype() == "TEXT": return ent.dxf.text or ""
    if ent.dxftype() == "MTEXT": return ent.text or ""
    return ""

def sig(ent):
    """Unique signature for entity comparison."""
    et = ent.dxftype()
    if et == "LINE":
        return ("LINE", round(ent.dxf.start.x,4), round(ent.dxf.start.y,4), 
                round(ent.dxf.end.x,4), round(ent.dxf.end.y,4))
    elif et == "ARC":
        return ("ARC", round(ent.dxf.center.x,4), round(ent.dxf.center.y,4), round(ent.dxf.radius,4))
    elif et in ("TEXT", "MTEXT"):
        t = entity_text(ent).strip()
        return ("TEXT", t, round(ent.dxf.insert.x,4), round(ent.dxf.insert.y,4))
    elif et == "LWPOLYLINE":
        pts = tuple((round(p[0],4), round(p[1],4)) for p in ent.get_points("xy"))
        return ("LWP", pts)
    elif et == "INSERT":
        return ("INSERT", ent.dxf.name, round(ent.dxf.insert.x,4), round(ent.dxf.insert.y,4))
    return None

def get_row_centers(doc):
    rc = {}
    for ent in doc.modelspace():
        if ent.dxftype() == "TEXT":
            t = (ent.dxf.text or "").strip()
            m = re.match(r"^\((\d+)\)$", t)
            if m: rc[int(m.group(1))] = ent.dxf.insert.y
    return rc

def verify(src_path, clone_path, src_rows, tgt_rows, tol=0.25):
    src = ezdxf.readfile(src_path)
    clone = ezdxf.readfile(clone_path)
    issues = []
    rc = get_row_centers(src)

    # Build source sigs
    src_sigs = {sig(e) for e in src.modelspace() if sig(e) is not None}

    # Find added entities
    added = []
    for e in clone.modelspace():
        s = sig(e)
        if s and s not in src_sigs:
            added.append((s, e))

    # 1. Check source rows unchanged  
    for r in src_rows:
        yc = rc.get(r)
        if yc is None: continue
        for et in ["LINE", "ARC", "TEXT"]:
            sc = sum(1 for e in src.modelspace() if e.dxftype()==et and abs(entity_y(e)-yc)<tol)
            cc = sum(1 for e in clone.modelspace() if e.dxftype()==et and abs(entity_y(e)-yc)<tol)
            if sc != cc:
                issues.append(f"SRC ROW {r}: {et} {sc}→{cc} (changed)")

    # 2. Check T10+ unchanged (no contamination)
    for r in range(10, 25):
        yc = rc.get(r)
        if yc is None: continue
        for et in ["LINE", "ARC", "TEXT"]:
            sc = sum(1 for e in src.modelspace() if e.dxftype()==et and abs(entity_y(e)-yc)<tol)
            cc = sum(1 for e in clone.modelspace() if e.dxftype()==et and abs(entity_y(e)-yc)<tol)
            if sc != cc:
                issues.append(f"LOWER ROW {r}: {et} {sc}→{cc} (contaminated)")

    # 3. Check added entities for left-side contamination
    left_added = [(s, e) for s, e in added if s[0] not in ("INSERT",) and
                  (s[0] == "TEXT" and s[2] < 15) or
                  (s[0] == "LINE" and (s[1]+s[3])/2 < 15) or 
                  (s[0] == "ARC" and s[1] < 15)]
    if left_added:
        issues.append(f"LEFT-SIDE CONTAMINATION: {len(left_added)} entities on left side (x<15)")
        for s, e in left_added[:5]:
            if s[0] == "TEXT":
                issues.append(f"  TEXT '{s[1]}' at ({s[2]:.4f}, {s[3]:.4f})")
            elif s[0] == "LINE":
                issues.append(f"  LINE ({s[1]:.4f},{s[2]:.4f})-({s[3]:.4f},{s[4]:.4f})")

    # 4. Check EPAC duplicates
    epacs = Counter()
    for e in clone.modelspace():
        t = entity_text(e).strip()
        if t.startswith("EPAC"):
            y = round(entity_y(e), 4)
            x = round(e.dxf.insert.x if e.dxftype()=="TEXT" else 0, 4)
            epacs[(t, x, y)] += 1
    epac_dups = {k: v for k, v in epacs.items() if v > 1}
    if epac_dups:
        issues.append(f"EPAC DUPLICATES: {len(epac_dups)} types")

    # 5. Check for text overlaps (same position)
    txt_pos = defaultdict(list)
    for e in clone.modelspace():
        if e.dxftype() not in ("TEXT", "MTEXT"): continue
        x, y = round(e.dxf.insert.x, 4), round(e.dxf.insert.y, 4)
        t = entity_text(e).strip()
        txt_pos[(x, y)].append(t)
    overlaps = {p: ts for p, ts in txt_pos.items() if len(ts) > 1}
    if overlaps:
        issues.append(f"TEXT OVERLAPS at same position: {len(overlaps)} spots")

    # 6. Report target row additions
    stats = {}
    for r in tgt_rows:
        yc = rc.get(r)
        if yc is None: continue
        added_sigs = set()
        for s, e in added:
            ey = entity_y(e)
            if abs(ey - yc) < tol:
                added_sigs.add(s)
        stats[f"T{r}_added"] = len(added_sigs)

    stats["source_rows"] = src_rows
    stats["target_rows"] = tgt_rows
    stats["total_added"] = len(added)
    stats["left_side_added"] = len(left_added)
    stats["epac_duplicate_types"] = len(epac_dups)
    stats["total_entities"] = len(list(clone.modelspace()))

    return {"passed": len(issues) == 0, "issues": issues, "stats": stats}

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__); sys.exit(1)
    src = sys.argv[1]; out = sys.argv[2]
    src_r = [int(x) for x in sys.argv[3].split(",")]
    tgt_r = [int(x) for x in sys.argv[4].split(",")]
    result = verify(src, out, src_r, tgt_r)
    print(json.dumps(result, indent=2))
    if result["passed"]:
        print("\n✅ ALL CHECKS PASSED")
    else:
        print(f"\n❌ {len(result['issues'])} ISSUES")
        for i in result["issues"]:
            print(f"  - {i}")
        sys.exit(1)
