"""Standalone CLI for DWG-native editing and CAD-space inspection.

    dwg index   drawing.dwg                    -> JSON entity index
    dwg find    drawing.dwg --text "TB-21"     -> matching entities
    dwg near    drawing.dwg --x 8.1 --y 12.3   -> entities near a CAD point
    dwg render  drawing.dwg --out x.png --x 26 --y 8 --w 4 --h 4
    dwg apply   drawing.dwg --out o.dwg --ops ops.json
    dwg verify  before.dwg after.dwg           -> what changed

Deliberately standalone: it talks to QCAD's scripting engine directly and does not
depend on the PDF markup pipeline, so it can be used from a web UI or a chat agent
without the DWG -> PDF -> DXF round-trip.
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from cli_anything.qcad.backends.qcad_headless import QcadHeadless
from cli_anything.qcad.utils.region_render import RegionRenderer


def _emit(data: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        click.echo(json.dumps(data, ensure_ascii=False, default=str))


def _load_ops(path: str) -> List[Dict[str, Any]]:
    text = Path(path).read_text()
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("ops", [])
    if not isinstance(data, list):
        raise click.BadParameter("ops file must be a list of operations, or "
                                 "{'ops': [...]}")
    return data


@click.group()
@click.option("--json", "json_output", is_flag=True, default=True,
              help="Emit JSON (default).")
@click.option("--qcad", default=None, help="Path to the qcad binary.")
@click.pass_context
def cli(ctx, json_output, qcad):
    """DWG-native inspection and editing via QCAD's headless scripting engine."""
    ctx.ensure_object(dict)
    ctx.obj["qcad"] = qcad
    ctx.obj["json_output"] = json_output


@cli.command()
@click.argument("dwg_path")
@click.option("--x", type=float, required=True, help="Rect origin x in CAD units.")
@click.option("--y", type=float, required=True, help="Rect origin y in CAD units.")
@click.option("--w", type=float, required=True, help="Rect width in CAD units.")
@click.option("--h", type=float, required=True, help="Rect height in CAD units.")
@click.pass_context
def in_rect(ctx, dwg_path, x, y, w, h):
    """List the entities inside a CAD rectangle.

    This is the chat portal's "what did the user select" call: the browser drags a
    box on a rendered window and sends back CAD coordinates.
    """
    from cli_anything.qcad.backends.dwg_native import DwgNativeEngine
    e = DwgNativeEngine(qcad_bin=ctx.obj.get("qcad"))
    rect = {"x": x, "y": y, "w": w, "h": h}
    result = e.query_rect(dwg_path, rect)
    ents = result.get("entities", [])
    from cli_anything.qcad.utils.region_query import summarise
    _emit({"success": True, "rect": rect, "count": len(ents),
           "summary": summarise(ents), "entities": ents},
          ctx.obj["json_output"])


@cli.command()
@click.argument("dwg_path")
@click.option("--x", type=float, required=True, help="CAD x.")
@click.option("--y", type=float, required=True, help="CAD y.")
@click.option("--radius", "-r", type=float, default=1.0, help="Search radius.")
@click.pass_context
def at_point(ctx, dwg_path, x, y, radius):
    """List the entities near a CAD point, nearest first."""
    from cli_anything.qcad.backends.dwg_native import DwgNativeEngine
    e = DwgNativeEngine(qcad_bin=ctx.obj.get("qcad"))
    result = e.query_point(dwg_path, x, y, radius)
    ents = result.get("entities", [])
    from cli_anything.qcad.utils.region_query import summarise
    _emit({"success": True, "point": [x, y], "radius": radius, "count": len(ents),
           "summary": summarise(ents), "entities": ents},
          ctx.obj["json_output"])


@cli.command()
@click.argument("dwg_path")
@click.pass_context
def index(ctx, dwg_path):
    """Emit the entity index: handles, layers, text and CAD positions."""
    h = QcadHeadless(qcad_bin=ctx.obj.get("qcad"))
    result = h.index(dwg_path)
    if "error" in result:
        _emit({"success": False, **result}, ctx.obj["json_output"])
        sys.exit(1)
    rows = result.get("rows", [])
    _emit({
        "success": True,
        "source": dwg_path,
        "entities": result.get("entities"),
        "withText": result.get("withText"),
        "withPosition": result.get("withPosition"),
        "layers": result.get("layers"),
        "blocks": result.get("blocks"),
        "perLayer": result.get("perLayer"),
        "rows": rows,
    }, ctx.obj["json_output"])


@cli.command()
@click.argument("dwg_path")
@click.option("--text", "-t", required=True, help="Substring to find (case-insensitive).")
@click.option("--limit", default=50, help="Max results.")
@click.pass_context
def find(ctx, dwg_path, text, limit):
    """Find entities by their text."""
    h = QcadHeadless(qcad_bin=ctx.obj.get("qcad"))
    hits = h.find_text(dwg_path, text)[:limit]
    _emit({"success": True, "query": text, "count": len(hits), "hits": hits},
          ctx.obj["json_output"])


@cli.command()
@click.argument("dwg_path")
@click.option("--x", type=float, required=True, help="CAD x.")
@click.option("--y", type=float, required=True, help="CAD y.")
@click.option("--radius", "-r", type=float, default=1.0, help="Search radius in CAD units.")
@click.option("--limit", default=50, help="Max results.")
@click.pass_context
def near(ctx, dwg_path, x, y, radius, limit):
    """Find entities near a CAD point, nearest first."""
    h = QcadHeadless(qcad_bin=ctx.obj.get("qcad"))
    hits = h.find_near(dwg_path, x, y, radius)[:limit]
    _emit({"success": True, "point": [x, y], "radius": radius,
           "count": len(hits), "hits": hits}, ctx.obj["json_output"])


@cli.command()
@click.argument("dwg_path")
@click.option("--out", "-o", required=True, help="Output PNG path.")
@click.option("--x", type=float, required=True, help="Window origin x in CAD units.")
@click.option("--y", type=float, required=True, help="Window origin y in CAD units.")
@click.option("--w", type=float, required=True, help="Window width in CAD units.")
@click.option("--h", type=float, required=True, help="Window height in CAD units.")
@click.option("--px-w", type=int, default=1200, help="Output width in pixels.")
@click.option("--px-h", type=int, default=800, help="Output height in pixels.")
@click.pass_context
def render(ctx, dwg_path, out, x, y, w, h, px_w, px_h):
    """Render a CAD-coordinate window to PNG."""
    r = RegionRenderer(qcad_bin=ctx.obj.get("qcad"))
    result = r.render(dwg_path, out, x, y, w, h, px_w, px_h)
    if not result.get("success"):
        _emit({"success": False, **result}, ctx.obj["json_output"])
        sys.exit(1)
    _emit(result, ctx.obj["json_output"])


@cli.command()
@click.argument("dwg_path")
@click.option("--out", "-o", required=True, help="Output DWG path.")
@click.option("--ops", required=True, help="JSON file with the operation list.")
@click.pass_context
def apply(ctx, dwg_path, out, ops):
    """Apply operations to a DWG and write a new DWG.

    Operation types: set_text, delete, move, add_text, add_line, add_box.
    See the module docstring of backends/qcad_headless.py for the field names.
    """
    op_list = _load_ops(ops)
    h = QcadHeadless(qcad_bin=ctx.obj.get("qcad"))
    result = h.apply(dwg_path, out, op_list)
    if "error" in result:
        _emit({"success": False, **result}, ctx.obj["json_output"])
        sys.exit(1)
    audit = result.get("audit") or {}
    failed = audit.get("failed", [])
    _emit({
        "success": bool(result.get("success")),
        "source": dwg_path,
        "output": out,
        "audit_passed": audit.get("passed"),
        "ops_checked": audit.get("checked"),
        "ops_verified": audit.get("verified"),
        "failed": failed,
        "audit_results": audit.get("results"),
        "ops": result.get("ops"),
        "entities": result.get("entities"),
    }, ctx.obj["json_output"])
    if failed or not audit.get("passed", True):
        # The file was written, but an op did not land. Do not report success.
        sys.exit(2)


@cli.command()
@click.argument("before_path")
@click.argument("after_path")
@click.option("--radius", "-r", type=float, default=0.01,
              help="Coordinate tolerance when matching entities.")
@click.pass_context
def verify(ctx, before_path, after_path, radius):
    """Diff two drawings: what was added, removed or moved."""
    h = QcadHeadless(qcad_bin=ctx.obj.get("qcad"))
    b = h.index(before_path).get("rows", [])
    a = h.index(after_path).get("rows", [])

    def key(r):
        return (r.get("t") or "", r.get("layer") or "")

    def pos(r):
        return (r.get("x"), r.get("y"))

    bmap = {key(r): r for r in b}
    amap = {key(r): r for r in a}
    added = [amap[k] for k in amap if k not in bmap]
    removed = [bmap[k] for k in bmap if k not in amap]
    moved = []
    for k in amap:
        if k not in bmap:
            continue
        px, py = pos(amap[k])
        qx, qy = pos(bmap[k])
        if px is None or qx is None:
            continue
        if abs(px - qx) > radius or abs(py - qy) > radius:
            moved.append({"entity": amap[k], "from": [qx, qy], "to": [px, py]})
    _emit({
        "success": True,
        "before": {"entities": len(b)},
        "after": {"entities": len(a)},
        "added": added,
        "removed": removed,
        "moved": moved,
    }, ctx.obj["json_output"])


if __name__ == "__main__":
    cli()