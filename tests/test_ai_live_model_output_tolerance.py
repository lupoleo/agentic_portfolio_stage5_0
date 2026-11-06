"""Live 2026-10-01: three model-output defects that failed whole hypotheses."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.company_assessment import CompanyAssessmentService
from app.ai.opportunity_scoring_service import OpportunityScoringService
from app.ai.research_service import ResearchModelOutput
from app.cio.models import Direction
from tests.test_ai_canonical_technical_volatility import _input as canonical_input
from tests.test_ai_company_assessment import EVIDENCE, DispatchProvider
from tests.test_ai_opportunity_scoring_service import output, research
from tests.test_ai_research_forward_uncertainties import output as research_output


# --- research_confidence on a percent scale (FMC NEW_LONG) -------------------------


@pytest.mark.parametrize("raw, expected", [(30, 0.30), (100, 1.0), (85.0, 0.85), (0.7, 0.7), (1, 1.0)])
def test_research_confidence_percent_scale_is_normalized(raw, expected):
    data = research_output().model_dump()
    data["research_confidence"] = raw
    assert ResearchModelOutput.model_validate(data).research_confidence == pytest.approx(expected)


@pytest.mark.parametrize("raw", [150, -5])
def test_research_confidence_out_of_any_scale_still_fails(raw):
    data = research_output().model_dump()
    data["research_confidence"] = raw
    with pytest.raises(ValidationError):
        ResearchModelOutput.model_validate(data)


# --- shared assessment fills a null company-frame component (XERS NEW_SHORT) -------


def _null_expectations():
    return output(expectations={"score": None, "rationale": None, "supporting_evidence_ids": []})


def _score(provider, direction=Direction.SHORT, value=None):
    scoring = OpportunityScoringService(
        provider, company_assessment_service=CompanyAssessmentService(provider)
    )
    return scoring.score(
        value or research(), evidence_coverage_score=.8, direction=direction,
        canonical_technical_input=canonical_input(ticker="PATH"), company_evidence=EVIDENCE,
    )


def test_null_company_frame_component_is_filled_without_repair():
    provider = DispatchProvider([_null_expectations()])
    result = _score(provider)
    schemas = [request.output_schema.__name__ for request in provider.requests]
    assert schemas == ["_OpportunityComponentScoringTransport", "CompanyAssessmentTransport"]
    assert result.components.expectations.score is not None
    sources = result.diagnostics["direction"]["company_assessment"]["sources"]
    assert sources["expectations"] == "SHARED_COMPANY_ASSESSMENT"


def test_without_a_shared_value_the_repair_still_runs():
    provider = DispatchProvider(
        [_null_expectations(), {"expectations": {"score": 55, "rationale": "Repaired.",
                                                 "supporting_evidence_ids": ["E2"]}}],
        fail_company=True,
    )
    result = _score(provider)
    schemas = [request.output_schema.__name__ for request in provider.requests]
    assert "_OpportunityComponentGroundingRepairTransport" in schemas
    assert result.components.expectations.score is not None


# --- technical rationale / citations computed by software (FMC NEW_SHORT) ----------


TECH_ID = "EVID-TECH-PATH-abc123"


def _with_technical_evidence():
    return research(evidence_ids=["E1", "E2", TECH_ID])


def test_missing_technical_rationale_and_invalid_citations_are_replaced():
    provider = DispatchProvider([output(technical={
        "score": 90, "rationale": "", "supporting_evidence_ids": ["J", "K", "L"],
    })])
    result = _score(provider, value=_with_technical_evidence())
    technical = result.components.technical
    assert technical.rationale.startswith("Deterministic SHORT technical base")
    assert technical.supporting_evidence_ids == [TECH_ID]
    assert result.diagnostics["direction"]["technical_fallback"] == {
        "rationale_generated": True, "citations_replaced": True,
    }


def test_valid_technical_output_is_untouched():
    provider = DispatchProvider([output(technical={
        "score": 60, "rationale": "Model rationale.", "supporting_evidence_ids": ["E3"],
    })])
    result = _score(provider, value=_with_technical_evidence())
    assert result.components.technical.rationale == "Model rationale."
    assert result.diagnostics["direction"]["technical_fallback"] == {
        "rationale_generated": False, "citations_replaced": False,
    }


def test_without_canonical_technical_evidence_the_defect_still_fails_closed():
    provider = DispatchProvider([output(technical={
        "score": 90, "rationale": "", "supporting_evidence_ids": ["J"],
    })])
    with pytest.raises(ValueError):
        _score(provider, value=research(evidence_ids=["E1", "E2", "E3"]))
