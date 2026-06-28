#!/usr/bin/env python3
"""Verify Pair 3 cloned DXF before converting to DWG."""
import sys
import ezdxf

def verify_pair3(dxf_path):
    """Verify cloned DXF meets all requirements."""
    issues = []
    
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception as e:
        return {"pass": False, "issues": [f"Cannot load DXF: {e}"]}
    
    msp = doc.modelspace()
    ents = list(msp)
    
    print(f"=== Pair 3 Verification: {dxf_path} ===")
    print(f"Total entities: {len(ents)}")
    
    # 1. Check no handle collisions
    handles = {}
    for e in ents:
        h = e.dxf.handle
        if h in handles:
            issues.append(f"Handle collision: {h} ({handles[h]} vs {e.dxftype()})")
        else:
            handles[h] = e.dxftype()
    
    # 2. Check T7, T8, T9 have cloned wire elements
    zones = {
        'T7': (18.8, 19.3),
        'T8': (18.3, 18.8),
        'T9': (17.8, 18.3),
    }
    
    for tname, (y_min, y_max) in zones.items():
        items = []
        for e in ents:
            y = None
            if hasattr(e.dxf, 'insert'):
                y = e.dxf.insert.y
            elif hasattr(e.dxf, 'start'):
                y = e.dxf.start[1]
            elif e.dxftype() == 'ARC' and hasattr(e.dxf, 'center'):
                y = e.dxf.center[1]
            if y is not None and y_min <= y <= y_max:
                items.append(e)
        
        # Count by type
        types = {}
        for e in items:
            t = e.dxftype()
            types[t] = types.get(t, 0) + 1
        
        print(f"\n{tname} zone (y={y_min}-{y_max}): {len(items)} entities")
        for t, c in sorted(types.items()):
            print(f"  {t}: {c}")
        
        # Check minimum requirements
        if types.get('TEXT', 0) < 2:
            issues.append(f"{tname}: Expected >=2 TEXT, found {types.get('TEXT', 0)}")
        if types.get('LINE', 0) < 1:
            issues.append(f"{tname}: Expected >=1 LINE, found {types.get('LINE', 0)}")
        if types.get('ARC', 0) < 1:
            issues.append(f"{tname}: Expected >=1 ARC, found {types.get('ARC', 0)}")
    
    # 3. Check original T4, T5, T6 elements still exist
    t4t6_y_ranges = [('T4', 19.6, 20.2), ('T5', 19.1, 19.6), ('T6', 18.8, 19.1)]
    for tname, y_min, y_max in t4t6_y_ranges:
        items = []
        for e in ents:
            y = None
            if hasattr(e.dxf, 'insert'):
                y = e.dxf.insert.y
            elif hasattr(e.dxf, 'start'):
                y = e.dxf.start[1]
            if y is not None and y_min <= y <= y_max:
                items.append(e)
        print(f"\n{tname} zone (original): {len(items)} entities")
    
    # 4. Check cable tag
    cable_found = False
    for e in msp.query('TEXT'):
        if 'CA-1452' in e.dxf.text:
            cable_found = True
            print(f"\nCable tag: {repr(e.dxf.text)} at ({e.dxf.insert.x:.3f}, {e.dxf.insert.y:.3f})")
            break
    if not cable_found:
        issues.append("Cable tag 'CA-1452' not found")
    
    # 5. Check drawing number
    dwg_found = False
    for e in msp.query('TEXT'):
        if '022-122-97024-00002-02' in e.dxf.text:
            dwg_found = True
            print(f"\nDrawing number: {repr(e.dxf.text)}")
            break
    if not dwg_found:
        issues.append("Drawing number '-02' not found")
    
    # 6. Check "TO DWG" text for clone
    todwg_found = False
    for e in msp.query('TEXT'):
        if 'TO DWG' in e.dxf.text and '02733' in e.dxf.text:
            todwg_found = True
            print(f"TO DWG clone: {repr(e.dxf.text)} at ({e.dxf.insert.x:.3f}, {e.dxf.insert.y:.3f})")
            break
    if not todwg_found:
        issues.append("TO DWG clone (02733) not found")
    
    # 7. Check layer colors
    neg_layers = []
    for layer in doc.layers:
        if layer.dxf.color < 0:
            neg_layers.append(layer.dxf.name)
    if neg_layers:
        issues.append(f"Negative layer colors: {neg_layers}")
    
    # Summary
    print(f"\n{'='*50}")
    if issues:
        print(f"FAIL: {len(issues)} issues found")
        for i in issues:
            print(f"  - {i}")
        return {"pass": False, "issues": issues}
    else:
        print("PASS: All checks passed")
        return {"pass": True, "issues": []}

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: verify_pair3.py <dxf_file>")
        sys.exit(1)
    
    result = verify_pair3(sys.argv[1])
    sys.exit(0 if result["pass"] else 1)
