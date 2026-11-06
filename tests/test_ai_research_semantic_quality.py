from dataclasses import dataclass

from app.ai.research_models import EvidenceQuality, ResearchStatus
from app.ai.research_semantics import (
    ResearchSemanticCode,
    ResearchSemanticQualityEvaluator,
)


@dataclass
class Evidence:
    evidence_id: str
    source_type: str
    text: str


@dataclass
class Output:
    research_status: ResearchStatus = ResearchStatus.PARTIAL
    market_context: str | None = None
    fundamental_context: str | None = None
    technical_context: str | None = None
    event_context: str | None = None
    catalyst_assessment: str | None = None
    bull_case: str | None = None
    bear_case: str | None = None
    key_risks: list[str] = None
    contradictory_evidence: list[str] = None
    unknowns: list[str] = None
    evidence_quality: EvidenceQuality = EvidenceQuality.MEDIUM
    research_confidence: float = 0.6
    requires_additional_research: bool = True

    def __post_init__(self):
        self.key_risks = self.key_risks or []
        self.contradictory_evidence = self.contradictory_evidence or []
        self.unknowns = self.unknowns or []


def evidence():
    return [
        Evidence("M1", "MARKET", "PATH close 18.64, RSI14 74.70, RVOL 0.80x."),
        Evidence("N1", "NEWS", "UiPath announced a Banco Azteca deployment."),
    ]


def useful_output(**changes):
    data = dict(
        research_status=ResearchStatus.PARTIAL,
        technical_context="PATH close 18.64; RSI14 74.70; RVOL 0.80x.",
        event_context="UiPath announced a Banco Azteca deployment.",
        catalyst_assessment="The deployment could affect positioning.",
        bull_case="Momentum and the deployment could support interest.",
        bear_case="Overbought momentum could reverse.",
        key_risks=["Momentum reversal"],
        unknowns=["Financial impact is not quantified."],
        evidence_quality=EvidenceQuality.LOW,
        requires_additional_research=True,
    )
    data.update(changes)
    return Output(**data)


def codes(report):
    return {x.code for x in report.issues}


def test_partial_useful_research_is_valid():
    report = ResearchSemanticQualityEvaluator().evaluate(useful_output(), evidence())
    assert report.is_valid
    assert report.useful_analysis


def test_useful_research_must_not_be_called_insufficient():
    report = ResearchSemanticQualityEvaluator().evaluate(
        useful_output(research_status=ResearchStatus.INSUFFICIENT_EVIDENCE),
        evidence(),
    )
    assert ResearchSemanticCode.USEFUL_ANALYSIS_MARKED_INSUFFICIENT in codes(report)
    assert not report.is_valid


def test_sparse_research_can_remain_insufficient():
    output = Output(
        research_status=ResearchStatus.INSUFFICIENT_EVIDENCE,
        event_context="A deployment was announced.",
        unknowns=["Financial impact unknown."],
        evidence_quality=EvidenceQuality.LOW,
        requires_additional_research=True,
    )
    report = ResearchSemanticQualityEvaluator().evaluate(output, evidence())
    assert report.is_valid
    assert not report.useful_analysis


def test_partial_requires_more_research():
    report = ResearchSemanticQualityEvaluator().evaluate(
        useful_output(requires_additional_research=False), evidence()
    )
    assert ResearchSemanticCode.PARTIAL_WITHOUT_MORE_RESEARCH in codes(report)


def test_complete_cannot_require_more_research():
    report = ResearchSemanticQualityEvaluator().evaluate(
        useful_output(
            research_status=ResearchStatus.COMPLETE,
            evidence_quality=EvidenceQuality.HIGH,
            requires_additional_research=True,
        ),
        evidence(),
    )
    assert ResearchSemanticCode.COMPLETE_WITH_MORE_RESEARCH in codes(report)


def test_complete_cannot_be_low_quality():
    report = ResearchSemanticQualityEvaluator().evaluate(
        useful_output(
            research_status=ResearchStatus.COMPLETE,
            evidence_quality=EvidenceQuality.LOW,
            requires_additional_research=False,
        ),
        evidence(),
    )
    assert ResearchSemanticCode.LOW_QUALITY_COMPLETE in codes(report)


def test_generic_risk_in_contradictions_is_warning_only():
    report = ResearchSemanticQualityEvaluator().evaluate(
        useful_output(contradictory_evidence=["RSI is overbought."]),
        evidence(),
    )
    assert ResearchSemanticCode.CONTRADICTION_LOOKS_LIKE_RISK in codes(report)
    assert report.is_valid


def test_genuine_tension_is_not_flagged_as_generic_risk():
    report = ResearchSemanticQualityEvaluator().evaluate(
        useful_output(
            contradictory_evidence=[
                "Momentum is strong while RSI is overbought."
            ]
        ),
        evidence(),
    )
    assert ResearchSemanticCode.CONTRADICTION_LOOKS_LIKE_RISK not in codes(report)


def test_broad_unanchored_market_claim_is_warning_only():
    report = ResearchSemanticQualityEvaluator().evaluate(
        useful_output(
            market_context="Institutions are broadly accumulating enterprise software."
        ),
        evidence(),
    )
    assert ResearchSemanticCode.UNSUPPORTED_GENERALIZATION_RISK in codes(report)
    assert report.is_valid
