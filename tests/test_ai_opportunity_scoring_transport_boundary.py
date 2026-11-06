from datetime import datetime, timezone

import pytest

from app.ai.models import AIResponse
from app.ai.opportunity_scoring_service import OpportunityScoringService
from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    OpportunityResearch,
    ResearchStatus,
)


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload

    def infer(self, request):
        return AIResponse(
            provider="FAKE",
            model="fake-model",
            structured_output=self.payload,
            latency_ms=1.0,
            usage={},
        )


def _research():
    return OpportunityResearch(
        research_id="RES-1",
        candidate_id="CAND-1",
        scan_id="SCAN-1",
        created_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
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
        contradictory_evidence=[],
        unknowns=["unknown"],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=.7,
        evidence_ids=["E1"],
        inference_ids=["I1"],
        requires_additional_research=True,
    )


def _payload():
    scored = {
        "score": 70,
        "rationale": "grounded",
        "supporting_evidence_ids": ["E1"],
    }
    return {
        "thesis": dict(scored),
        "catalyst": dict(scored),
        "fundamental": {
            "score": None,
            "rationale": "LLM explains why it cannot score this.",
            "supporting_evidence_ids": [],
        },
        "technical": dict(scored),
        "expectations": {
            "score": None,
            "rationale": "Unknown expectations.",
            "supporting_evidence_ids": [],
        },
        "positive_factors": [],
        "negative_factors": [],
        "uncertainty_factors": [],
    }


def test_default_service_preserves_strict_public_semantics():
    with pytest.raises(Exception):
        OpportunityScoringService(FakeProvider(_payload())).score(
            _research(), evidence_coverage_score=.8
        )


def test_opt_in_transport_normalization_accepts_llm_null_explanation():
    result = OpportunityScoringService(
        FakeProvider(_payload()),
        normalize_provider_transport=True,
    ).score(_research(), evidence_coverage_score=.8)

    assert result.components.fundamental.score is None
    assert result.components.fundamental.rationale is None
    assert result.components.expectations.score is None
    assert result.components.expectations.rationale is None


def test_opt_in_does_not_change_numeric_components():
    result = OpportunityScoringService(
        FakeProvider(_payload()),
        normalize_provider_transport=True,
    ).score(_research(), evidence_coverage_score=.8)

    assert result.components.thesis.score == 70
    assert result.components.catalyst.score == 70
    assert result.components.technical.score == 70
