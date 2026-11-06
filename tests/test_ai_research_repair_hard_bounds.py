from datetime import datetime, timezone

from app.ai.research_models import EvidenceQuality, ExpectationsAssessment, ResearchStatus
from app.ai.research_service import (
    REPAIR_EVIDENCE_TOTAL_CHAR_BUDGET,
    REPAIR_PREVIOUS_TOTAL_CHAR_BUDGET,
    ResearchEvidence,
    ResearchModelOutput,
    ResearchService,
)


def _output(huge=False):
    blob = "X" * 50000 if huge else "normal"
    return ResearchModelOutput(
        research_status=ResearchStatus.PARTIAL,
        market_context=blob,
        fundamental_context=blob,
        technical_context=blob,
        event_context=blob,
        catalyst_assessment=blob,
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case=blob,
        bear_case=blob,
        key_risks=[blob, blob],
        contradictory_evidence=[blob],
        unknowns=[blob],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=.7,
        requires_additional_research=True,
    )


def test_previous_output_is_hard_bounded_even_for_pathological_llm_output():
    block = ResearchService._scoped_previous_output(
        _output(huge=True),
        {"market_context", "fundamental_context", "bull_case", "key_risks"},
    )
    assert len(block) <= REPAIR_PREVIOUS_TOTAL_CHAR_BUDGET
    assert "research_status" in block
    assert "repair_target_previous_values" in block


def test_repair_evidence_block_has_absolute_total_bound():
    evidence = [
        ResearchEvidence(
            evidence_id=f"E{i}",
            source_type="NEWS",
            text=("very long evidence " * 5000),
            published_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        )
        for i in range(10)
    ]
    block = ResearchService._bounded_repair_evidence_block(evidence)
    assert len(block) <= REPAIR_EVIDENCE_TOTAL_CHAR_BUDGET
    assert "E1" in block


def test_small_previous_output_is_not_needlessly_truncated():
    block = ResearchService._scoped_previous_output(
        _output(huge=False),
        {"market_context", "fundamental_context"},
    )
    assert "normal" in block
    assert "PREVIOUS_OUTPUT_TRUNCATED" not in block


def test_hard_bound_text_preserves_head_and_tail_and_exact_ceiling():
    text = "HEAD-" + ("x" * 10000) + "-TAIL"
    bounded = ResearchService._hard_bound_text(
        text, 200, marker="...[CUT]..."
    )
    assert len(bounded) == 200
    assert bounded.startswith("HEAD-")
    assert bounded.endswith("-TAIL")
    assert "...[CUT]..." in bounded
