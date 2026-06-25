"""CLI entry point for cli-anything-qcad."""
import json
import sys
from pathlib import Path

import click

from cli_anything.qcad.core.session import JobSession
from cli_anything.qcad.pipelines.markup_pipeline import MarkupPipeline
from cli_anything.qcad.utils.pdf_parser import PdfAnnotationParser
from cli_anything.qcad.backends.dwg_converter import DwgConverter
from cli_anything.qcad.utils.visual_verify import VisualVerifier


@click.group(invoke_without_command=True)
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--qcad", default=None, help="Path to QCAD binary.")
@click.option("--oda", default=None, help="Path to ODAFileConverter binary.")
@click.option("--overrides", default=None, help="JSON file with handle overrides for cloud deletion.")
@click.pass_context
def cli(ctx, json_output, qcad, oda, overrides):
    """CLI-Anything harness for QCAD: PDF markup → verified DWG."""
    ctx.ensure_object(dict)
    ctx.obj["json_output"] = json_output
    ctx.obj["qcad"] = qcad
    ctx.obj["oda"] = oda
    ctx.obj["overrides"] = overrides
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@click.argument("dwg_path")
@click.argument("pdf_path")
@click.option("--output", "-o", default=None, help="Output DWG path.")
@click.option("--dry-run", is_flag=True, help="Plan only, do not execute edits.")
@click.pass_context
def apply(ctx, dwg_path, pdf_path, output, dry_run):
    """Apply PDF markups to a DWG file and verify the result."""
    converter = DwgConverter(qcad_bin=ctx.obj.get("qcad"), oda_converter=ctx.obj.get("oda"))
    overrides = None
    if ctx.obj.get("overrides"):
        with open(ctx.obj["overrides"]) as f:
            overrides = json.load(f)
    pipeline = MarkupPipeline(
        pdf_parser=PdfAnnotationParser(),
        converter=converter,
        verifier=VisualVerifier(),
        qcad_bin=ctx.obj.get("qcad"),
    )
    job = pipeline.run_with_pdf(dwg_path, pdf_path, output_dwg=output, overrides=overrides)
    _emit(ctx, job.to_dict())


@cli.command()
@click.argument("dwg_path")
@click.argument("dxf_path")
@click.pass_context
def dwg2dxf(ctx, dwg_path, dxf_path):
    """Convert DWG to DXF."""
    converter = DwgConverter(qcad_bin=ctx.obj.get("qcad"), oda_converter=ctx.obj.get("oda"))
    success = converter.dwg_to_dxf(dwg_path, dxf_path)
    _emit(ctx, {"success": success, "dxf": dxf_path})


@cli.command()
@click.argument("dxf_path")
@click.argument("dwg_path")
@click.pass_context
def dxf2dwg(ctx, dxf_path, dwg_path):
    """Convert DXF to DWG."""
    converter = DwgConverter(qcad_bin=ctx.obj.get("qcad"), oda_converter=ctx.obj.get("oda"))
    success = converter.dxf_to_dwg(dxf_path, dwg_path)
    _emit(ctx, {"success": success, "dwg": dwg_path})


@cli.command()
@click.argument("pdf_path")
@click.pass_context
def parse(ctx, pdf_path):
    """Extract actionable annotations from a PDF markup file."""
    parser = PdfAnnotationParser()
    annotations = parser.parse(pdf_path)
    _emit(ctx, {"annotations": annotations})


@cli.command()
@click.argument("dwg_path")
@click.option("--out", "-o", required=True, help="Output PNG path.")
@click.pass_context
def render(ctx, dwg_path, out):
    """Render a DWG/DXF to PNG."""
    verifier = VisualVerifier()
    success = verifier.render(dwg_path, out)
    _emit(ctx, {"success": success, "png": out})


def _emit(ctx, data):
    if ctx.obj.get("json_output"):
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for k, v in data.items():
            click.echo(f"{k}: {v}")


def entrypoint(argv=None):
    return cli.main(args=argv, prog_name="cli-anything-qcad", standalone_mode=False)


if __name__ == "__main__":
    sys.exit(entrypoint())
