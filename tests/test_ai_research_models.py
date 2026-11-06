from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    OpportunityResearch,
    ResearchStatus,
)


NOW = datetime(2026, 8, 30, 21, 30, tzinfo=timezone.utc)


def make_research(**overrides):
    data = {
        "research_id": "RES-001",
        "candidate_id": "CAND-001",
        "scan_id": "SCAN-001",
        "created_at": NOW,
        "ticker": "PATH",
        "portfolio_snapshot_id": "SNAP-001",
        "risk_state_id": "RISK-001",
        "research_status": ResearchStatus.COMPLETE,
        "market_context": "Software sector constructive.",
        "fundamental_context": "Growth remains positive.",
        "technical_context": "Momentum is improving.",
        "event_context": "Earnings are approaching.",
        "catalyst_assessment": "Guidance is the principal catalyst.",
        "expectations_assessment": (
            ExpectationsAssessment.PARTIALLY_PRICED_IN
        ),
        "bull_case": "Upside if guidance exceeds expectations.",
        "bear_case": "Downside if guidance disappoints.",
        "key_risks": ["Valuation", "Guidance risk"],
        "contradictory_evidence": ["Recent multiple expansion"],
        "unknowns": ["Whisper expectations"],
        "evidence_quality": EvidenceQuality.HIGH,
        "research_confidence": 0.82,
        "evidence_ids": ["NEWS-001", "EARNINGS-001"],
        "inference_ids": ["AI-001"],
        "requires_additional_research": False,
        "metadata": {"prompt_version": "research-v1"},
    }
    data.update(overrides)
    return OpportunityResearch(**data)


def test_valid_complete_research():
    research = make_research()

    assert research.ticker == "PATH"
    assert research.research_status == ResearchStatus.COMPLETE
    assert research.research_confidence == 0.82


def test_ticker_is_normalized():
    research = make_research(ticker="  path ")

    assert research.ticker == "PATH"


def test_blank_ticker_rejected():
    with pytest.raises(ValidationError, match="ticker cannot be blank"):
        make_research(ticker="   ")


@pytest.mark.parametrize(
    "field_name",
    ["research_id", "candidate_id", "scan_id"],
)
def test_blank_core_identifier_rejected(field_name):
    with pytest.raises(ValidationError, match="identifier cannot be blank"):
        make_research(**{field_name: "   "})


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_research_confidence_is_bounded(confidence):
    with pytest.raises(ValidationError):
        make_research(research_confidence=confidence)


def test_insufficient_evidence_requires_more_research():
    with pytest.raises(
        ValidationError,
        match="INSUFFICIENT_EVIDENCE requires",
    ):
        make_research(
            research_status=ResearchStatus.INSUFFICIENT_EVIDENCE,
            requires_additional_research=False,
        )


def test_insufficient_evidence_valid_when_more_research_required():
    research = make_research(
        research_status=ResearchStatus.INSUFFICIENT_EVIDENCE,
        evidence_quality=EvidenceQuality.LOW,
        requires_additional_research=True,
    )

    assert research.requires_additional_research is True


def test_complete_research_cannot_request_more_research():
    with pytest.raises(
        ValidationError,
        match="COMPLETE research cannot require",
    ):
        make_research(
            research_status=ResearchStatus.COMPLETE,
            requires_additional_research=True,
        )


def test_partial_research_may_request_more_research():
    research = make_research(
        research_status=ResearchStatus.PARTIAL,
        evidence_quality=EvidenceQuality.MEDIUM,
        requires_additional_research=True,
    )

    assert research.research_status == ResearchStatus.PARTIAL


@pytest.mark.parametrize(
    "field_name",
    [
        "key_risks",
        "contradictory_evidence",
        "unknowns",
        "evidence_ids",
        "inference_ids",
    ],
)
def test_blank_list_items_rejected(field_name):
    with pytest.raises(ValidationError, match="list items cannot be blank"):
        make_research(**{field_name: ["valid", "   "]})


def test_default_expectations_assessment_is_unknown():
    data = make_research().model_dump()
    data.pop("expectations_assessment")

    research = OpportunityResearch.model_validate(data)

    assert (
        research.expectations_assessment
        == ExpectationsAssessment.UNKNOWN
    )


def test_extra_fields_are_forbidden():
    with pytest.raises(ValidationError):
        make_research(trading_decision="BUY")


def test_no_trade_decision_fields_in_contract():
    fields = OpportunityResearch.model_fields

    forbidden = {
        "direction",
        "action",
        "decision",
        "position_size",
        "instrument_id",
        "opportunity_id",
    }

    assert forbidden.isdisjoint(fields)


def test_round_trip_json():
    research = make_research()

    loaded = OpportunityResearch.model_validate_json(
        research.model_dump_json()
    )

    assert loaded == research
