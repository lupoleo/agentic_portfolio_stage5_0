"""AI-8C.2-R2: context gaps, supplied-fact contradictions, confidence, banks."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.ai.evidence_provider import EvidenceRequest
from app.ai.fundamental_evidence_provider import (
    FUNDAMENTAL_EVIDENCE_POLICY_VERSION,
    YahooFundamentalEvidenceProvider,
)
from app.ai.research_models import EvidenceQuality, ResearchStatus
from app.ai.research_service import (
    RESEARCH_CONTRACT_VERSION,
    RESEARCH_PROMPT_VERSION,
    ResearchEvidence,
    ResearchService,
)
from app.ai.research_validator import (
    ResearchCoverageCode,
    ResearchCoverageSeverity,
    ResearchCoverageValidator,
)
from tests.test_ai_research_forward_uncertainties import (
    NEWS,
    TECHNICAL_WITH_VOLATILITY,
    output,
)
from tests.test_ai_research_service_coverage_integration import NOW, SequenceProvider, candidate


FUNDAMENTAL = ResearchEvidence(
    evidence_id="F1", source_type="FUNDAMENTAL", published_at=NOW,
    text=(
        "Fundamental company snapshot. Revenue: USD 3.17 billion. Trailing EPS: 1.19. "
        "Operating margin: 19.387% (decimal: 0.19387). Operating cash flow: USD 1.47 billion. "
        "Trailing P/E valuation multiple: 14.9."
    ),
)
ANALYST = ResearchEvidence(
    evidence_id="A1", source_type="ANALYST", published_at=NOW,
    text=(
        "Analyst expectations snapshot. Analyst price target mean: 23.95. "
        "Forward EPS estimate: 1.99. Analyst earnings estimate: avg=0.33."
    ),
)
FULL = (TECHNICAL_WITH_VOLATILITY, NEWS, FUNDAMENTAL, ANALYST)


def errors(value, evidence=FULL):
    report = ResearchCoverageValidator().validate(value, list(evidence))
    return {issue.code for issue in report.errors}


def test_versions():
    assert RESEARCH_PROMPT_VERSION == "opportunity-research-v1.5-context-gaps-confidence"
    # R2 introduced contract v3; later revisions (R5: v4) supersede it.
    assert int(RESEARCH_CONTRACT_VERSION.split("-research-v", 1)[1].split("-", 1)[0]) >= 3
    assert FUNDAMENTAL_EVIDENCE_POLICY_VERSION == "yahoo-fundamental-evidence-v3-financials-aware"


# --- context gaps (operator policy, 2026-09-30) ---------------------------------


@pytest.mark.parametrize("item", [
    "Peer valuation benchmarks missing",             # live, 9 of 10 research
    "Current P/E ratio relative to sector",
    "2026 full-year revenue guidance",               # analyst estimates supplied
    "Recent quarterly margins not disclosed",        # trailing margins supplied
    "Missing operating margin trends",
    "Comparative sector P/E ratios not quantified.",  # live UCG LONG, AI-8C.2-R3
])
def test_context_gaps_do_not_block_complete_when_anchor_is_supplied(item):
    assert ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS not in errors(
        output(unknowns=[item])
    )


@pytest.mark.parametrize("item", [
    "Peer valuation benchmarks missing",
    "2026 full-year revenue guidance",
    "Recent quarterly margins not disclosed",
])
def test_context_gaps_still_block_without_their_anchor(item):
    assert ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS in errors(
        output(unknowns=[item]), evidence=(TECHNICAL_WITH_VOLATILITY, NEWS),
    )


@pytest.mark.parametrize("item", [
    "Sector-wide demand for gold/silver in 2026",     # not a comparison
    "M&A deal specifics and valuation",
    "Latest balance sheet not provided",
])
def test_non_comparison_gaps_still_block(item):
    assert ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS in errors(
        output(unknowns=[item])
    )


def test_material_gaps_without_evidence_exempt_nothing():
    value = output(unknowns=["Peer valuation benchmarks missing"])
    assert ResearchCoverageValidator().material_gaps(value) == ["Peer valuation benchmarks missing"]


# --- supplied fundamental / analyst facts ----------------------------------------


@pytest.mark.parametrize("unknown", [
    "Consensus EPS estimates",                        # live CDE, supplied
    "Current operating margins",                      # live CDE, supplied
    "Consensus earnings estimates for 2026",
    "Analyst price targets",
    "Trailing P/E ratio",
])
def test_unknown_contradicting_supplied_fact_is_flagged(unknown):
    value = output(research_status=ResearchStatus.PARTIAL, requires_additional_research=True,
                   unknowns=[unknown])
    assert ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT in errors(value)


@pytest.mark.parametrize("unknown", [
    "Long-term sustainability of operating margin (61.568%)",
    "Missing operating margin trends",
    "Exact P/E and P/B ratios (only relative to peers is stated)",
    "Consensus EPS revisions over the last quarter",
    "Free cash flow growth",
])
def test_refinements_and_forward_items_are_not_contradictions(unknown):
    value = output(research_status=ResearchStatus.PARTIAL, requires_additional_research=True,
                   unknowns=[unknown])
    assert ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT not in errors(value)


def test_supplied_fact_rule_needs_the_fact_in_evidence():
    value = output(research_status=ResearchStatus.PARTIAL, requires_additional_research=True,
                   unknowns=["Consensus EPS estimates"])
    assert ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT not in errors(
        value, evidence=(TECHNICAL_WITH_VOLATILITY, NEWS)
    )


# --- research confidence ---------------------------------------------------------


def _confidence_issues(**overrides):
    value = output(research_status=ResearchStatus.PARTIAL, requires_additional_research=True,
                   **overrides)
    report = ResearchCoverageValidator().validate(value, list(FULL))
    return [issue for issue in report.issues
            if issue.code is ResearchCoverageCode.RESEARCH_CONFIDENCE_INCOHERENT]


def test_zero_confidence_with_medium_quality_is_a_warning_not_an_error():
    issues = _confidence_issues(research_confidence=0.0, evidence_quality=EvidenceQuality.MEDIUM)
    assert len(issues) == 1 and issues[0].severity is ResearchCoverageSeverity.WARNING


@pytest.mark.parametrize("overrides", [
    dict(research_confidence=0.0, evidence_quality=EvidenceQuality.LOW),
    dict(research_confidence=0.2, evidence_quality=EvidenceQuality.MEDIUM),
])
def test_coherent_confidence_is_not_flagged(overrides):
    assert _confidence_issues(**overrides) == []


def _partial(**overrides):
    data = dict(research_status=ResearchStatus.PARTIAL, requires_additional_research=True,
                unknowns=["M&A deal specifics and valuation"], research_confidence=0.0,
                evidence_quality=EvidenceQuality.MEDIUM)
    data.update(overrides)
    return output(**data)


def test_service_repairs_only_the_confidence_field():
    provider = SequenceProvider([_partial(), _partial(research_confidence=0.7)])
    result = ResearchService(provider).research(candidate(), list(FULL), now=NOW)
    assert len(provider.requests) == 2
    assert set(provider.requests[1].output_schema.model_fields) == {"research_confidence"}
    assert "RESEARCH CONFIDENCE REPAIR" in provider.requests[1].prompt
    research = result.research
    assert research.research_confidence == 0.7
    assert research.metadata["initial_research_confidence"] == 0.0
    assert research.metadata["research_confidence_repaired"] is True


def test_service_keeps_research_when_repair_leaves_confidence_incoherent():
    provider = SequenceProvider([_partial(), _partial()])
    result = ResearchService(provider).research(candidate(), list(FULL), now=NOW)
    assert result.research.research_confidence == 0.0
    assert result.research.metadata["research_confidence_repaired"] is False


def test_service_records_context_gaps_and_contract():
    provider = SequenceProvider([output(unknowns=["Peer valuation benchmarks missing"])])
    result = ResearchService(provider).research(candidate(), list(FULL), now=NOW)
    research = result.research
    assert research.research_status is ResearchStatus.COMPLETE
    assert research.metadata["research_contract"] == RESEARCH_CONTRACT_VERSION
    assert research.metadata["material_gaps"] == []
    assert research.metadata["context_gaps"] == ["Peer valuation benchmarks missing"]


def test_prompt_defines_context_gaps_and_confidence():
    prompt = ResearchService(SequenceProvider([]))._build_prompt(candidate(), list(FULL))
    assert "U5. A comparison or finer granularity" in prompt
    assert "research_confidence (0.0-1.0)" in prompt


# --- financial-sector fundamentals ------------------------------------------------


BANK = {
    "sector": "Financial Services", "industry": "Banks - Regional",
    "financialCurrency": "EUR", "totalRevenue": 5_333_326_848,
    "grossMargins": 0.0, "operatingMargins": 0.61568, "profitMargins": 0.36147,
    "operatingCashflow": -7_279_021_056, "totalDebt": 51_835_170_816,
    "trailingPE": 12.5354,
}


def _fetch(info):
    request = EvidenceRequest(ticker="BAMI.MI", as_of=datetime(2026, 9, 30, tzinfo=timezone.utc))
    return YahooFundamentalEvidenceProvider(lambda ticker: dict(info)).fetch(request)


def test_bank_fundamentals_omit_non_meaningful_metrics():
    result = _fetch(BANK)
    text = result.items[0].evidence.text
    assert "Gross margin" not in text
    assert "Operating cash flow" not in text
    assert "Operating margin: 61.568%" in text
    assert "Financial-sector company" in text
    assert result.items[0].source.metadata["financial_sector"] is True


def test_non_financial_fundamentals_are_unchanged():
    info = dict(BANK, sector="Basic Materials", industry="Gold")
    text = _fetch(info).items[0].evidence.text
    assert "Gross margin: 0%" in text
    assert "Operating cash flow" in text
    assert "Financial-sector company" not in text
