#!/usr/bin/env python3
"""
Hybrid PDF Annotation → DXF Deletion Pipeline v2
Improved coordinate transform: portrait top-half → pymupdf y-flip, 
portrait bottom-half → raw_direct (no flip).

Usage:
    python3 annotation_extractor.py --pdf 1.pdf --dxf 1.dxf --out /tmp/hybrid_analysis

Output:
    /tmp/hybrid_analysis/hybrid_analysis.json  — per-cloud entity lists with handles
"""
import re, json, os, argparse, math
import pymupdf
import ezdxf


def pdf_extract_clouds(pdf_path):
    """Extract all PolygonCloud /Vertices from a PDF."""
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    clouds = []
    for annot in page.annots():
        raw_obj = page.parent.xref_object(annot.xref)
        if '/IT /PolygonCloud' not in raw_obj:
            continue
        verts_match = re.search(r'/Vertices\s*\[([^\]]+)\]', raw_obj)
        if not verts_match:
            continue
        coords = [float(x) for x in verts_match.group(1).split()]
        raw_verts = [(coords[2*i], coords[2*i+1]) for i in range(len(coords)//2)]

        rect_match = re.search(r'/Rect\s*\[([^\]]+)\]', raw_obj)
        raw_rect = None
        if rect_match:
            r = [float(x) for x in rect_match.group(1).split()]
            raw_rect = [r[0], r[1], r[2], r[3]]

        nm_match = re.search(r'/NM\s*\(([^)]+)\)', raw_obj)
        clouds.append({
            "xref": annot.xref,
            "name": nm_match.group(1) if nm_match else None,
            "raw_rect": raw_rect,
            "raw_verts": raw_verts,
        })
    doc.close()
    return clouds


def build_dxf_index(dxf_path):
    """Index all deletable entities in the DXF modelspace."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    entities = []
    for e in msp.query("TEXT"):
        entities.append({"type": "TEXT", "handle": e.dxf.handle,
                         "x": e.dxf.insert.x, "y": e.dxf.insert.y,
                         "text": e.dxf.text.strip()})
    for e in msp.query("MTEXT"):
        entities.append({"type": "MTEXT", "handle": e.dxf.handle,
                         "x": e.dxf.insert.x, "y": e.dxf.insert.y,
                         "text": e.text.strip()})
    for e in msp.query("LINE"):
        entities.append({"type": "LINE", "handle": e.dxf.handle,
                         "x": (e.dxf.start.x + e.dxf.end.x) / 2,
                         "y": (e.dxf.start.y + e.dxf.end.y) / 2})
    for e in msp.query("CIRCLE"):
        entities.append({"type": "CIRCLE", "handle": e.dxf.handle,
                         "x": e.dxf.center.x, "y": e.dxf.center.y})
    for e in msp.query("ARC"):
        entities.append({"type": "ARC", "handle": e.dxf.handle,
                         "x": e.dxf.center.x, "y": e.dxf.center.y})
    return entities


def point_in_polygon(x, y, verts):
    inside = False
    n = len(verts)
    j = n - 1
    for i in range(n):
        xi, yi = verts[i][0], verts[i][1]
        xj, yj = verts[j][0], verts[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def get_dxf_extents(entities):
    if not entities:
        return 0, 0, 0, 0
    xs = [e["x"] for e in entities]
    ys = [e["y"] for e in entities]
    return min(xs), max(xs), min(ys), max(ys)


def transform_verts(raw_verts, mode, page_h=1224, scale=72):
    if mode == "pymupdf":
        return [(v[0] / scale, (page_h - v[1]) / scale) for v in raw_verts]
    elif mode == "raw_direct":
        return [(v[0] / scale, v[1] / scale) for v in raw_verts]
    elif mode == "swap_xy":
        return [(v[1] / scale, v[0] / scale) for v in raw_verts]
    elif mode == "pymupdf_swap":
        return [((page_h - v[1]) / scale, v[0] / scale) for v in raw_verts]
    return [(v[0] / scale, v[1] / scale) for v in raw_verts]


def run_hybrid_pipeline(pdf_path, dxf_path, out_dir="."):
    os.makedirs(out_dir, exist_ok=True)
    clouds = pdf_extract_clouds(pdf_path)
    entities = build_dxf_index(dxf_path)
    xmin, xmax, ymin, ymax = get_dxf_extents(entities)

    print(f"DXF extents: x=[{xmin:.2f}, {xmax:.2f}], y=[{ymin:.2f}, {ymax:.2f}]")
    print(f"Total DXF entities: {len(entities)}")
    print(f"Clouds found: {len(clouds)}")

    results = []
    for i, cloud in enumerate(clouds):
        raw_verts = cloud["raw_verts"]
        raw_rect = cloud["raw_rect"]
        if raw_rect:
            rect_center_y = (raw_rect[1] + raw_rect[3]) / 2
        else:
            rect_center_y = sum(v[1] for v in raw_verts) / len(raw_verts)

        # Primary mapping based on center_y
        preferred_mode = "pymupdf" if rect_center_y > 700 else "raw_direct"
        dxf_verts = transform_verts(raw_verts, preferred_mode)
        inside = [e for e in entities if point_in_polygon(e["x"], e["y"], dxf_verts)]

        # Fallback: if empty, try alternate
        if len(inside) == 0:
            alt_mode = "raw_direct" if preferred_mode == "pymupdf" else "pymupdf"
            dxf_verts_alt = transform_verts(raw_verts, alt_mode)
            inside_alt = [e for e in entities if point_in_polygon(e["x"], e["y"], dxf_verts_alt)]
            if len(inside_alt) > 0:
                preferred_mode = alt_mode
                dxf_verts = dxf_verts_alt
                inside = inside_alt

        types = {}
        for e in inside:
            types[e["type"]] = types.get(e["type"], 0) + 1

        vxs = [v[0] for v in dxf_verts]
        vys = [v[1] for v in dxf_verts]

        result = {
            "cloud_index": i,
            "xref": cloud["xref"],
            "name": cloud["name"],
            "mode": preferred_mode,
            "raw_center_y": rect_center_y,
            "count": len(inside),
            "entity_types": types,
            "bbox": {
                "xmin": min(vxs), "xmax": max(vxs),
                "ymin": min(vys), "ymax": max(vys)
            },
            "in_bounds": (min(vxs) >= xmin - 1 and max(vxs) <= xmax + 1
                          and min(vys) >= ymin - 1 and max(vys) <= ymax + 1),
            "deletion_handles": [e["handle"] for e in inside],
            "entities": [
                {"type": e["type"], "handle": e["handle"],
                 "x": round(e["x"], 3), "y": round(e["y"], 3),
                 "text": e.get("text", "")}
                for e in inside
            ]
        }
        results.append(result)
        print(f"\nCloud {i} (xref={cloud['xref']}): mode={preferred_mode}, center_y={rect_center_y:.1f} → {len(inside)} entities {types}")

    out_json = os.path.join(out_dir, "hybrid_analysis.json")
    with open(out_json, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved: {out_json}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--dxf", required=True)
    parser.add_argument("--out", default="/tmp/hybrid_v2")
    args = parser.parse_args()
    run_hybrid_pipeline(args.pdf, args.dxf, args.out)
