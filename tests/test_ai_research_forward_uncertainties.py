"""AI-8C.2-R1: missing as-of facts versus forward-looking uncertainties."""
from __future__ import annotations

import pytest

from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    OpportunityResearch,
    ResearchStatus,
)
from app.ai.research_service import (
    RESEARCH_CONTRACT_VERSION,
    RESEARCH_PROMPT_VERSION,
    ResearchEvidence,
    ResearchModelOutput,
    ResearchService,
)
from app.ai.research_validator import ResearchCoverageCode, ResearchCoverageValidator
from tests.test_ai_research_service_coverage_integration import (
    NOW,
    SequenceProvider,
    candidate,
)


TECHNICAL_WITH_VOLATILITY = ResearchEvidence(
    evidence_id="T1", source_type="MARKET", published_at=NOW,
    text=(
        "Latest price: 21.40. RSI14: 58.2000. RVOL: 1.1000x. "
        "20-session annualized volatility: 23.4500%. Canonical trend: BULLISH."
    ),
)
NEWS = ResearchEvidence(
    evidence_id="N1", source_type="NEWS", published_at=NOW,
    text="The company announced a new multi-year customer contract.",
)


def output(**overrides):
    data = dict(
        research_status=ResearchStatus.COMPLETE,
        market_context="Sector steady.",
        fundamental_context="Revenue grew 12% with stable margins.",
        technical_context="Bullish trend; RSI 58; volatility 23%.",
        event_context="New multi-year contract announced.",
        catalyst_assessment="Contract supports revenue visibility.",
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Contract and trend align.",
        bear_case="Execution could disappoint.",
        key_risks=["Execution"],
        contradictory_evidence=[],
        unknowns=[],
        forward_uncertainties=[],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=0.85,
        requires_additional_research=False,
    )
    data.update(overrides)
    return ResearchModelOutput(**data)


def codes(value, evidence=(TECHNICAL_WITH_VOLATILITY, NEWS)):
    report = ResearchCoverageValidator().validate(value, list(evidence))
    return {issue.code for issue in report.errors}


def test_versions_are_explicit():
    # R1 introduced prompt v1.4 / contract v2; later revisions supersede them.
    prompt_minor = int(RESEARCH_PROMPT_VERSION.split("-v1.", 1)[1].split("-", 1)[0])
    contract_major = int(RESEARCH_CONTRACT_VERSION.split("-research-v", 1)[1].split("-", 1)[0])
    assert prompt_minor >= 4 and contract_major >= 2


def test_model_field_defaults_and_legacy_payload_loads():
    legacy = dict(
        research_id="RES-1", candidate_id="C-1", scan_id="S-1", created_at=NOW,
        ticker="hpe", research_status=ResearchStatus.PARTIAL,
        evidence_quality=EvidenceQuality.MEDIUM, research_confidence=0.5,
        requires_additional_research=True,
    )
    assert OpportunityResearch(**legacy).forward_uncertainties == []
    with pytest.raises(ValueError):
        OpportunityResearch(**legacy, forward_uncertainties=["  "])


@pytest.mark.parametrize("item", [
    "Long-term impact of new contracts on margins",          # HPE LONG, 2026-09-30
    "Sustainability of free cash flow growth beyond 2024",
    "Future earnings growth trajectory",
    "Potential for further valuation compression if earnings growth stalls.",
])
def test_forward_uncertainties_do_not_block_complete(item):
    assert ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS not in codes(
        output(forward_uncertainties=[item])
    )


@pytest.mark.parametrize("item", [
    "Exact margins and balance sheet details not specified",
    "No analyst price targets or consensus recommendations provided",
    "Fundamental strength relative to industry peers",
    "True valuation relative to industry peers",
])
def test_forward_items_that_are_as_of_gaps_block_complete(item):
    assert ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS in codes(
        output(forward_uncertainties=[item])
    )


def test_material_unknowns_still_block_complete():
    assert ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS in codes(
        output(unknowns=["Latest quarterly revenue is not supplied"])
    )


def test_material_gaps_combine_unknowns_and_reclassified_items():
    validator = ResearchCoverageValidator()
    value = output(
        unknowns=["Consensus estimates"],
        forward_uncertainties=["Future margin path", "Peer valuation not provided"],
    )
    assert validator.material_gaps(value) == ["Consensus estimates", "Peer valuation not provided"]
    assert validator.reclassified_forward_uncertainties(value) == ["Peer valuation not provided"]


@pytest.mark.parametrize("unknown", [
    "Technical volatility metrics beyond RSI and moving averages are unknown",
    "Technical volatility magnitude is not explicitly quantified",
    "technical_volatility_not_priced_in",
])
def test_volatility_unknown_contradicts_supplied_volatility(unknown):
    value = output(research_status=ResearchStatus.PARTIAL, requires_additional_research=True,
                   unknowns=[unknown])
    assert ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT in codes(value)


def test_volatility_unknown_quoting_a_value_is_not_a_contradiction():
    value = output(research_status=ResearchStatus.PARTIAL, requires_additional_research=True,
                   unknowns=["Volatility persistence beyond current 22.37% level"])
    assert ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT not in codes(value)


def test_volatility_unknown_without_supplied_volatility_is_allowed():
    value = output(research_status=ResearchStatus.PARTIAL, requires_additional_research=True,
                   unknowns=["Technical volatility is unknown"])
    assert ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT not in codes(value, evidence=(NEWS,))


def test_prompt_defines_the_distinction_and_drops_the_volatility_example():
    prompt = ResearchService(SequenceProvider([]))._build_prompt(
        candidate(), [TECHNICAL_WITH_VOLATILITY, NEWS]
    )
    assert "UNKNOWNS VERSUS FORWARD UNCERTAINTIES" in prompt
    assert "They never by themselves" in prompt
    assert "technical volatility\n    is unknown" not in prompt
    assert "never list any of them" in prompt


def test_service_persists_forward_uncertainties_and_complete_status():
    provider = SequenceProvider([output(
        forward_uncertainties=["Long-term impact of new contracts on margins"],
    )])
    result = ResearchService(provider).research(
        candidate(), [TECHNICAL_WITH_VOLATILITY, NEWS], now=NOW,
    )
    research = result.research
    assert research.research_status is ResearchStatus.COMPLETE
    assert research.forward_uncertainties == ["Long-term impact of new contracts on margins"]
    assert research.metadata["research_contract"] == RESEARCH_CONTRACT_VERSION
    assert research.metadata["material_gaps"] == []
    assert research.metadata["forward_uncertainty_count"] == 1


def test_service_fails_closed_when_a_disguised_gap_keeps_complete():
    disguised = output(forward_uncertainties=["Latest balance sheet not provided"])
    provider = SequenceProvider([disguised, disguised])
    result = ResearchService(provider).research(
        candidate(), [TECHNICAL_WITH_VOLATILITY, NEWS], now=NOW,
    )
    assert result.research.research_status is ResearchStatus.PARTIAL
    assert result.research.requires_additional_research is True
    assert result.research.metadata["material_gaps"] == ["Latest balance sheet not provided"]


def test_service_removes_volatility_unknown_that_contradicts_evidence():
    initial = output(
        research_status=ResearchStatus.PARTIAL, requires_additional_research=True,
        unknowns=["Technical volatility metrics beyond RSI and moving averages are unknown"],
    )
    provider = SequenceProvider([initial, initial])
    result = ResearchService(provider).research(
        candidate(), [TECHNICAL_WITH_VOLATILITY, NEWS], now=NOW,
    )
    assert result.research.unknowns == []
    assert result.research.metadata["deterministic_unknowns_canonicalized"] is True
