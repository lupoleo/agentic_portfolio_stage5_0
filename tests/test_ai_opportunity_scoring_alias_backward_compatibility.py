from datetime import datetime, timezone

from app.ai.opportunity_score_models import OpportunityScoringProfile
from app.ai.opportunity_scoring_service import OpportunityScoringService
from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    OpportunityResearch,
    ResearchStatus,
)


def _research():
    return OpportunityResearch(
        research_id="RES-1",
        candidate_id="CAND-1",
        scan_id="SCAN-1",
        created_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        ticker="SNPS",
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
        contradictory_evidence=[],
        unknowns=["unknown"],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=.7,
        evidence_ids=["EV-A", "EV-B", "EV-C"],
        inference_ids=["I1"],
        requires_additional_research=True,
    )


def test_legacy_prompt_preserves_canonical_evidence_ids():
    prompt = OpportunityScoringService(provider=object())._build_prompt(
        _research(), OpportunityScoringProfile.STANDARD
    )
    assert "ALLOWED EVIDENCE IDS: EV-A, EV-B, EV-C" in prompt
    assert "ALLOWED EVIDENCE REFERENCES:" not in prompt


def test_explicit_alias_map_uses_transport_references():
    research = _research()
    _, c2a = OpportunityScoringService._build_evidence_alias_maps(
        research.evidence_ids
    )
    prompt = OpportunityScoringService(provider=object())._build_prompt(
        research,
        OpportunityScoringProfile.STANDARD,
        canonical_to_alias=c2a,
    )
    assert "ALLOWED EVIDENCE REFERENCES: E1, E2, E3" in prompt
    assert "ALLOWED EVIDENCE IDS: EV-A, EV-B, EV-C" not in prompt
    assert "Copy aliases exactly" in prompt


def test_legacy_prompt_does_not_instruct_alias_copying():
    prompt = OpportunityScoringService(provider=object())._build_prompt(
        _research(), OpportunityScoringProfile.STANDARD
    )
    assert "Copy aliases exactly" not in prompt
    assert "more IDs from ALLOWED EVIDENCE IDS" in prompt
