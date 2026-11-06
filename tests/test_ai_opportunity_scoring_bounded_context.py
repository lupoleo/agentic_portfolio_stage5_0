from datetime import datetime, timezone

from app.ai.opportunity_score_models import OpportunityScoringProfile
from app.ai.opportunity_scoring_service import OpportunityScoringService
from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    OpportunityResearch,
    ResearchStatus,
)


def _research(**overrides):
    data = dict(
        research_id="RES-1",
        candidate_id="CAND-1",
        scan_id="SCAN-1",
        created_at=datetime.now(timezone.utc),
        ticker="PATH",
        research_status=ResearchStatus.PARTIAL,
        market_context="market",
        fundamental_context="fundamental",
        technical_context="technical",
        event_context="event",
        catalyst_assessment="catalyst",
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="bull",
        bear_case="bear",
        key_risks=["risk"],
        contradictory_evidence=["contradiction"],
        unknowns=["unknown"],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=0.7,
        evidence_ids=["E1", "E2"],
        inference_ids=["I1"],
        requires_additional_research=True,
    )
    data.update(overrides)
    return OpportunityResearch(**data)


def test_small_context_is_preserved_exactly():
    research = _research()
    context = OpportunityScoringService._build_bounded_scoring_context(research)
    assert context["market_context"] == "market"
    assert context["fundamental_context"] == "fundamental"
    assert context["technical_context"] == "technical"


def test_every_context_field_obeys_its_hard_budget():
    huge = "Sentence with useful evidence. " * 5000
    research = _research(
        market_context=huge,
        fundamental_context=huge,
        technical_context=huge,
        event_context=huge,
        catalyst_assessment=huge,
        bull_case=huge,
        bear_case=huge,
        key_risks=[huge],
        contradictory_evidence=[huge],
        unknowns=[huge],
    )
    context = OpportunityScoringService._build_bounded_scoring_context(research)
    for field, budget in OpportunityScoringService._SCORING_CONTEXT_BUDGETS.items():
        assert len(context[field]) <= budget


def test_giant_single_unit_preserves_head_and_tail():
    value = "HEAD-" + ("x" * 5000) + "-TAIL"
    compact = OpportunityScoringService._bounded_context_value(value, 200)
    assert len(compact) <= 200
    assert compact.startswith("HEAD-")
    assert compact.endswith("-TAIL")
    assert "[bounded]" in compact


def test_prompt_size_is_bounded_for_arbitrarily_large_research():
    huge = "Evidence sentence one. Evidence sentence two. " * 10000
    research = _research(
        market_context=huge,
        fundamental_context=huge,
        technical_context=huge,
        event_context=huge,
        catalyst_assessment=huge,
        bull_case=huge,
        bear_case=huge,
        key_risks=[huge],
        contradictory_evidence=[huge],
        unknowns=[huge],
    )
    service = OpportunityScoringService(provider=object())
    prompt = service._build_prompt(research, OpportunityScoringProfile.STANDARD)

    # The field budgets total 10,100 chars. Instructions/headers/evidence IDs
    # add a bounded overhead. This protects against the prior ~40k prompt.
    assert len(prompt) < 14000


def test_prompt_does_not_include_unbounded_raw_research():
    sentinel = "UNBOUNDED_SENTINEL_" + ("z" * 20000)
    research = _research(fundamental_context=sentinel)
    service = OpportunityScoringService(provider=object())
    prompt = service._build_prompt(research, OpportunityScoringProfile.STANDARD)
    assert sentinel not in prompt
    assert "UNBOUNDED_SENTINEL_" in prompt


def test_missing_canonical_context_remains_none_in_prompt():
    research = _research(fundamental_context=None, technical_context=None)
    service = OpportunityScoringService(provider=object())
    prompt = service._build_prompt(research, OpportunityScoringProfile.STANDARD)
    assert "Fundamental context: None" in prompt
    assert "Technical context: None" in prompt


def test_allowed_evidence_ids_are_preserved():
    research = _research(evidence_ids=["EV-A", "EV-B", "EV-C"])
    service = OpportunityScoringService(provider=object())
    prompt = service._build_prompt(research, OpportunityScoringProfile.STANDARD)
    assert "ALLOWED EVIDENCE IDS: EV-A, EV-B, EV-C" in prompt
