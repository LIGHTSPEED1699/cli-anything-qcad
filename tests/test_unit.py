#!/usr/bin/env python3
"""Test PDF annotation parsing and layer visibility fix without needing DWG files."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli_anything.qcad.utils.pdf_parser import PdfAnnotationParser
from cli_anything.qcad.utils.layer_fix import fix_layer_visibility
from cli_anything.qcad.core.categories import classify, CATEGORIES


def test_classify():
    assert classify("Replace NT111 with NT112").name == "text_change"
    assert classify("Move this to row 2").name == "move"
    assert classify("Delete F174").name == "delete"
    assert "text_change" in CATEGORIES
    print("classify OK")


def test_layer_fix():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.dxf"
        out = Path(tmp) / "output.dxf"
        src.write_text(
            "0\nTABLE\n2\nLAYER\n0\nLAYER\n2\n0\n62\n-7\n0\nENDTAB\n0\nENDSEC\n"
        )
        modified = fix_layer_visibility(str(src), str(out))
        assert modified == 1
        assert "7" in out.read_text()
        assert "-7" not in out.read_text()
    print("layer_fix OK")


def test_pdf_parser_requires_pymupdf():
    parser = PdfAnnotationParser()
    assert hasattr(parser, "parse")
    print("pdf_parser import OK")


if __name__ == "__main__":
    test_classify()
    test_layer_fix()
    test_pdf_parser_requires_pymupdf()
    print("all tests passed")
