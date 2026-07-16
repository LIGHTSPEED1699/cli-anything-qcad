#!/usr/bin/env python3
"""
Phase A Integration Test: tier_router + dxf_editor + visual_verifier

Creates a synthetic DXF with known entities, routes annotations through
Tier Router, executes edits via DXFEditor, then verifies with VisualVerifier.
"""

import sys
import json
import tempfile
from pathlib import Path

try:
    import ezdxf
except ImportError:
    print("ERROR: ezdxf not installed. Run: pip install ezdxf")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))

from tier_router import TierRouter, Tier
from dxf_editor import DXFEditor, EditResult
from visual_verifier import VisualVerifier


def create_test_dxf(output_path: str) -> dict:
    """Create a synthetic DXF with labeled entities for testing."""
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()

    # Add some text entities
    msp.add_text("Blu", dxfattribs={"insert": (10, 20), "height": 5, "layer": "labels"})
    msp.add_text("Grn", dxfattribs={"insert": (10, 30), "height": 5, "layer": "labels"})
    msp.add_text("Red", dxfattribs={"insert": (10, 40), "height": 5, "layer": "labels"})
    msp.add_text("NT111", dxfattribs={"insert": (50, 20), "height": 3, "layer": "part_numbers"})
    msp.add_text("old_label", dxfattribs={"insert": (50, 30), "height": 3, "layer": "labels"})

    # Add some lines
    msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "border"})
    msp.add_line((100, 0), (100, 100), dxfattribs={"layer": "border"})
    msp.add_line((0, 100), (100, 100), dxfattribs={"layer": "border"})
    msp.add_line((0, 0), (0, 100), dxfattribs={"layer": "border"})

    # Add a block definition and insert
    block = doc.blocks.new("BlockA")
    block.add_circle((0, 0), 10)
    msp.add_blockref("BlockA", insert=(80, 80), dxfattribs={"layer": "blocks"})

    doc.saveas(output_path)

    return {
        "file": output_path,
        "text_count": 5,
        "line_count": 4,
        "block_count": 1,
        "entities": ["Blu", "Grn", "Red", "NT111", "old_label", "BlockA"],
    }


def test_tier_router():
    """Test tier routing with known annotations."""
    print("\n" + "=" * 60)
    print("TEST 1: Tier Router")
    print("=" * 60)

    router = TierRouter(input_format="dxf")

    test_cases = [
        ("Change Blu to Wht", Tier.EZDXF),
        ("Replace NT111 with NT-110", Tier.QCAD_ECMA),
        ("Delete old_label", Tier.EZDXF),
        ("Move BlockA to (150, 200)", Tier.QCAD_ECMA),
        ("Rearrange the layout", Tier.VLM_X11),
        ("Color change to Red", Tier.EZDXF),
    ]

    passed = 0
    for text, expected_tier in test_cases:
        result = router.route(text)
        ok = result.tier == expected_tier
        status = "✓" if ok else "✗"
        print(f"  {status} '{text}' → {result.tier.value} (expected {expected_tier.value})")
        if not ok:
            print(f"     Got action={result.action_type}, target={result.target_name}")
        passed += ok

    print(f"\n  Result: {passed}/{len(test_cases)} passed")
    return passed == len(test_cases)


def test_dxf_editor():
    """Test DXF editing operations."""
    print("\n" + "=" * 60)
    print("TEST 2: DXF Editor")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path = Path(tmpdir) / "test.dxf"
        create_test_dxf(str(dxf_path))

        editor = DXFEditor(str(dxf_path))
        if not editor.load():
            print("  ✗ Failed to load DXF")
            return False

        passed = 0
        total = 0

        # Test 2a: Replace text
        total += 1
        result = editor.replace_text("Blu", "Wht")
        if result.success:
            print("  ✓ Replace 'Blu' → 'Wht' succeeded")
            passed += 1
        else:
            print(f"  ✗ Replace failed: {result.error}")

        # Test 2b: Replace non-existent text
        total += 1
        result = editor.replace_text("NonExistent", "X")
        if not result.success:
            print("  ✓ Non-existent entity correctly rejected")
            passed += 1
        else:
            print("  ✗ Should have failed for non-existent entity")

        # Test 2c: Delete entity
        total += 1
        result = editor.delete_entity("old_label")
        if result.success:
            print("  ✓ Delete 'old_label' succeeded")
            passed += 1
        else:
            print(f"  ✗ Delete failed: {result.error}")

        # Save and verify
        modified_path = Path(tmpdir) / "test_modified.dxf"
        editor.doc.saveas(str(modified_path))

        # Re-load and verify changes
        doc2 = ezdxf.readfile(str(modified_path))
        msp2 = doc2.modelspace()
        texts = [e.dxf.text for e in msp2 if e.dxftype() == "TEXT"]

        total += 1
        if "Wht" in texts and "Blu" not in texts:
            print("  ✓ Verification: 'Wht' present, 'Blu' absent")
            passed += 1
        else:
            print(f"  ✗ Verification failed. Texts: {texts}")

        total += 1
        if "old_label" not in texts:
            print("  ✓ Verification: 'old_label' correctly deleted")
            passed += 1
        else:
            print(f"  ✗ 'old_label' still present")

        print(f"\n  Result: {passed}/{total} passed")
        return passed == total


def test_visual_verifier():
    """Test visual verifier dry-run (no actual DXF/DWG file)."""
    print("\n" + "=" * 60)
    print("TEST 3: Visual Verifier (dry-run)")
    print("=" * 60)

    verifier = VisualVerifier(renderer="auto", dpi=150)
    print(f"  Available tools: {verifier.tools}")

    # Test tool detection
    has_renderer = any(verifier.tools.values())
    if has_renderer:
        print("  ✓ At least one rendering tool available")
    else:
        print("  ⚠ No rendering tools found (LibreCAD/QCAD/ImageMagick)")

    return has_renderer


def test_end_to_end():
    """End-to-end: create DXF → route → edit → verify pipeline."""
    print("\n" + "=" * 60)
    print("TEST 4: End-to-End Pipeline (dry)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Step 1: Create test DXF
        dxf_path = Path(tmpdir) / "panel.dxf"
        info = create_test_dxf(str(dxf_path))
        print(f"  Created test DXF: {dxf_path}")
        print(f"    Entities: {info['entities']}")

        # Step 2: Route annotations
        router = TierRouter(input_format="dxf")
        annotations = [
            {"text": "Change Blu to Wht", "type": "FreeText"},
            {"text": "Delete old_label", "type": "FreeText"},
        ]
        routes = router.batch_route(annotations)
        summary = router.summarize_routing(routes)
        print(f"  Routing summary: {json.dumps(summary, indent=2)}")

        # Step 3: Execute T1 edits
        editor = DXFEditor(str(dxf_path))
        editor.load()

        edit_results = []
        for route in routes:
            if route.tier == Tier.EZDXF:
                if route.action_type == "change_text":
                    result = editor.replace_text(route.target_name, route.replacement_name)
                    edit_results.append(result)
                    print(f"  Edit: {route.target_name} → {route.replacement_name}: {'✓' if result.success else '✗'}")
                elif route.action_type == "delete_entity":
                    result = editor.delete_entity(route.target_name)
                    edit_results.append(result)
                    print(f"  Delete: {route.target_name}: {'✓' if result.success else '✗'}")

        # Step 4: Save modified
        modified_path = Path(tmpdir) / "panel_modified.dxf"
        editor.doc.saveas(str(modified_path))
        print(f"  Saved modified DXF: {modified_path}")

        # Step 5: Verify (if tools available)
        verifier = VisualVerifier(renderer="auto")
        if any(verifier.tools.values()):
            print(f"  Running visual verification...")
            result = verifier.verify(
                str(dxf_path), str(modified_path),
                expected_change_desc="Change Blu to Wht and delete old_label"
            )
            print(f"    Status: {result.status}")
            print(f"    Pixel change: {result.pixel_change_pct*100:.2f}%")
            print(f"    Renderer: {result.renderer_used}")
            if result.error:
                print(f"    Error: {result.error}")
        else:
            print("  ⚠ Skipping visual verification (no rendering tools)")

        # Check edit results
        all_ok = all(r.success for r in edit_results)
        print(f"\n  Result: {'✓ PASSED' if all_ok else '✗ FAILED'}")
        return all_ok


def main():
    print("=" * 60)
    print("Phase A Integration Tests")
    print("=" * 60)

    results = {
        "tier_router": test_tier_router(),
        "dxf_editor": test_dxf_editor(),
        "visual_verifier": test_visual_verifier(),
        "end_to_end": test_end_to_end(),
    }

    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    for name, passed in results.items():
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"  {status}: {name}")

    total_passed = sum(results.values())
    total_tests = len(results)
    print(f"\n  Overall: {total_passed}/{total_tests} test suites passed")

    return total_passed == total_tests


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
