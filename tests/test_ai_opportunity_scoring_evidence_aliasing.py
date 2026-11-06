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
        evidence_ids=["CANON-A", "CANON-B", "CANON-C"],
        inference_ids=["I1"],
        requires_additional_research=True,
    )


def _payload(ref):
    base = {
        "score": None,
        "rationale": None,
        "supporting_evidence_ids": [],
    }
    payload = {
        name: dict(base)
        for name in ("thesis", "catalyst", "fundamental", "technical", "expectations")
    }
    payload["thesis"] = {
        "score": 70,
        "rationale": "grounded",
        "supporting_evidence_ids": [ref],
    }
    payload.update(
        positive_factors=[],
        negative_factors=[],
        uncertainty_factors=[],
    )
    return payload


def test_alias_maps_are_stable_and_ordered():
    a2c, c2a = OpportunityScoringService._build_evidence_alias_maps(
        ["CANON-A", "CANON-B", "CANON-C"]
    )
    assert a2c == {"E1": "CANON-A", "E2": "CANON-B", "E3": "CANON-C"}
    assert c2a == {"CANON-A": "E1", "CANON-B": "E2", "CANON-C": "E3"}


def test_exact_aliases_are_dereferenced():
    out = OpportunityScoringService._dereference_evidence_aliases(
        _payload("E2"), {"E1": "CANON-A", "E2": "CANON-B"}
    )
    assert out["thesis"]["supporting_evidence_ids"] == ["CANON-B"]


def test_unknown_alias_is_preserved_fail_closed():
    out = OpportunityScoringService._dereference_evidence_aliases(
        _payload("E99"), {"E1": "CANON-A"}
    )
    assert out["thesis"]["supporting_evidence_ids"] == ["E99"]


def test_typo_alias_is_not_fuzzy_repaired():
    out = OpportunityScoringService._dereference_evidence_aliases(
        _payload("E1x"), {"E1": "CANON-A"}
    )
    assert out["thesis"]["supporting_evidence_ids"] == ["E1x"]


def test_prompt_exposes_short_aliases_not_canonical_ids():
    research = _research()
    _, c2a = OpportunityScoringService._build_evidence_alias_maps(
        research.evidence_ids
    )
    prompt = OpportunityScoringService(provider=None)._build_prompt(
        research,
        OpportunityScoringProfile.STANDARD,
        canonical_to_alias=c2a,
    )
    assert "ALLOWED EVIDENCE REFERENCES: E1, E2, E3" in prompt
    assert "CANON-A" not in prompt
    assert "CANON-B" not in prompt
    assert "CANON-C" not in prompt
    assert "Copy aliases exactly" in prompt
