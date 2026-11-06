from datetime import datetime, timezone

from app.ai.research_models import EvidenceQuality, ExpectationsAssessment, ResearchStatus
from app.ai.research_service import ResearchEvidence, ResearchModelOutput
from app.ai.research_validator import ResearchCoverageCode, ResearchCoverageValidator

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)

def evidence():
    return [
        ResearchEvidence(evidence_id="M1", source_type="MARKET", published_at=NOW,
                         text="20-session return 42.34%. SMA20 16.0468. RSI14 72.53. RVOL 0.79x."),
        ResearchEvidence(evidence_id="N1", source_type="NEWS", published_at=NOW,
                         text="UiPath highlighted a Banco Azteca Maestro deployment."),
    ]

def output(**overrides):
    data = dict(
        research_status=ResearchStatus.PARTIAL,
        market_context=None,
        fundamental_context=None,
        technical_context="20-session return is strong; RSI14 is elevated.",
        event_context="UiPath highlighted a Banco Azteca deployment.",
        catalyst_assessment="Deployment adoption may support the orchestration narrative.",
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Adoption and momentum could support upside.",
        bear_case="Extended price action raises pullback risk.",
        key_risks=["Technical extension"],
        contradictory_evidence=[],
        unknowns=["Valuation", "Consensus expectations"],
        evidence_quality=EvidenceQuality.MEDIUM,
        research_confidence=0.65,
        requires_additional_research=True,
    )
    data.update(overrides)
    return ResearchModelOutput(**data)

def codes(report):
    return {x.code for x in report.issues}

def test_valid_partial_output_passes():
    assert ResearchCoverageValidator().validate(output(), evidence()).is_valid

def test_missing_technical_context_fails_when_technical_evidence_exists():
    r = ResearchCoverageValidator().validate(output(technical_context=None), evidence())
    assert ResearchCoverageCode.TECHNICAL_CONTEXT_MISSING in codes(r)
    assert not r.is_valid

def test_missing_event_context_fails_when_news_exists():
    r = ResearchCoverageValidator().validate(output(event_context=None), evidence())
    assert ResearchCoverageCode.EVENT_CONTEXT_MISSING in codes(r)

def test_complete_low_quality_fails():
    r = ResearchCoverageValidator().validate(
        output(research_status=ResearchStatus.COMPLETE, evidence_quality=EvidenceQuality.LOW,
               requires_additional_research=False, unknowns=[]), evidence())
    assert ResearchCoverageCode.COMPLETE_WITH_LOW_EVIDENCE in codes(r)

def test_complete_with_material_unknowns_fails():
    r = ResearchCoverageValidator().validate(
        output(research_status=ResearchStatus.COMPLETE, evidence_quality=EvidenceQuality.HIGH,
               requires_additional_research=False, unknowns=["Current valuation"]), evidence())
    assert ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS in codes(r)

def test_complete_requiring_more_fails():
    r = ResearchCoverageValidator().validate(
        output(research_status=ResearchStatus.COMPLETE, evidence_quality=EvidenceQuality.HIGH,
               unknowns=[], requires_additional_research=True), evidence())
    assert ResearchCoverageCode.COMPLETE_REQUIRES_MORE in codes(r)

def test_insufficient_without_more_research_fails():
    r = ResearchCoverageValidator().validate(
        output(research_status=ResearchStatus.INSUFFICIENT_EVIDENCE, evidence_quality=EvidenceQuality.LOW,
               requires_additional_research=False), evidence())
    assert ResearchCoverageCode.INSUFFICIENT_WITHOUT_MORE_RESEARCH in codes(r)

def test_unknown_rsi_conflicts_with_supplied_rsi():
    r = ResearchCoverageValidator().validate(output(unknowns=["RSI is unknown"]), evidence())
    assert ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT in codes(r)

def test_narrower_unknown_is_allowed():
    r = ResearchCoverageValidator().validate(output(unknowns=["Technical indicators beyond RSI are unknown"]), evidence())
    assert ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT not in codes(r)

def test_risk_like_contradiction_is_warning_not_error():
    r = ResearchCoverageValidator().validate(
        output(contradictory_evidence=["RSI14 is overbought and suggests correction risk"]), evidence())
    assert ResearchCoverageCode.CONTRADICTION_LOOKS_LIKE_RISK in codes(r)
    assert r.is_valid

def test_repair_instructions_are_constrained():
    r = ResearchCoverageValidator().validate(output(technical_context=None, event_context=None), evidence())
    text = r.repair_instructions()
    assert "using ONLY the supplied evidence" in text
    assert "Do not browse, invent, or add facts" in text
    assert "TECHNICAL_CONTEXT_MISSING" in text
    assert "EVENT_CONTEXT_MISSING" in text
