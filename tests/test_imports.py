#!/usr/bin/env python3
"""Smoke test for cli-anything-qcad package imports."""


def test_imports():
    from cli_anything.qcad.core.categories import classify, CATEGORIES
    from cli_anything.qcad.core.session import JobSession
    from cli_anything.qcad.pipelines.markup_pipeline import MarkupPipeline
    from cli_anything.qcad.backends.dwg_converter import DwgConverter
    from cli_anything.qcad.backends.ezdxf_backend import EzdxfBackend
    from cli_anything.qcad.backends.qcad_ecma_backend import QcadEcmaBackend
    from cli_anything.qcad.backends.vlm_x11_backend import VlmX11Backend
    from cli_anything.qcad.utils.visual_verify import VisualVerifier
    from cli_anything.qcad.qcad_cli import cli

    assert "text_change" in CATEGORIES
    assert classify("replace NT111 with NT112").name == "text_change"
    print("imports OK")


if __name__ == "__main__":
    test_imports()
