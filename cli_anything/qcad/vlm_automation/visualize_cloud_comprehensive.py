#!/usr/bin/env python3
"""
Visualize PDF annotation clouds overlaid onto DXF entity positions
with color-coded mappings to compare results.
"""
import re, json, argparse, math
import pymupdf
import ezdxf
import matplotlib.pyplot as plt
import matplotlib.patches as patches

def load_dxf_entities(dxf_path):
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    entities = []
    for e in msp.query("TEXT"):
        entities.append({"type": "TEXT", "handle": e.dxf.handle, "x": e.dxf.insert.x, "y": e.dxf.insert.y, "text": e.dxf.text.strip()})
    for e in msp.query("LINE"):
        entities.append({"type": "LINE", "x": (e.dxf.start.x+e.dxf.end.x)/2, "y": (e.dxf.start.y+e.dxf.end.y)/2})
    for e in msp.query("CIRCLE"):
        entities.append({"type": "CIRCLE", "x": e.dxf.center.x, "y": e.dxf.center.y})
    return entities

def point_in_polygon(x, y, verts):
    inside = False
    n = len(verts)
    j = n - 1
    for i in range(n):
        xi, yi = verts[i][0], verts[i][1]
        xj, yj = verts[j][0], verts[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi):
            inside = not inside
        j = i
    return inside

def plot_cloud_comprehensive(pdf_path, dxf_path, out_png):
    doc_pdf = pymupdf.open(pdf_path)
    page = doc_pdf[0]
    dxf_entities = load_dxf_entities(dxf_path)
    
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
        nm = nm_match.group(1) if nm_match else str(annot.xref)
        
        clouds.append({"xref": annot.xref, "name": nm, "rect": raw_rect, "vertices": raw_verts})
    
    doc_pdf.close()
    
    fig = plt.figure(figsize=(16, 14))
    
    for idx, cloud in enumerate(clouds):
        ax1 = plt.subplot(4, 2, idx*2 + 1)
        ax2 = plt.subplot(4, 2, idx*2 + 2)
        
        raw_verts = cloud["vertices"]
        
        # Mapping 1: raw_direct
        v_rd = [(v[0]/72.0, v[1]/72.0) for v in raw_verts]
        hits_rd = {e["handle"]: e for e in dxf_entities if point_in_polygon(e["x"], e["y"], v_rd)}
        
        # Mapping 2: pymupdf (y-flip)
        v_pm = [(v[0]/72.0, (1224 - v[1])/72.0) for v in raw_verts]
        hits_pm = {e["handle"]: e for e in dxf_entities if point_in_polygon(e["x"], e["y"], v_pm)}
        
        def draw_panel(ax, verts, hits, title):
            non_hits = [e for e in dxf_entities if e["handle"] not in hits]
            if non_hits:
                ax.scatter([e["x"] for e in non_hits], [e["y"] for e in non_hits], 
                          s=8, c='lightgray', alpha=0.4, zorder=1, label='Outside')
            
            colors = {'TEXT': 'blue', 'MTEXT': 'navy', 'LINE': 'red', 'CIRCLE': 'green', 'ARC': 'orange'}
            if hits:
                by_type = {}
                for e in hits.values():
                    t = e["type"]
                    by_type.setdefault(t, []).append(e)
                for t, es in by_type.items():
                    ax.scatter([e["x"] for e in es], [e["y"] for e in es],
                              s=40, c=colors.get(t, 'purple'), alpha=0.8, zorder=3,
                              label=f'{t} ({len(es)})', edgecolors='black', linewidth=0.5)
                    if t == 'TEXT':
                        for e in sorted(es, key=lambda x: x["y"], reverse=True)[:8]:
                            ax.annotate(e["text"], (e["x"], e["y"]), fontsize=6, alpha=0.7,
                                       ha='center', va='bottom', color='darkblue')
            
            poly = patches.Polygon(verts, closed=True, fill=False, edgecolor='black',
                                    linewidth=2, linestyle='-', zorder=4)
            ax.add_patch(poly)
            ax.scatter([v[0] for v in verts], [v[1] for v in verts], 
                      s=50, c='yellow', zorder=5, edgecolors='black', linewidth=1)
            
            ax.set_title(title, fontsize=10, fontweight='bold')
            ax.set_aspect('equal')
            ax.grid(True, alpha=0.3)
            ax.set_xlabel('X')
            ax.set_ylabel('Y')
            
            all_x = [v[0] for v in verts]
            all_y = [v[1] for v in verts]
            margin = max(max(all_x) - min(all_x), max(all_y) - min(all_y)) * 0.3
            ax.set_xlim(min(all_x) - margin, max(all_x) + margin)
            ax.set_ylim(min(all_y) - margin, max(all_y) + margin)
            
            from matplotlib.lines import Line2D
            legend_elements = [Line2D([0], [0], marker='o', color='w', markerfacecolor=c, markersize=8, label=t)
                               for t, c in colors.items() if t in by_type]
            legend_elements.append(Line2D([0], [0], marker='o', color='w', markerfacecolor='lightgray', markersize=5, label='Entities'))
            ax.legend(handles=legend_elements + [Line2D([0], [0], color='black', lw=2, label='Cloud')], loc='upper right', fontsize=7)
        
        draw_panel(ax1, v_rd, hits_rd, f"Cloud {idx} (xref={cloud['xref']})\nRAW_DIRECT: {len(hits_rd)} entities")
        draw_panel(ax2, v_pm, hits_pm, f"Cloud {idx} (xref={cloud['xref']})\nPYMUPDF: {len(hits_pm)} entities")
    
    plt.suptitle("PDF Cloud → DXF Entity Mapping Comparison\nYellow dots = cloud vertices | Black border = cloud polygon | Colored dots = matched entities",
                 fontsize=12, fontweight='bold')
    plt.tight_layout(rect=[0, 0, 1, 0.97])
    plt.savefig(out_png, dpi=150, bbox_inches='tight')
    print(f"Saved visualization to {out_png}")
    plt.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--dxf", required=True)
    parser.add_argument("--out", default="/tmp/cloud_mapping_comprehensive.png")
    args = parser.parse_args()
    plot_cloud_comprehensive(args.pdf, args.dxf, args.out)
