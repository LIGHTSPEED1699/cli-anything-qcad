#!/usr/bin/env python3
"""Generate a clean Mermaid module-level dependency graph — final version."""

import json
import subprocess

def run_pydeps():
    result = subprocess.run(
        ["pydeps", "cli_anything", "--only", "cli_anything", "--show-deps",
         "--max-bacon", "2", "--cluster", "--no-dot"],
        capture_output=True, text=True, cwd="/home/hongbin/Repos/cli-anything-qcad"
    )
    return json.loads(result.stdout)

def short_name(mod_name):
    parts = mod_name.split(".")
    if len(parts) >= 4:
        return parts[-1]
    return parts[-1]

def group_for(mod_name):
    parts = mod_name.split(".")
    if len(parts) >= 4:
        return parts[2]
    return "other"

def main():
    data = run_pydeps()

    # Build meaningful module list (skip top-level __init__ stuff)
    modules = {}
    for name, info in data.items():
        if name in ("cli_anything", "cli_anything.qcad"):
            continue
        if info.get("path", "").endswith("/__init__.py") and not info.get("imports"):
            continue
        # Include if it has actual code or is imported by something meaningful
        if info.get("imports") or info.get("imported_by") or group_for(name) == "vlm_automation":
            modules[name] = info
        elif info.get("path", "").endswith(".py"):
            modules[name] = info

    # Filter edges: keep only meaningful connections
    edges = set()
    for src_name, info in modules.items():
        if "vlm_automation" in src_name:
            continue
        src = short_name(src_name)
        for imp in info.get("imports", []):
            if imp in modules and "vlm_automation" not in imp and imp not in ("cli_anything", "cli_anything.qcad"):
                dst = short_name(imp)
                if dst != src:
                    edges.add((src, dst))

    # --- Layout ---
    colors = {
        "backends":   "#0288d1",
        "core":       "#7b1fa2",
        "engines":    "#e65100",
        "utils":      "#2e7d32",
        "pipelines":  "#c62828",
        "other":      "#616161",
    }
    fills = {
        "backends":   "#e1f5fe",
        "core":       "#f3e5f5",
        "engines":    "#fff3e0",
        "utils":      "#e8f5e9",
        "pipelines":  "#fce4ec",
        "other":      "#f5f5f5",
    }

    # Group by subgraph
    by_group = {}
    for name in modules:
        if "vlm_automation" in name:
            continue
        g = group_for(name)
        by_group.setdefault(g, []).append(name)

    lines = ["```mermaid", "flowchart LR", ""]

    subgraph_order = ["backends", "core", "engines", "utils", "pipelines"]
    for g in subgraph_order:
        if g not in by_group:
            continue
        names = by_group[g]
        label = g.replace("_", " ").title()
        lines.append(f"    subgraph {g}[{label}]")
        for name in names:
            s = short_name(name)
            lines.append(f"        {s}[{s}]")
        lines.append("    end")
        lines.append("")

    # Special: qcad_cli floating outside subgraphs
    lines.append("    subgraph other[Entry Point]")
    lines.append(f"        qcad_cli[qcad_cli]")
    lines.append("    end")
    lines.append("")

    # Edges
    for src, dst in sorted(edges):
        lines.append(f"    {src} --> {dst}")

    lines.append("")
    # Class defs
    for g in subgraph_order + ["other"]:
        lines.append(f"    classDef {g} fill:{fills.get(g, '#f5f5f5')},stroke:{colors.get(g, '#616161')}")
    lines.append("")
    # Assign styles to subgraph nodes
    for g in subgraph_order:
        if g not in by_group:
            continue
        for name in by_group[g]:
            s = short_name(name)
            lines.append(f"    class {s} {g}")
    lines.append("    class qcad_cli other")
    lines.append("```")
    lines.append("")
    lines.append("**Note:** `vlm_automation/` contains 54 standalone scripts with no inter-module dependencies (all isolated).")
    print("\n".join(lines))

if __name__ == "__main__":
    main()
