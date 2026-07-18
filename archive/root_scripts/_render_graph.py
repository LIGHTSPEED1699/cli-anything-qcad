#!/usr/bin/env python3
"""Render module dependency graph as SVG using networkx + matplotlib."""

import json, subprocess, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx

def run_pydeps():
    result = subprocess.run(
        ["pydeps", "cli_anything", "--only", "cli_anything", "--show-deps",
         "--max-bacon", "2", "--cluster", "--no-dot"],
        capture_output=True, text=True, cwd="."
    )
    return json.loads(result.stdout)

def main():
    data = run_pydeps()

    # Collect meaningful nodes and edges
    G = nx.DiGraph()
    colors = {}
    group_fill = {
        "backends":  "#e1f5fe",
        "core":      "#f3e5f5",
        "engines":   "#fff3e0",
        "utils":     "#e8f5e9",
        "pipelines": "#fce4ec",
        "entry":     "#f5f5f5",
    }

    def add_node(name, info):
        parts = name.split(".")
        short = parts[-1]
        group = parts[2] if len(parts) >= 4 else "entry"
        G.add_node(short)
        if short not in colors:
            colors[short] = group_fill.get(group, "#f5f5f5")

    for name, info in data.items():
        if name in ("cli_anything", "cli_anything.qcad"):
            continue
        if "vlm_automation" in name:
            continue
        if info.get("path", "").endswith("/__init__.py") and not info.get("imports"):
            continue
        parts = name.split(".")
        if len(parts) < 3:
            continue
        add_node(name, info)

        for imp in info.get("imports", []):
            if imp in data and "vlm_automation" not in imp and imp not in ("cli_anything", "cli_anything.qcad"):
                iparts = imp.split(".")
                if len(iparts) >= 2:
                    idst = iparts[-1]
                    if idst != parts[-1]:
                        # Also ensure import target exists as a node
                        add_node(imp, data.get(imp, {}))
                        G.add_edge(parts[-1], idst)

    # Layout
    pos = nx.spring_layout(G, k=1.5, iterations=50, seed=42)

    fig, ax = plt.subplots(1, 1, figsize=(20, 14))

    # Draw edges
    nx.draw_networkx_edges(G, pos, ax=ax, arrows=True, arrowstyle='-|>',
                          arrowsize=15, edge_color='#888888', alpha=0.6, width=1.0)

    # Draw nodes with color
    for node in G.nodes():
        nx.draw_networkx_nodes(G, pos, nodelist=[node], ax=ax,
                               node_color=colors[node], node_size=1800,
                               edgecolors='#444444', linewidths=1.5)

    # Draw labels
    nx.draw_networkx_labels(G, pos, font_size=7, font_family='monospace')

    ax.set_title("cli-anything-qcad — Module Dependency Graph", fontsize=14, pad=20)
    ax.axis('off')

    # Legend
    legend_patches = []
    for g, c in group_fill.items():
        legend_patches.append(
            mpatches.Patch(facecolor=c, edgecolor='#444444', label=g.title())
        )
    ax.legend(handles=legend_patches, loc='upper right', framealpha=0.9)

    plt.tight_layout()
    plt.savefig("/tmp/qcad_graph.png", dpi=200, bbox_inches='tight')
    print(f"Saved: /tmp/qcad_graph.png ({G.number_of_nodes()} nodes, {G.number_of_edges()} edges)")

if __name__ == "__main__":
    main()
