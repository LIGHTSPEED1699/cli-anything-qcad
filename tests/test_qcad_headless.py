#!/usr/bin/env python3
"""Test the DWG-native headless backend and the CAD-space region renderer.

Runs against a real drawing if one is available; skips the live checks otherwise.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DRAWING = os.environ.get("DWG_TEST_FILE", "")
QCAD = os.environ.get("QCAD_BIN", str(Path.home() / "opt/qcad-3.32.7-pro-linux-qt6-x86_64/qcad"))


def test_pure_helpers():
    """Coordinate mapping and ops loading need no QCAD."""
    from cli_anything.qcad.utils.region_render import RegionRenderer
    from cli_anything.qcad.dwg_cli import _load_ops

    window = {"x": 24.0, "y": 6.0, "w": 12.0, "h": 10.0}
    pixels = {"w": 1200, "h": 1000}

    # CAD -> pixel
    px, py = RegionRenderer.cad_to_pixel(24.0, 6.0, window, pixels)
    assert abs(px) < 1e-6 and abs(py - 1000) < 1e-6, (px, py)
    px, py = RegionRenderer.cad_to_pixel(36.0, 16.0, window, pixels)
    assert abs(px - 1200) < 1e-6 and abs(py) < 1e-6, (px, py)

    # pixel -> CAD is the inverse
    for cx, cy in ((24.0, 6.0), (30.0, 11.0), (36.0, 16.0)):
        p = RegionRenderer.cad_to_pixel(cx, cy, window, pixels)
        back = RegionRenderer.pixel_to_cad(p[0], p[1], window, pixels)
        assert abs(back[0] - cx) < 1e-6 and abs(back[1] - cy) < 1e-6, (cx, cy, back)

    # ops file shapes
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ops.json"
        p.write_text(json.dumps([{"op": "set_text", "handle": 1, "text": "x"}]))
        assert _load_ops(str(p))[0]["op"] == "set_text"
        p.write_text(json.dumps({"ops": [{"op": "delete", "handle": 2}]}))
        assert _load_ops(str(p))[0]["op"] == "delete"
    print("pure helpers OK")


def test_index_and_find():
    if not DRAWING or not Path(DRAWING).exists():
        print("index/find SKIPPED (no DWG_TEST_FILE)")
        return
    from cli_anything.qcad.backends.qcad_headless import QcadHeadless
    h = QcadHeadless(qcad_bin=QCAD)
    idx = h.index(DRAWING)
    assert "error" not in idx, idx
    assert idx["entities"] > 0, idx
    assert len(idx["rows"]) == idx["entities"], (len(idx["rows"]), idx["entities"])

    # every row must have a handle and a layer
    for r in idx["rows"][:50]:
        assert "h" in r and "layer" in r, r

    # find_text returns only real rows
    texts = [r for r in idx["rows"] if r.get("t")]
    if texts:
        needle = texts[0]["t"][:4]
        hits = h.find_text(DRAWING, needle)
        assert hits, f"no hits for {needle!r}"
        assert all("t" in x for x in hits)

    # find_near must include the entity that is exactly there
    with_pos = [r for r in idx["rows"] if r.get("x") is not None]
    if with_pos:
        target = with_pos[0]
        hits = h.find_near(DRAWING, target["x"], target["y"], 0.5)
        assert hits, "expected a hit at the entity's own position"
        assert hits[0]["dist"] == 0.0, hits[0]
    print(f"index/find OK ({idx['entities']} entities)")


def test_apply_roundtrip():
    if not DRAWING or not Path(DRAWING).exists():
        print("apply SKIPPED (no DWG_TEST_FILE)")
        return
    from cli_anything.qcad.backends.qcad_headless import QcadHeadless
    h = QcadHeadless(qcad_bin=QCAD)
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "out.dwg")
        ops = [
            {"op": "add_text", "text": "TESTNOTE", "x": 26.0, "y": 12.5,
             "height": 1.0, "layer": "E-TEXT"},
            {"op": "add_box", "x": 26.0, "y": 8.0, "w": 4.0, "h": 4.0,
             "color": [255, 0, 0]},
            {"op": "set_text", "handle": 999999999, "text": "NOPE"},
        ]
        res = h.apply(DRAWING, out, ops)
        assert "error" not in res, res
        assert Path(out).exists(), "no output written"
        reports = res["ops"]
        assert reports[0]["ok"], reports[0]
        assert reports[1]["ok"], reports[1]
        # a bogus handle must fail cleanly, not crash
        assert not reports[2]["ok"] and "not found" in reports[2].get("error", ""), reports[2]

        # the note must be findable in the written file, at the right place
        hits = h.find_text(out, "TESTNOTE")
        assert hits, "TESTNOTE not found in the output DWG"
        got = hits[0]
        assert abs(got["x"] - 26.0) < 0.01 and abs(got["y"] - 12.5) < 0.01, got
    print("apply roundtrip OK")


def test_region_render():
    if not DRAWING or not Path(DRAWING).exists():
        print("render SKIPPED (no DWG_TEST_FILE)")
        return
    from cli_anything.qcad.utils.region_render import RegionRenderer
    r = RegionRenderer(qcad_bin=QCAD)
    with tempfile.TemporaryDirectory() as d:
        png = str(Path(d) / "r.png")
        res = r.render(DRAWING, png, 24.0, 6.0, 12.0, 10.0, 600, 500)
        assert res.get("success"), res
        assert Path(png).stat().st_size > 2000, "render looks degenerate"
    print("region render OK")


def test_region_query():
    """Region resolution is pure geometry - no QCAD needed."""
    from cli_anything.qcad.utils.region_query import (entities_in_rect,
                                                     entities_near_point, summarise)
    rows = [
        {"h": 1, "layer": "E-TEXT", "t": "RACK", "x": 8.1, "y": 12.3},
        {"h": 2, "layer": "e-symb", "x1": 9.8, "y1": 13.5, "x2": 9.8, "y2": 13.5},
        {"h": 3, "layer": "E-TEXT", "t": "FAR", "x": 30.0, "y": 1.3},
    ]
    inside = entities_in_rect(rows, {"x": 7, "y": 11, "w": 4, "h": 3})
    assert {r["h"] for r in inside} == {1, 2}, inside
    assert {r["h"] for r in entities_in_rect(rows, {"x": 0, "y": 0, "w": 1, "h": 1})} == set()

    near = entities_near_point(rows, 8.1, 12.3, 0.05)
    assert near and near[0]["h"] == 1, near
    assert near[0]["dist"] == 0.0, near

    s = summarise(rows)
    assert s["count"] == 3 and s["text_count"] == 2, s
    print("region query OK")


if __name__ == "__main__":
    test_pure_helpers()
    test_region_query()
    test_index_and_find()
    test_apply_roundtrip()
    test_region_render()
    print("ALL OK")
