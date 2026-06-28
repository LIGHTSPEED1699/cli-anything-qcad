#!/usr/bin/env python3
"""
Template: Pair 3 geometry clone pass.
Copies LINE/ARC/LWPOLYLINE from source terminal rows to target terminal rows,
avoiding duplicates already present at target positions.

Usage:
  python geometry_clone_pass.py <source.dxf> <target.dxf> <output.dxf> --src-rows 4,5,6 --tgt-rows 7,8,9 --dy -0.75
"""
import os, re, json, argparse
import ezdxf

def read_dxf(path):
    with open(path, 'rb') as f:
        return f.read().decode().splitlines(keepends=False)

def write_dxf(path, lines):
    with open(path, 'wb') as f:
        for line in lines:
            f.write(line.encode() + b'\r\n')

def build_entity_map(lines):
    es = ee = None
    for i, line in enumerate(lines):
        if line.strip() == "ENTITIES": es = i
        if es is not None and line.strip() == "ENDSEC": ee = i; break
    emap = {}
    i = es if es else 0
    while i < (ee if ee else len(lines)) - 1:
        if lines[i].strip() == "0":
            ent = lines[i+1].strip() if i+1 < len(lines) else ""
            if ent in {"TEXT","MTEXT","LINE","LWPOLYLINE","POLYLINE","INSERT","SOLID",
                        "ARC","CIRCLE","HATCH","ELLIPSE","SPLINE","ATTRIB"}:
                j = i+2; h = None
                while j < len(lines) and j < (ee or len(lines)):
                    if lines[j].strip() == "0": break
                    if lines[j].strip() == "5" and j+1 < len(lines): h = lines[j+1].strip()
                    j += 1
                if h and ent not in {"VERTEX","SEQEND"}: emap[h] = (i, j, ent)
                i = j - 1
        i += 1
    return emap, es, ee

def get_entity_y(lines, s, e):
    ys = []
    for i in range(s, e):
        if lines[i].strip() == "20" and i+1 < e:
            try: ys.append(float(lines[i+1]))
            except: pass
    return sum(ys)/len(ys) if ys else None

def get_entity_x_avg(lines, s, e):
    xs = []
    for i in range(s, e):
        if lines[i].strip() in ("10","11") and i+1 < e:
            try: xs.append(float(lines[i+1]))
            except: pass
    return sum(xs)/len(xs) if xs else None

def clone_entity(lines, s, e, nh, dy):
    blk = lines[s:e]; out, j = [], 0
    while j < len(blk):
        g = blk[j].strip()
        if g == "5": out.extend([blk[j], nh]); j += 2
        elif g == "330": out.extend([blk[j], "1F"]); j += 2
        elif g == "360": j += 2
        elif g.startswith("102") and j+1 < len(blk) and "XDICTIONARY" in blk[j+1]:
            j += 2
            while j < len(blk) and blk[j].strip() != "102":
                if blk[j].strip() == "0": break
                j += 1
            if j < len(blk) and blk[j].strip() == "102": j += 1
        elif g in ("20","21","23","24","25","26"):
            out.append(blk[j])
            if j+1 < len(blk):
                try: out.append(str(float(blk[j+1]) + dy))
                except: out.append(blk[j+1])
            j += 2
        else:
            out.append(blk[j]); j += 1
    return out

def get_terminal_y(doc):
    terminals = {}
    for e in doc.modelspace():
        if e.dxftype() == 'INSERT' and e.dxf.name == 'Wlltermn':
            tnum = None
            for att in e.attribs:
                if att.dxf.tag == 'TERMNUM':
                    try: tnum = int(att.dxf.text)
                    except: pass
                    break
            if tnum:
                terminals[tnum] = e.dxf.insert.y
    return terminals

def get_existing_positions(doc):
    positions = {}
    for e in doc.modelspace():
        y = x_avg = None
        if e.dxftype() == 'LINE':
            y = (e.dxf.start.y + e.dxf.end.y) / 2
            x_avg = (e.dxf.start.x + e.dxf.end.x) / 2
        elif e.dxftype() == 'ARC':
            y = e.dxf.center.y
            x_avg = e.dxf.center.x
        elif e.dxftype() == 'LWPOLYLINE':
            pts = e.get_points('xy')
            if pts:
                y = sum(p[1] for p in pts) / len(pts)
                x_avg = sum(p[0] for p in pts) / len(pts)
        
        if y and x_avg:
            key = (e.dxftype(), round(y, 3), round(x_avg, 3))
            positions[key] = e.dxf.handle
    return positions

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="Source DXF with geometry to clone")
    parser.add_argument("target", help="Target DXF to append clones into")
    parser.add_argument("output", help="Output DXF")
    parser.add_argument("--src-rows", type=lambda s: [int(x) for x in s.split(",")], default=[4,5,6])
    parser.add_argument("--tgt-rows", type=lambda s: [int(x) for x in s.split(",")], default=[7,8,9])
    parser.add_argument("--dy", type=float, default=-0.75, help="Y offset per clone")
    parser.add_argument("--tol", type=float, default=0.15, help="Y tolerance for matching")
    args = parser.parse_args()

    src_raw = read_dxf(args.source)
    tgt_raw = read_dxf(args.target)
    
    src_emap, src_es, src_ee = build_entity_map(src_raw)
    tgt_emap, tgt_es, tgt_ee = build_entity_map(tgt_raw)
    
    src_doc = ezdxf.readfile(args.source)
    tgt_doc = ezdxf.readfile(args.target)
    
    terminals = get_terminal_y(src_doc)
    existing = get_existing_positions(tgt_doc)
    
    print(f"Source terminals: {len(terminals)}")
    print(f"Existing entities in target: {len(existing)}")
    
    max_h = max(int(h, 16) for h in tgt_emap.keys())
    next_h = max_h + 1
    print(f"Starting new handles from 0x{next_h:04X}")
    
    clones = []
    handled = set()
    
    for src, tgt in zip(args.src_rows, args.tgt_rows):
        src_y = terminals[src]
        print(f"\nT{src} -> T{tgt} (dy={args.dy})")
        
        found = []
        for h, (s, e_ent, ent) in src_emap.items():
            if ent not in ('LINE', 'ARC', 'LWPOLYLINE'):
                continue
            y = get_entity_y(src_raw, s, e_ent)
            if y is None or abs(y - src_y) > args.tol:
                continue
            
            target_y = y + args.dy
            x_avg = get_entity_x_avg(src_raw, s, e_ent)
            key = (ent, round(target_y, 3), round(x_avg, 3))
            
            if key in existing:
                print(f"  SKIP {ent} h={h} (duplicate at target)")
                continue
            
            found.append(h)
        
        print(f"  New clones: {len(found)}")
        for h in found:
            if h in handled: continue
            handled.add(h)
            s, e_ent, et = src_emap[h]
            nh = f"{next_h:04X}"
            next_h += 1
            clones.append(clone_entity(src_raw, s, e_ent, nh, args.dy))
    
    print(f"\nTotal new clones: {len(clones)}")
    
    # Insert before ENDSEC
    insert_idx = tgt_ee
    for i in range(tgt_ee - 1, tgt_es, -1):
        if tgt_raw[i].strip() == "0":
            insert_idx = i
            break
    
    new_lines = tgt_raw[:insert_idx]
    for c in clones:
        new_lines.extend(c)
    new_lines.extend(tgt_raw[insert_idx:])
    
    for i in range(len(new_lines)-1, -1, -1):
        if new_lines[i].strip() == "EOF":
            new_lines = new_lines[:i+1]
            break
    else:
        while new_lines and new_lines[-1].strip() == "":
            new_lines.pop()
        if not new_lines or new_lines[-1].strip() != "EOF":
            new_lines.append("EOF")
    
    write_dxf(args.output, new_lines)
    print(f"\nWrote: {args.output}")

if __name__ == "__main__":
    main()
