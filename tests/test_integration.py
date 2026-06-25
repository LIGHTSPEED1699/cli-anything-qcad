#!/usr/bin/env python3
"""Test QCAD CLI commands in a synthetic environment."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_cli_parse_help():
    from cli_anything.qcad.qcad_cli import entrypoint
    result = entrypoint(["--help"])
    assert result in (None, 0)
    print("cli help OK")


def test_categories_json():
    from cli_anything.qcad.core.categories import CATEGORIES
    assert "delete" in CATEGORIES
    assert "clone" in CATEGORIES
    print("categories OK")


def test_dwg_converter_init():
    from cli_anything.qcad.backends.dwg_converter import DwgConverter
    c = DwgConverter()
    assert c.qcad_bin or c.oda_converter
    print("converter init OK")


def test_visual_verify_compare():
    from cli_anything.qcad.utils.visual_verify import VisualVerifier, VerificationResult
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmpdir:
        a = str(Path(tmpdir) / "a.png")
        b = str(Path(tmpdir) / "b.png")
        Image.new("RGB", (100, 100), color="white").save(a)
        Image.new("RGB", (100, 100), color="black").save(b)
        v = VisualVerifier()
        r = v.compare(a, b, [])
        assert isinstance(r, VerificationResult)
        assert r.status == "PASSED"
    print("visual verify compare OK")


def test_renderer_init():
    from cli_anything.qcad.utils.render import QcadRenderer
    r = QcadRenderer()
    assert r._find_qcad() is not None
    print("renderer init OK")


if __name__ == "__main__":
    test_cli_parse_help()
    test_categories_json()
    test_dwg_converter_init()
    test_visual_verify_compare()
    test_renderer_init()
    print("all integration tests passed")
