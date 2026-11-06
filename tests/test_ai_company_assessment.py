"""AI-8C.3-R2.2: one shared, direction-free company assessment per listing."""
from __future__ import annotations

import pytest

from app.ai.company_assessment import (
    COMPANY_ASSESSMENT_POLICY,
    CompanyAssessmentService,
    CompanyAssessmentTransport,
)
from app.ai.models import AIResponse
from app.ai.opportunity_scoring_service import OpportunityScoringService
from app.ai.research_models import ExpectationsAssessment
from app.ai.research_service import ResearchEvidence
from app.cio.models import Direction
from app.scanner.research_integration import (
    build_research_hypotheses,
    opportunity_materialization_decision,
)
from app.scanner.research_integration_contracts import (
    HypothesisOutcomeReason,
    ResearchHypothesisKind,
)
from tests.test_ai_canonical_technical_volatility import _input as canonical_input
from tests.test_ai_opportunity_scoring_service import output, research
from tests.test_scanner_research_integration import (
    NOW,
    research as integration_research,
    score as integration_score,
    universe,
)


FUNDAMENTAL = ResearchEvidence(evidence_id="E1", source_type="FUNDAMENTAL",
                               text="Fundamental company snapshot. Revenue growth: 38%.")
ANALYST = ResearchEvidence(evidence_id="E2", source_type="ANALYST",
                           text="Analyst expectations snapshot. Analyst price target mean: 30.")
NEWS = ResearchEvidence(evidence_id="E3", source_type="NEWS", text="Contract announced.")
EVIDENCE = [FUNDAMENTAL, ANALYST, NEWS]

COMPANY_PAYLOAD = {
    "fundamental": {"score": 80, "rationale": "Strong growth.", "supporting_evidence_ids": ["C1"]},
    "expectations": {"score": 70, "rationale": "Targets above price.", "supporting_evidence_ids": ["C2"]},
}


class DispatchProvider:
    """Answers scoring and company-assessment requests with fixed payloads."""

    def __init__(self, scoring_payloads, company_payload=COMPANY_PAYLOAD, fail_company=False):
        self.scoring_payloads = list(scoring_payloads)
        self.company_payload = company_payload
        self.fail_company = fail_company
        self.requests = []

    def infer(self, request):
        self.requests.append(request)
        if request.output_schema is CompanyAssessmentTransport:
            if self.fail_company:
                raise RuntimeError("provider down")
            payload = self.company_payload
        else:
            payload = self.scoring_payloads.pop(0)
        return AIResponse(provider="FAKE", model="fake", structured_output=payload,
                          latency_ms=1.0, usage={})

    def company_calls(self):
        return [r for r in self.requests if r.output_schema is CompanyAssessmentTransport]


# --- service ---------------------------------------------------------------------


def test_assessment_uses_only_company_evidence_and_no_direction():
    provider = DispatchProvider([])
    result = CompanyAssessmentService(provider).assess("PATH", EVIDENCE)
    assert result.status == "ASSESSED"
    assert result.evidence_ids == ("E1", "E2")
    assert result.fundamental.score == 80 and result.fundamental.evidence_ids == ("E1",)
    assert result.expectations.evidence_ids == ("E2",)
    prompt = provider.company_calls()[0].prompt
    assert "Contract announced" not in prompt
    assert "HYPOTHESIS DIRECTION" not in prompt
    assert "SHORT" not in prompt and "LONG" not in prompt
    assert result.to_diagnostics()["policy_version"] == COMPANY_ASSESSMENT_POLICY


def test_assessment_is_cached_by_evidence():
    provider = DispatchProvider([])
    service = CompanyAssessmentService(provider)
    first = service.assess("PATH", EVIDENCE)
    second = service.assess("PATH", list(reversed(EVIDENCE)))
    assert first is second
    assert len(provider.company_calls()) == 1
    service.assess("PATH", [FUNDAMENTAL])
    assert len(provider.company_calls()) == 2


def test_no_company_evidence_makes_no_call():
    provider = DispatchProvider([])
    result = CompanyAssessmentService(provider).assess("PATH", [NEWS])
    assert result.status == "NO_COMPANY_EVIDENCE"
    assert provider.company_calls() == []


def test_provider_failure_fails_closed_without_raising():
    provider = DispatchProvider([], fail_company=True)
    result = CompanyAssessmentService(provider).assess("PATH", EVIDENCE)
    assert result.status == "FAILED"
    assert result.fundamental.score is None


def test_ungrounded_scores_are_dropped():
    payload = {"fundamental": {"score": 80, "rationale": "Strong.", "supporting_evidence_ids": ["C9"]}}
    result = CompanyAssessmentService(DispatchProvider([], company_payload=payload)).assess("PATH", EVIDENCE)
    assert result.fundamental.score is None
    assert "FUNDAMENTAL_UNGROUNDED" in result.diagnostics


# --- scoring integration ---------------------------------------------------------


def _research():
    return research()  # expectations PARTIALLY_PRICED_IN: both components scorable


def _score(service, provider, direction, evidence=EVIDENCE, value=None):
    scoring = OpportunityScoringService(provider, company_assessment_service=service)
    return scoring.score(
        value or _research(), evidence_coverage_score=.8, direction=direction,
        canonical_technical_input=canonical_input(ticker="PATH"), company_evidence=evidence,
    )


def test_long_and_short_share_one_company_assessment():
    # The scoring model disagrees with itself across directions (live defect);
    # the shared assessment must override both sides identically.
    provider = DispatchProvider([
        output(fundamental={"score": 40, "rationale": "x", "supporting_evidence_ids": ["E1"]}),
        output(fundamental={"score": 75, "rationale": "y", "supporting_evidence_ids": ["E1"]}),
    ])
    service = CompanyAssessmentService(provider)
    long_result = _score(service, provider, Direction.LONG)
    short_result = _score(service, provider, Direction.SHORT)
    assert len(provider.company_calls()) == 1
    long_frame = long_result.diagnostics["direction"]["company_frame_components"]
    short_frame = short_result.diagnostics["direction"]["company_frame_components"]
    assert long_frame == short_frame
    for name in ("fundamental", "expectations"):
        long_value = getattr(long_result.components, name).score
        short_value = getattr(short_result.components, name).score
        assert long_value + short_value == pytest.approx(100.0)
    sources = short_result.diagnostics["direction"]["company_assessment"]["sources"]
    assert sources == {"fundamental": "SHARED_COMPANY_ASSESSMENT",
                       "expectations": "SHARED_COMPANY_ASSESSMENT"}
    model_values = short_result.diagnostics["direction"]["company_assessment"]["scoring_model_values"]
    assert model_values["fundamental"] == 75


def test_shared_value_not_cited_by_research_is_not_used():
    provider = DispatchProvider([output()])
    service = CompanyAssessmentService(provider)
    foreign = ResearchEvidence(evidence_id="EX", source_type="FUNDAMENTAL", text="Other snapshot.")
    result = _score(service, provider, Direction.LONG, evidence=[foreign])
    assert result.diagnostics["direction"]["company_assessment"]["sources"]["fundamental"] == (
        "SCORING_MODEL_SHARED_NOT_GROUNDED"
    )


def test_unscorable_component_is_not_overridden():
    provider = DispatchProvider([output(expectations={"score": None, "rationale": None,
                                                      "supporting_evidence_ids": []})])
    service = CompanyAssessmentService(provider)
    result = _score(service, provider, Direction.LONG,
                    value=research(expectations_assessment=ExpectationsAssessment.UNKNOWN))
    assert result.components.expectations.score is None
    assert result.diagnostics["direction"]["company_assessment"]["sources"]["expectations"] == "SCORING_MODEL"


def test_scoring_without_company_service_records_disabled():
    provider = DispatchProvider([output()])
    result = OpportunityScoringService(provider).score(
        _research(), evidence_coverage_score=.8, direction=Direction.LONG,
        canonical_technical_input=canonical_input(ticker="PATH"), company_evidence=EVIDENCE,
    )
    assert provider.company_calls() == []
    assert result.diagnostics["direction"]["company_assessment"]["reason"] == "COMPANY_ASSESSMENT_DISABLED"


# --- materialization -------------------------------------------------------------


@pytest.fixture
def short_enabled(monkeypatch):
    import app.scanner.research_integration as integration

    monkeypatch.setattr(integration, "SHORT_MATERIALIZATION_ENABLED", True)


def _short():
    hypotheses = build_research_hypotheses(universe(), created_at=NOW)
    return next(h for h in hypotheses if h.kind is ResearchHypothesisKind.NEW_SHORT)


def _metadata(source):
    return {"scoring_diagnostics": {"direction": {
        "direction": "SHORT", "direction_source": "HYPOTHESIS",
        "policy_version": "ai-8c3-directional-scoring-v3",
        "company_assessment": {"sources": {"fundamental": source, "expectations": source}},
    }}}


def test_short_requires_shared_company_assessment(short_enabled):
    short = _short()
    complete = integration_research(short)
    rejected = opportunity_materialization_decision(
        short, complete, integration_score(short, complete, metadata=_metadata("SCORING_MODEL")),
    )
    assert rejected.reason is HypothesisOutcomeReason.SCORE_DIRECTION_MISMATCH
    accepted = opportunity_materialization_decision(
        short, complete, integration_score(short, complete, metadata=_metadata("SHARED_COMPANY_ASSESSMENT")),
    )
    assert accepted.reason is HypothesisOutcomeReason.OPPORTUNITY_CREATED


def test_runtime_enables_the_shared_assessment():
    import inspect

    import app.e2e.stage4_runtime as runtime

    assert "company_assessment_service=CompanyAssessmentService(provider)" in inspect.getsource(runtime)
