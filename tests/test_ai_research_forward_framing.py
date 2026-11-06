"""AI-8C.2-R5: forward-framed unknowns do not block COMPLETE (operator decision 2026-10-05)."""
from __future__ import annotations

import pytest

from app.ai.research_models import EvidenceQuality, ResearchStatus
from app.ai.research_service import RESEARCH_CONTRACT_VERSION, ResearchService
from app.ai.research_validator import ResearchCoverageCode, ResearchCoverageValidator
from tests.test_ai_research_context_gaps import FULL
from tests.test_ai_research_forward_uncertainties import output
from tests.test_ai_research_service_coverage_integration import NOW, SequenceProvider, candidate


def codes(value):
    report = ResearchCoverageValidator().validate(value, list(FULL))
    return {issue.code for issue in report.issues}


def partial(**overrides):
    data = dict(research_status=ResearchStatus.PARTIAL, requires_additional_research=True,
                evidence_quality=EvidenceQuality.HIGH, research_confidence=0.7)
    data.update(overrides)
    return output(**data)


def test_contract_version():
    assert RESEARCH_CONTRACT_VERSION == "ai-8c2-research-v4-forward-framing"


@pytest.mark.parametrize("item", [
    "Impact of M&A activity on long-term margins",          # ENEL.MI, 2026-10-05
    "Exact future earnings trajectory",                      # LDO.MI
    "Impact of pending acquisition on margins",              # LDO.MI
    "Sustainability of revenue growth and margin expansion",
    "Market reaction to potential earnings improvements",
])
def test_forward_framed_unknowns_do_not_block_complete(item):
    value = output(unknowns=[item])
    assert ResearchCoverageValidator().forward_framed_unknowns(value) == [item]
    assert ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS not in codes(value)


@pytest.mark.parametrize("item", [
    "Latest free cash flow figures and future capex",        # as-of wording
    "Future margin impact of tariffs not disclosed",         # gap marker
    "Full details on balance sheet leverage and debt structure",
    "Recent impact of tariffs on margins",
])
def test_as_of_gaps_still_block(item):
    assert ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS in codes(output(unknowns=[item]))


@pytest.mark.parametrize("item", [
    "Market acceptance of new valuation metrics",           # ENEL.MI forward list
    "Volatility from macroeconomic shifts",
])
def test_live_forward_items_are_no_longer_reclassified_as_gaps(item):
    value = output(forward_uncertainties=[item])
    assert ResearchCoverageValidator().reclassified_forward_uncertainties(value) == []


def test_sector_specific_multiples_are_a_context_gap_when_pe_is_supplied():
    value = output(unknowns=["Sector-specific pricing multiples not disclosed."])
    assert ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS not in codes(value)


def test_partial_with_only_forward_framed_unknowns_warns():
    issues = ResearchCoverageValidator().validate(
        partial(unknowns=["Impact of M&A activity on long-term margins"]), list(FULL)
    ).issues
    forward = [i for i in issues if i.code is ResearchCoverageCode.FORWARD_ITEMS_IN_UNKNOWNS]
    assert len(forward) == 1 and "long-term margins" in forward[0].message


def test_low_quality_partial_does_not_trigger_a_reassessment():
    value = partial(unknowns=["Impact of M&A activity on long-term margins"],
                    evidence_quality=EvidenceQuality.LOW)
    assert ResearchCoverageCode.FORWARD_ITEMS_IN_UNKNOWNS not in codes(value)


def test_partial_with_a_real_gap_does_not_warn():
    value = partial(unknowns=["Impact of M&A activity on long-term margins",
                              "Full details on balance sheet leverage and debt structure"])
    assert ResearchCoverageCode.FORWARD_ITEMS_IN_UNKNOWNS not in codes(value)


def test_service_lets_the_model_reclassify_and_reassess():
    initial = partial(unknowns=["Impact of M&A activity on long-term margins"])
    repaired = output(unknowns=[], forward_uncertainties=["Impact of M&A activity on long-term margins"])
    provider = SequenceProvider([initial, repaired])
    result = ResearchService(provider).research(candidate(), list(FULL), now=NOW)
    assert len(provider.requests) == 2
    assert {"unknowns", "forward_uncertainties", "research_status",
            "requires_additional_research"} <= set(provider.requests[1].output_schema.model_fields)
    assert "FORWARD UNCERTAINTY RECLASSIFICATION" in provider.requests[1].prompt
    research = result.research
    assert research.research_status is ResearchStatus.COMPLETE
    assert research.metadata["forward_reclassification_requested"] is True
    assert research.metadata["initial_research_status"] == "PARTIAL"
    assert research.metadata["research_contract"] == RESEARCH_CONTRACT_VERSION


def test_software_never_promotes_when_the_model_keeps_partial():
    initial = partial(unknowns=["Impact of M&A activity on long-term margins"])
    provider = SequenceProvider([initial, initial])
    result = ResearchService(provider).research(candidate(), list(FULL), now=NOW)
    assert result.research.research_status is ResearchStatus.PARTIAL
    assert result.research.metadata["forward_framed_unknowns"] == [
        "Impact of M&A activity on long-term margins"
    ]


def test_partial_with_forward_items_only_in_their_own_list_is_reassessed():
    # Live 2026-10-05, ENEL.MI NEW_SHORT: no material gap once the forward
    # items are no longer reclassified, but no forward-framed unknowns either.
    value = partial(unknowns=["Peer valuation benchmarks", "Recent quarterly margins"],
                    forward_uncertainties=["Market acceptance of new valuation metrics",
                                           "Volatility from macroeconomic shifts"])
    issues = ResearchCoverageValidator().validate(value, list(FULL)).issues
    forward = [i for i in issues if i.code is ResearchCoverageCode.FORWARD_ITEMS_IN_UNKNOWNS]
    assert len(forward) == 1
    assert "every open item is a forward uncertainty or a context gap" in forward[0].message


def test_partial_without_any_forward_item_is_not_reassessed():
    value = partial(unknowns=["Peer valuation benchmarks"], forward_uncertainties=[])
    assert ResearchCoverageCode.FORWARD_ITEMS_IN_UNKNOWNS not in codes(value)
