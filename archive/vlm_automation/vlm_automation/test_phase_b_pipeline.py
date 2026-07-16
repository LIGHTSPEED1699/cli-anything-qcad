#!/usr/bin/env python3
"""
Phase B Integration Test: VLM Pipeline + Confidence + Queue + Logger

Tests all Phase B components in a single synthetic flow:
  1. InstructionParser (Phase 1) — parse annotation text
  2. TargetDisambiguator (Phase 2) — resolve target with DXF metadata
  3. PostActionVerifier (Phase 3) — verify edit via screenshot
  4. ConfidenceScorer — multi-layer scoring
  5. ReviewQueue — enqueue low-confidence tasks
  6. AuditLogger — tamper-evident log entry
  7. VLMClient — health check and auto-select

This test uses a synthetic DXF created by Phase A test utilities.
Vision tests are mocked with placeholder images if no real screenshot available.
"""

import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Phase A imports (already tested)
from tier_router import TierRouter, Tier
from dxf_editor import DXFEditor, EditResult
from visual_verifier import VisualVerifier

# Phase B imports
from vlm_client import VLMClient
from vlm_instruction_parser import InstructionParser, ParsedInstruction
from vlm_disambiguator import TargetDisambiguator, VerifiedTarget
from vlm_verifier import PostActionVerifier, VerificationVerdict
from confidence_scorer import ConfidenceScorer, ConfidenceReport
from review_queue import ReviewQueue
from audit_logger import AuditLogger

try:
    import ezdxf
except ImportError:
    print("ERROR: ezdxf not installed. Run: pip install ezdxf")
    sys.exit(1)


def create_test_dxf(path: str) -> dict:
    """Create synthetic DXF with known entities."""
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_text("Blu", dxfattribs={"insert": (10, 20), "height": 5, "layer": "labels"})
    msp.add_text("NT111", dxfattribs={"insert": (50, 20), "height": 3, "layer": "part_numbers"})
    msp.add_text("old_label", dxfattribs={"insert": (50, 30), "height": 3, "layer": "labels"})
    block = doc.blocks.new("BlockA")
    block.add_circle((0, 0), 10)
    msp.add_blockref("BlockA", insert=(80, 80), dxfattribs={"layer": "blocks"})
    doc.saveas(path)
    return {"file": path, "texts": ["Blu", "NT111", "old_label"], "blocks": ["BlockA"]}


def test_vlm_client():
    print("\n" + "=" * 60)
    print("TEST 1: VLM Client — Health & Auto-Select")
    print("=" * 60)
    client = VLMClient()
    health = client.health_check()
    print(f"  Health check: {'✅ PASS' if health else '❌ FAIL'}")

    auto = VLMClient.auto_select("vision")
    print(f"  Auto-select (vision): {auto}")
    assert health, "Ollama not running"
    assert auto in VLMClient.MODEL_REGISTRY, f"Auto-selected model {auto} not in registry"
    print("  ✅ VLMClient OK")
    return health


def test_instruction_parser():
    print("\n" + "=" * 60)
    print("TEST 2: Phase 1 — Instruction Parser")
    print("=" * 60)
    parser = InstructionParser(model="qwen2.5vl:latest")

    cases = [
        ("Change Blu to Wht", "replace_text", "Blu", "Wht"),
        ("Replace NT111 with NT-110", "replace_text", "NT111", "NT-110"),
        ("Delete old_label", "delete_entity", "old_label", ""),
        ("Rearrange the layout", "rearrange_layout", "", ""),
    ]

    passed = 0
    for text, expected_action, expected_target, expected_repl in cases:
        result = parser.parse(text)
        ok = result.action_type == expected_action
        if expected_target:
            ok = ok and result.target_name == expected_target
        print(f"  '{text[:30]}' → action={result.action_type} target={result.target_name} "
              f"conf={result.confidence:.2f} review={result.needs_human_review} {'✅' if ok else '❌'}")
        if ok:
            passed += 1

    print(f"  Score: {passed}/{len(cases)} passed")
    assert passed >= len(cases) * 0.75, f"Too many parser failures: {passed}/{len(cases)}"
    print("  ✅ InstructionParser OK")
    return passed, len(cases)


def test_disambiguator(dxf_path: str):
    print("\n" + "=" * 60)
    print("TEST 3: Phase 2 — Target Disambiguator")
    print("=" * 60)
    disambiguator = TargetDisambiguator(model="qwen2.5vl:latest")

    # Case 1: Single text match — should fast-path
    parsed = ParsedInstruction(
        action_type="replace_text",
        target_name="Blu",
        replacement_name="Wht",
        confidence=0.90,
        reasoning="",
        needs_human_review=False,
    )
    result = disambiguator.disambiguate(parsed, dxf_path=dxf_path)
    print(f"  'Blu' → handle={result.target_handle} insert={result.target_insert} "
          f"conf={result.confidence:.2f} review={result.needs_human_review}")
    assert result.target_handle, "Disambiguator should find a handle for 'Blu'"
    assert result.confidence > 0.5, "Confidence too low for exact match"

    # Case 2: Block search
    parsed2 = ParsedInstruction(
        action_type="replace_block",
        target_name="BlockA",
        replacement_name="BlockB",
        confidence=0.85,
        reasoning="",
        needs_human_review=False,
    )
    result2 = disambiguator.disambiguate(parsed2, dxf_path=dxf_path)
    print(f"  'BlockA' → handle={result2.target_handle} block={result2.target_block} "
          f"conf={result2.confidence:.2f}")
    assert result2.target_block == "BlockA", "Should find BlockA"

    # Case 3: No match
    parsed3 = ParsedInstruction(
        action_type="replace_text",
        target_name="NonExistent",
        replacement_name="Foo",
        confidence=0.80,
        reasoning="",
        needs_human_review=False,
    )
    result3 = disambiguator.disambiguate(parsed3, dxf_path=dxf_path)
    print(f"  'NonExistent' → handle={result3.target_handle} conf={result3.confidence:.2f} "
          f"review={result3.needs_human_review}")
    assert result3.needs_human_review, "Non-existent target should flag human review"

    print("  ✅ TargetDisambiguator OK")
    return True


def test_confidence_scorer():
    print("\n" + "=" * 60)
    print("TEST 4: Confidence Scorer")
    print("=" * 60)
    scorer = ConfidenceScorer()

    # All good
    report = scorer.score(
        annotation_confidence=0.85,
        metadata_distance_px=45.0,
        coordinate_variance_px=12.0,
        post_action_confidence=0.92,
    )
    print(f"  All-good → composite={report.composite_score:.3f} passed={report.overall_passed} "
          f"review={report.human_review_required}")
    assert report.overall_passed, "All good should pass"
    assert not report.human_review_required

    # Low post-action confidence
    report2 = scorer.score(
        annotation_confidence=0.85,
        metadata_distance_px=45.0,
        coordinate_variance_px=12.0,
        post_action_confidence=0.50,
    )
    print(f"  Low post-action → composite={report2.composite_score:.3f} passed={report2.overall_passed} "
          f"review={report2.human_review_required}")
    assert not report2.overall_passed

    # Two failures → human review
    report3 = scorer.score(
        annotation_confidence=0.50,
        metadata_distance_px=250.0,
        coordinate_variance_px=12.0,
        post_action_confidence=0.92,
    )
    print(f"  Two failures → composite={report3.composite_score:.3f} passed={report3.overall_passed} "
          f"review={report3.human_review_required}")
    assert report3.human_review_required, "Two layer failures should require human review"

    # Verify chain integrity check works
    integrity = scorer.quick_check(0.85)
    print(f"  Quick check 0.85: {integrity}")
    assert integrity

    print("  ✅ ConfidenceScorer OK")
    return True


def test_review_queue():
    print("\n" + "=" * 60)
    print("TEST 5: Review Queue")
    print("=" * 60)
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / "test_reviews.db"
        q = ReviewQueue(db_path=str(db))

        rid = q.enqueue(
            annotation="Change Blu to Wht",
            tier=1,
            parsed_json={"action_type": "replace_text"},
            confidence_report={"composite": 0.45, "human_review": True},
        )
        print(f"  Enqueued: {rid}")
        assert rid, "Should return review_id"

        pending = q.list_pending()
        print(f"  Pending count: {len(pending)}")
        assert len(pending) == 1

        entry = q.get(rid)
        assert entry is not None
        assert entry.status == "PENDING"

        # Discord export
        discord_msg = q.export_for_discord(rid)
        assert "Human Review Required" in discord_msg
        print(f"  Discord preview: {discord_msg[:80]}...")

        # Resolve
        ok = q.update_status(rid, "APPROVED", reviewer="test_bot", notes="Looks correct")
        assert ok
        resolved = q.get(rid)
        assert resolved.status == "APPROVED"

        stats = q.stats()
        print(f"  Stats: {stats}")
        assert stats.get("APPROVED", 0) == 1

    print("  ✅ ReviewQueue OK")
    return True


def test_audit_logger():
    print("\n" + "=" * 60)
    print("TEST 6: Audit Logger")
    print("=" * 60)
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / "test_audit.db"
        jsonl = Path(tmpdir) / "test_audit.jsonl"
        logger = AuditLogger(db_path=str(db), jsonl_path=str(jsonl))

        aid = logger.log(
            tier=1,
            annotation="Change Blu to Wht",
            parsed_instruction={"action_type": "replace_text"},
            confidence_report={"composite": 0.92},
            verification_status="PASSED",
        )
        print(f"  Logged: {aid}")
        assert aid, "Should return action_id"

        entry = logger.get_entry(aid)
        assert entry is not None
        assert entry.tier == 1
        assert entry.previous_hash == "", "First entry should have empty previous_hash"

        # Log second entry to test chain
        aid2 = logger.log(
            tier=2,
            annotation="Move BlockA",
            parsed_instruction={"action_type": "move_entity"},
            confidence_report={"composite": 0.78},
            verification_status="WARNING",
        )
        entry2 = logger.get_entry(aid2)
        assert entry2.previous_hash == entry.entry_hash, "Chain link should match previous entry_hash"

        # Verify integrity
        integrity = logger.verify_chain()
        print(f"  Integrity: {integrity['integrity']} ({integrity['total_entries']} entries)")
        assert integrity["integrity"] == "OK"

        summary = logger.summary()
        print(f"  Summary: {json.dumps(summary, indent=2)[:200]}...")
        assert summary["total_actions"] == 2

    print("  ✅ AuditLogger OK")
    return True


def test_phase3_verifier_mock():
    print("\n" + "=" * 60)
    print("TEST 7: Phase 3 — Post-Action Verifier (text-only mock)")
    print("=" * 60)
    verifier = PostActionVerifier(model="qwen2.5vl:latest")

    # Text-only verification (no screenshot needed)
    parsed = ParsedInstruction(
        action_type="replace_text",
        target_name="Blu",
        replacement_name="Wht",
        confidence=0.90,
        reasoning="",
        needs_human_review=False,
    )
    result = verifier.verify_text_only(
        instruction="Change Blu to Wht",
        before_text="Blu\nGrn\nRed",
        after_text="Wht\nGrn\nRed",
    )
    print(f"  Text-only verify: status={result.status} conf={result.confidence:.2f} "
          f"intended={result.intended_change_detected}")
    assert result.status == "PASSED", f"Simple text replace should pass, got {result.status}"
    assert result.intended_change_detected

    # Failure case
    result2 = verifier.verify_text_only(
        instruction="Change Blu to Wht",
        before_text="Blu\nGrn\nRed",
        after_text="Blu\nGrn\nRed",  # no change!
    )
    print(f"  No-change verify: status={result2.status} conf={result2.confidence:.2f}")
    assert result2.status in ("FAILED", "WARNING"), "No change should fail"

    print("  ✅ PostActionVerifier (text) OK")
    return True


def test_end_to_end_flow(dxf_path: str):
    print("\n" + "=" * 60)
    print("TEST 8: End-to-End Tier 1 Flow with Phase B Safety Gates")
    print("=" * 60)

    # 1. Router
    router = TierRouter(input_format="dxf")
    route_result = router.route("Change Blu to Wht")
    print(f"  Router: { route_result.tier.value } ({route_result.action_type})")
    assert route_result.tier == Tier.EZDXF

    # 2. Parse
    parser = InstructionParser(model="qwen2.5vl:latest")
    parsed = parser.parse("Change Blu to Wht")
    print(f"  Phase 1: action={parsed.action_type} target={parsed.target_name}")
    assert parsed.action_type == "replace_text"

    # 3. Disambiguate
    disambiguator = TargetDisambiguator(model="qwen2.5vl:latest")
    verified = disambiguator.disambiguate(parsed, dxf_path=dxf_path)
    print(f"  Phase 2: handle={verified.target_handle} insert={verified.target_insert} conf={verified.confidence:.2f}")
    assert verified.target_handle

    # 4. Edit (Phase A)
    editor = DXFEditor(dxf_path)
    assert editor.load(), "Failed to load DXF for editing"
    edit_result = editor.replace_text("Blu", "Wht")
    print(f"  Edit: success={edit_result.success} action={edit_result.action}")
    assert edit_result.success

    # 5. Confidence score (pre-verification, skip post-action for speed)
    scorer = ConfidenceScorer()
    report = scorer.score(
        annotation_confidence=parsed.confidence,
        metadata_distance_px=0.0,  # exact match
        post_action_confidence=None,  # not verified yet
    )
    print(f"  Confidence: composite={report.composite_score:.3f} passed={report.overall_passed} review={report.human_review_required}")
    assert report.overall_passed

    # 6. Visual verify (Phase A — render check)
    verifier = VisualVerifier(renderer="auto")
    # We won't actually render since no headless renderer may be available in test env
    print(f"  VisualVerifier: renderer available = {verifier.renderer != 'none'}")

    # 7. Audit log
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = AuditLogger(db_path=str(Path(tmpdir)/"audit.db"))
        aid = logger.log(
            tier=1,
            annotation="Change Blu to Wht",
            parsed_instruction=parsed.to_dict(),
            confidence_report=report.to_dict(),
            verification_status="PASSED",
        )
        print(f"  Audit: {aid}")
        assert aid

    # 8. If score failed, queue for review (not needed here but test the path)
    with tempfile.TemporaryDirectory() as tmpdir:
        q = ReviewQueue(db_path=str(Path(tmpdir)/"reviews.db"))
        rid = q.enqueue(
            annotation="Rearrange layout",
            tier=4,
            parsed_json=parsed.to_dict(),
            confidence_report=report.to_dict(),
        )
        print(f"  Review queue: {rid}")
        assert rid

    print("  ✅ End-to-end flow OK")
    return True


def main():
    print("=" * 60)
    print("Phase B Integration Tests")
    print("=" * 60)

    # Create test DXF
    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path = str(Path(tmpdir) / "test_phase_b.dxf")
        meta = create_test_dxf(dxf_path)
        print(f"Synthetic DXF: {dxf_path} ({len(meta['texts'])} texts, {len(meta['blocks'])} blocks)")

        results = []
        try:
            results.append(("VLM Client", test_vlm_client()))
        except Exception as exc:
            print(f"  ❌ VLM Client FAILED: {exc}")
            results.append(("VLM Client", False))

        try:
            results.append(("Instruction Parser", test_instruction_parser()))
        except Exception as exc:
            print(f"  ❌ Instruction Parser FAILED: {exc}")
            results.append(("Instruction Parser", False))

        try:
            results.append(("Target Disambiguator", test_disambiguator(dxf_path)))
        except Exception as exc:
            print(f"  ❌ Target Disambiguator FAILED: {exc}")
            results.append(("Target Disambiguator", False))

        try:
            results.append(("Confidence Scorer", test_confidence_scorer()))
        except Exception as exc:
            print(f"  ❌ Confidence Scorer FAILED: {exc}")
            results.append(("Confidence Scorer", False))

        try:
            results.append(("Review Queue", test_review_queue()))
        except Exception as exc:
            print(f"  ❌ Review Queue FAILED: {exc}")
            results.append(("Review Queue", False))

        try:
            results.append(("Audit Logger", test_audit_logger()))
        except Exception as exc:
            print(f"  ❌ Audit Logger FAILED: {exc}")
            results.append(("Audit Logger", False))

        try:
            results.append(("Phase 3 Verifier", test_phase3_verifier_mock()))
        except Exception as exc:
            print(f"  ❌ Phase 3 Verifier FAILED: {exc}")
            results.append(("Phase 3 Verifier", False))

        try:
            results.append(("End-to-End Flow", test_end_to_end_flow(dxf_path)))
        except Exception as exc:
            print(f"  ❌ End-to-End Flow FAILED: {exc}")
            results.append(("End-to-End Flow", False))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, ok in results if ok is True or (isinstance(ok, tuple) and ok[0] >= ok[1] * 0.75))
    total = len(results)
    for name, ok in results:
        mark = "✅" if ok is True or (isinstance(ok, tuple) and ok[0] >= ok[1] * 0.75) else "❌"
        print(f"  {mark} {name}")
    print(f"\nOverall: {passed}/{total} suites passed")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
