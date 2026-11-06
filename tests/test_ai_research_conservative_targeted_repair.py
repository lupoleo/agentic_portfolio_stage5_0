from app.ai.research_models import EvidenceQuality, ExpectationsAssessment, ResearchStatus
from app.ai.research_semantics import (
    ResearchSemanticCode, ResearchSemanticIssue, ResearchSemanticReport,
    ResearchSemanticSeverity,
)
from app.ai.research_service import ResearchModelOutput, ResearchService
from app.ai.research_validator import (
    ResearchCoverageCode, ResearchCoverageIssue, ResearchCoverageReport,
    ResearchCoverageSeverity,
)


def _output(**overrides):
    data = dict(
        research_status=ResearchStatus.COMPLETE,
        market_context="market",
        fundamental_context="fundamental",
        technical_context="technical",
        event_context="event",
        catalyst_assessment="catalyst",
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="bull",
        bear_case="bear",
        key_risks=["risk"],
        contradictory_evidence=["tension"],
        unknowns=["Technical volatility is unknown."],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=0.7,
        requires_additional_research=False,
    )
    data.update(overrides)
    return ResearchModelOutput(**data)


def _coverage(code):
    return ResearchCoverageReport((
        ResearchCoverageIssue(
            code=code,
            severity=ResearchCoverageSeverity.ERROR,
            message="x",
        ),
    ))


def _semantic(*issues):
    return ResearchSemanticReport(
        issues=tuple(issues),
        useful_analysis=True,
        supported_dimensions=("technical_context", "event_context"),
    )


def test_material_unknown_repair_can_only_change_status_pair():
    fields = ResearchService._repairable_fields(
        _coverage(ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS),
        _semantic(),
    )
    assert fields == {"research_status", "requires_additional_research"}


def test_missing_technical_context_allows_only_technical_context():
    fields = ResearchService._repairable_fields(
        _coverage(ResearchCoverageCode.TECHNICAL_CONTEXT_MISSING),
        _semantic(),
    )
    assert fields == {"technical_context"}


def test_semantic_insufficient_repair_can_only_change_status_pair():
    issue = ResearchSemanticIssue(
        code=ResearchSemanticCode.USEFUL_ANALYSIS_MARKED_INSUFFICIENT,
        severity=ResearchSemanticSeverity.ERROR,
        message="x",
    )
    fields = ResearchService._repairable_fields(
        ResearchCoverageReport(()),
        _semantic(issue),
    )
    assert fields == {"research_status", "requires_additional_research"}


def test_conservative_merge_preserves_valid_research_when_llm_blanks_it():
    previous = _output()
    destructive = _output(
        research_status=ResearchStatus.PARTIAL,
        market_context=None,
        fundamental_context=None,
        technical_context=None,
        event_context=None,
        catalyst_assessment=None,
        bull_case=None,
        bear_case=None,
        key_risks=[],
        contradictory_evidence=[],
        requires_additional_research=True,
    )
    merged = ResearchService._merge_repair_output(
        previous,
        destructive,
        {"research_status", "requires_additional_research"},
    )
    assert merged.research_status == ResearchStatus.PARTIAL
    assert merged.requires_additional_research is True
    assert merged.market_context == "market"
    assert merged.fundamental_context == "fundamental"
    assert merged.technical_context == "technical"
    assert merged.event_context == "event"
    assert merged.catalyst_assessment == "catalyst"
    assert merged.bull_case == "bull"
    assert merged.bear_case == "bear"
    assert merged.key_risks == ["risk"]
    assert merged.contradictory_evidence == ["tension"]


def test_targeted_context_repair_does_not_modify_other_fields():
    previous = _output(technical_context=None)
    candidate = _output(
        technical_context="repaired technical",
        market_context="LLM rewrote market",
        fundamental_context=None,
    )
    merged = ResearchService._merge_repair_output(
        previous, candidate, {"technical_context"}
    )
    assert merged.technical_context == "repaired technical"
    assert merged.market_context == "market"
    assert merged.fundamental_context == "fundamental"


def test_unknown_conflict_repair_can_only_change_unknowns():
    fields = ResearchService._repairable_fields(
        _coverage(ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT),
        _semantic(),
    )
    assert fields == {"unknowns"}
