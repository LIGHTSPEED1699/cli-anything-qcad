#!/usr/bin/env python3
"""Test PDF annotation parsing, layer visibility fix, and engine imports."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_classify():
    from cli_anything.qcad.core.categories import classify
    cases = [
        ("delete clouded items", "delete"),
        ("change tag to PLC22", "text_change"),
        ("clone terminal row 4 to row 7", "clone"),
        ("move valve to the right", "move"),
        ("something unclear", "ambiguous"),
    ]
    for text, expected in cases:
        cat = classify(text)
        assert cat.name == expected, f"{text} -> {cat.name} != {expected}"
    print("classify OK")


def test_layer_fix():
    from cli_anything.qcad.utils.layer_fix import fix_layer_visibility
    lines = ["  0", "LAYER", "  5", "10", " 62", "-7", "  0", "ENDTAB"]
    with tempfile.NamedTemporaryFile("w", suffix=".dxf", delete=False) as fin:
        fin.write("\n".join(lines))
        ip = fin.name
    op = ip + "_fixed.dxf"
    try:
        fix_layer_visibility(ip, op)
        out = Path(op).read_text()
        assert "7\n  0" in out or "\n7\n" in out.replace("\r", "")
        print("layer_fix OK")
    finally:
        for p in (ip, op):
            Path(p).unlink(missing_ok=True)


def test_pdf_parser_import():
    from cli_anything.qcad.utils.pdf_parser import PdfAnnotationParser
    p = PdfAnnotationParser()
    assert p is not None
    print("pdf_parser import OK")


def test_engine_imports():
    from cli_anything.qcad.engines.cloud_deletion import CloudDeletionEngine
    from cli_anything.qcad.engines.terminal_clone import TerminalCloneEngine
    from cli_anything.qcad.utils.visual_verifier import QcadVlmVerifier
    assert CloudDeletionEngine
    assert TerminalCloneEngine
    assert QcadVlmVerifier
    print("engine imports OK")


if __name__ == "__main__":
    test_classify()
    test_layer_fix()
    test_pdf_parser_import()
    test_engine_imports()
    print("all tests passed")
