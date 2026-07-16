#!/usr/bin/env python3
"""Visualize hybrid analysis results with color-coded clouds.

Usage:
    python3 visualize_hybrid_results.py --json hybrid_analysis.json --dxf 1.dxf --out /tmp/hybrid_results.png

Produces two images:
    /tmp/hybrid_results.png          — 4 subplots, one per cloud
    /tmp/hybrid_results_combined.png — single combined overlay
"""
import json, argparse, os
import ezdxf
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.lines import Line2D


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_dxf_entities(dxf_path):
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    entities = []
    for e in msp.query("TEXT"):
        entities.append({"type": "TEXT", "x": e.dxf.insert.x, "y": e.dxf.insert.y, "text": e.dxf.text.strip()})
    for e in msp.query("MTEXT"):
        entities.append({"type": "MTEXT", "x": e.dxf.insert.x, "y": e.dxf.insert.y, "text": e.text.strip()})
    for e in msp.query("LINE"):
        entities.append({"type": "LINE", "x": (e.dxf.start.x + e.dxf.end.x) / 2, "y": (e.dxf.start.y + e.dxf.end.y) / 2})
    for e in msp.query("CIRCLE"):
        entities.append({"type": "CIRCLE", "x": e.dxf.center.x, "y": e.dxf.center.y})
    for e in msp.query("ARC"):
        entities.append({"type": "ARC", "x": e.dxf.center.x, "y": e.dxf.center.y})
    return entities


def plot_hybrid_results(analysis_json, dxf_path, out_png):
    analysis = load_json(analysis_json)
    entities = load_dxf_entities(dxf_path)

    fig, axes = plt.subplots(2, 2, figsize=(16, 14))
    axes = axes.flatten()
    colors = ['#FF4444', '#44FF44', '#4444FF', '#FFAA00']

    for idx, ax in enumerate(axes):
        ax.scatter([e["x"] for e in entities], [e["y"] for e in entities],
                   s=8, c='lightgray', alpha=0.4, zorder=1, label='Other entities')

        highlighted = set()
        for ci, cloud in enumerate(analysis):
            if ci == idx:
                cloud_color = colors[ci % len(colors)]
                for e_data in cloud.get("entities", []):
                    ent = next((x for x in entities if abs(x["x"] - e_data["x"]) < 0.001 and abs(x["y"] - e_data["y"]) < 0.001), None)
                    if ent:
                        highlighted.add((ent["x"], ent["y"]))
                        if ent["type"] == "TEXT":
                            ax.scatter(ent["x"], ent["y"], s=80, c=cloud_color, alpha=0.8,
                                       zorder=4, edgecolors='black', linewidth=1)
                            ax.annotate(ent.get("text", ""), (ent["x"], ent["y"]),
                                        fontsize=7, color='darkblue', fontweight='bold',
                                        ha='center', va='bottom')
                        elif ent["type"] == "LINE":
                            ax.scatter(ent["x"], ent["y"], s=60, c='red', alpha=0.8,
                                       zorder=4, marker='s', edgecolors='black')
                        elif ent["type"] == "CIRCLE":
                            ax.scatter(ent["x"], ent["y"], s=60, c='green', alpha=0.8,
                                       zorder=4, marker='o', edgecolors='black')

                bb = cloud["bbox"]
                rect = patches.Rectangle((bb["xmin"], bb["ymin"]),
                                         bb["xmax"] - bb["xmin"], bb["ymax"] - bb["ymin"],
                                         linewidth=2, edgecolor=cloud_color,
                                         facecolor=cloud_color, alpha=0.15, zorder=2)
                ax.add_patch(rect)
                ax.set_title(
                    f"Cloud {ci} (xref={cloud['xref']})\n"
                    f"mode={cloud['mode']} | {cloud['count']} entities\n"
                    f"{cloud['entity_types']}",
                    fontsize=10, fontweight='bold', color=cloud_color
                )

        ax.set_aspect('equal')
        ax.grid(True, alpha=0.3)
        ax.set_xlabel('X')
        ax.set_ylabel('Y')

        main_cloud = analysis[idx] if idx < len(analysis) else None
        if main_cloud:
            bb = main_cloud["bbox"]
            margin = max(bb["xmax"] - bb["xmin"], bb["ymax"] - bb["ymin"]) * 0.4
            ax.set_xlim(bb["xmin"] - margin, bb["xmax"] + margin)
            ax.set_ylim(bb["ymin"] - margin, bb["ymax"] + margin)

    plt.suptitle("Hybrid Pipeline: Cloud Regions with Identified Entities\n"
                 "Colored dots = entities inside cloud | Gray dots = other DXF entities",
                 fontsize=12, fontweight='bold')
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.savefig(out_png, dpi=150, bbox_inches='tight')
    print(f"Saved: {out_png}")
    plt.close()

    # Combined view
    fig, ax = plt.subplots(figsize=(14, 10))
    ax.scatter([e["x"] for e in entities], [e["y"] for e in entities],
               s=6, c='gray', alpha=0.3, zorder=1, label='All entities')

    for ci, cloud in enumerate(analysis):
        cloud_color = colors[ci % len(colors)]
        bb = cloud["bbox"]
        rect = patches.Rectangle((bb["xmin"], bb["ymin"]),
                                 bb["xmax"] - bb["xmin"], bb["ymax"] - bb["ymin"],
                                 linewidth=2.5, edgecolor=cloud_color,
                                 facecolor=cloud_color, alpha=0.12, zorder=2)
        ax.add_patch(rect)

        for e in cloud.get("entities", []):
            ax.scatter(e["x"], e["y"], s=50, c=cloud_color,
                       alpha=0.85, zorder=4, edgecolors='black', linewidth=0.5)
            if e.get("text"):
                ax.annotate(e["text"], (e["x"], e["y"]), fontsize=6,
                            color=cloud_color, fontweight='bold', ha='center')

    ax.set_title("All Clouds Overlaid on DXF Entity Map", fontsize=13, fontweight='bold')
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.grid(True, alpha=0.3)
    ax.set_aspect('equal')

    legend_elements = [Line2D([0], [0], marker='s', color='w', markerfacecolor=colors[i],
                               markersize=10, label=f"Cloud {i} [{analysis[i]['mode']}] ({analysis[i]['count']} ents)")
                       for i in range(len(analysis))]
    ax.legend(handles=legend_elements, loc='upper right')

    combined_png = out_png.replace('.png', '_combined.png')
    plt.savefig(combined_png, dpi=150, bbox_inches='tight')
    print(f"Saved: {combined_png}")
    plt.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True)
    parser.add_argument("--dxf", required=True)
    parser.add_argument("--out", default="/tmp/hybrid_results.png")
    args = parser.parse_args()
    plot_hybrid_results(args.json, args.dxf, args.out)
