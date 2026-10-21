#!/usr/bin/env python3
"""Test the apply path with the audit: real ops must verify, a broken op must fail."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DRAWING = os.environ.get("DWG_TEST_FILE", "")
QCAD = os.environ.get("QCAD_BIN", str(Path.home() / "opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad"))


def test_audit_pure():
    """The audit rules are pure logic - no QCAD needed."""
    from cli_anything.qcad.backends.apply_audit import audit_ops
    before = [{"h": 1, "layer": "L", "t": "OLD", "x": 5.0, "y": 5.0},
              {"h": 2, "layer": "L", "x": 1.0, "y": 1.0}]
    after = [{"h": 2, "layer": "L", "x": 1.0, "y": 1.0},
             {"h": 3, "layer": "E-TEXT", "t": "NEW", "x": 26.0, "y": 12.5}]

    r = audit_ops([{"op": "delete", "handle": 1}], before, after)
    assert r["passed"], r

    r = audit_ops([{"op": "set_text", "handle": 2, "text": "NEW"}], before, after)
    assert not r["passed"], r
    assert "expected" in r["failed"][0]["detail"], r

    r = audit_ops([{"op": "add_text", "text": "NEW", "x": 26.0, "y": 12.5}], before, after)
    assert r["passed"], r

    # wrong position must fail
    r = audit_ops([{"op": "add_text", "text": "NEW", "x": 99.0, "y": 99.0}], before, after)
    assert not r["passed"], r

    # a delete that did not happen must fail
    r = audit_ops([{"op": "delete", "handle": 2}], before, after)
    assert not r["passed"], r
    assert "still present" in r["failed"][0]["detail"], r

    # move: 1,1 + (2,3) = (3,4)
    r = audit_ops([{"op": "move", "handle": 2, "dx": 2.0, "dy": 3.0}], before,
                  [{"h": 2, "layer": "L", "x": 3.0, "y": 4.0}])
    assert r["passed"], r
    r = audit_ops([{"op": "move", "handle": 2, "dx": 2.0, "dy": 3.0}], before, after)
    assert not r["passed"], r
    print("audit rules OK")


def test_apply_verified():
    if not DRAWING or not Path(DRAWING).exists():
        print("apply SKIPPED (no DWG_TEST_FILE)")
        return
    from cli_anything.qcad.backends.qcad_headless import QcadHeadless
    h = QcadHeadless(qcad_bin=QCAD)
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "out.dwg")
        # find a real text entity to edit and delete
        idx = h.index(DRAWING)
        victim = next((r for r in idx["rows"] if r.get("t") == "PIPELINE AREA"), None)
        assert victim, "PIPELINE AREA not found in the test drawing"

        ops = [
            {"op": "set_text", "handle": victim["h"], "text": "AUDITED TEXT"},
            {"op": "add_text", "text": "AUDITNOTE", "x": 26.0, "y": 12.5,
             "height": 1.0, "layer": "E-TEXT"},
            {"op": "add_box", "x": 26.0, "y": 8.0, "w": 4.0, "h": 4.0,
             "color": [255, 0, 0]},
        ]
        res = h.apply(DRAWING, out, ops)
        assert "error" not in res, res
        assert res.get("success") is True, res
        audit = res.get("audit") or {}
        assert audit.get("passed") is True, audit
        assert audit.get("verified") == audit.get("checked"), audit
        for r in audit["results"]:
            assert r["verified"], r
        # the text must really be in the file
        hits = h.find_text(out, "AUDITED TEXT")
        assert hits, "AUDITED TEXT not in the output"
        assert hits[0]["h"] == victim["h"], hits
    print("apply verified OK")


def test_apply_delete_actually_deletes():
    if not DRAWING or not Path(DRAWING).exists():
        print("delete SKIPPED (no DWG_TEST_FILE)")
        return
    from cli_anything.qcad.backends.qcad_headless import QcadHeadless
    h = QcadHeadless(qcad_bin=QCAD)
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "out.dwg")
        idx = h.index(DRAWING)
        victim = next((r for r in idx["rows"] if r.get("t") == "PIPELINE AREA"), None)
        assert victim, "PIPELINE AREA not found"

        res = h.apply(DRAWING, out, [{"op": "delete", "handle": victim["h"]}])
        assert "error" not in res, res
        assert res.get("success") is True, res
        assert not h.find_text(out, "PIPELINE AREA"), "the text was not deleted"
    print("apply delete OK")


def test_apply_audit_catches_failure():
    """A delete of a handle that does not exist must not report success."""
    if not DRAWING or not Path(DRAWING).exists():
        print("audit-failure SKIPPED (no DWG_TEST_FILE)")
        return
    from cli_anything.qcad.backends.qcad_headless import QcadHeadless
    h = QcadHeadless(qcad_bin=QCAD)
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "out.dwg")
        res = h.apply(DRAWING, out, [{"op": "delete", "handle": 999999999}])
        assert "error" not in res, res
        assert res.get("success") is False, res
        assert res["audit"]["failed"], res["audit"]
    print("audit catches failure OK")


def test_add_text_without_height_keeps_text():
    """The bug that broke T3: a text added without a height exported empty."""
    if not DRAWING or not Path(DRAWING).exists():
        print("height SKIPPED (no DWG_TEST_FILE)")
        return
    from cli_anything.qcad.backends.qcad_headless import QcadHeadless
    h = QcadHeadless(qcad_bin=QCAD)
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "out.dwg")
        res = h.apply(DRAWING, out,
                      [{"op": "add_text", "text": "NOHEIGHTTEST",
                        "x": 26.0, "y": 12.5, "layer": "E-TEXT"}])
        assert res.get("success") is True, res
        hits = h.find_text(out, "NOHEIGHTTEST")
        assert hits, "text without a height did not survive the export"
        assert abs(hits[0]["x"] - 26.0) < 0.01, hits
    print("add_text without height OK")


if __name__ == "__main__":
    test_audit_pure()
    test_apply_verified()
    test_apply_delete_actually_deletes()
    test_apply_audit_catches_failure()
    test_add_text_without_height_keeps_text()
    print("ALL OK")
