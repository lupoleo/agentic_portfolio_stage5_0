from app.ai.research_models import EvidenceQuality, ResearchStatus
from app.ai.research_semantics import (
    ResearchSemanticCode,
    ResearchSemanticIssue,
    ResearchSemanticReport,
    ResearchSemanticSeverity,
)
from app.ai.research_service import ResearchModelOutput, ResearchService
from app.ai.research_validator import (
    ResearchCoverageCode,
    ResearchCoverageIssue,
    ResearchCoverageReport,
    ResearchCoverageSeverity,
)


def _output(status=ResearchStatus.INSUFFICIENT_EVIDENCE, requires_more=True):
    return ResearchModelOutput(
        research_status=status,
        market_context="market",
        fundamental_context="fundamental",
        technical_context="technical",
        event_context="event",
        catalyst_assessment="catalyst",
        bull_case="bull",
        bear_case="bear",
        key_risks=["risk"],
        unknowns=["Technical volatility is unknown."],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=0.7,
        requires_additional_research=requires_more,
    )


def _useful_insufficient_semantic():
    return ResearchSemanticReport(
        issues=(
            ResearchSemanticIssue(
                code=ResearchSemanticCode.USEFUL_ANALYSIS_MARKED_INSUFFICIENT,
                severity=ResearchSemanticSeverity.ERROR,
                message="Useful analysis was marked insufficient.",
            ),
        ),
        useful_analysis=True,
        supported_dimensions=("technical_context", "event_context"),
    )


def test_normalizes_useful_insufficient_to_partial_without_second_llm_repair():
    output, changed = ResearchService._normalize_repaired_status(
        _output(),
        ResearchCoverageReport(()),
        _useful_insufficient_semantic(),
    )
    assert changed is True
    assert output.research_status == ResearchStatus.PARTIAL
    assert output.requires_additional_research is True
    assert output.market_context == "market"
    assert output.technical_context == "technical"
    assert output.event_context == "event"


def test_does_not_normalize_when_coverage_still_invalid():
    coverage = ResearchCoverageReport((
        ResearchCoverageIssue(
            code=ResearchCoverageCode.TECHNICAL_CONTEXT_MISSING,
            severity=ResearchCoverageSeverity.ERROR,
            message="x",
        ),
    ))
    output, changed = ResearchService._normalize_repaired_status(
        _output(), coverage, _useful_insufficient_semantic()
    )
    assert changed is False
    assert output.research_status == ResearchStatus.INSUFFICIENT_EVIDENCE


def test_does_not_normalize_unrelated_semantic_failure():
    semantic = ResearchSemanticReport(
        issues=(
            ResearchSemanticIssue(
                code=ResearchSemanticCode.PARTIAL_WITHOUT_MORE_RESEARCH,
                severity=ResearchSemanticSeverity.ERROR,
                message="x",
            ),
        ),
        useful_analysis=True,
        supported_dimensions=("technical_context", "event_context"),
    )
    output, changed = ResearchService._normalize_repaired_status(
        _output(), ResearchCoverageReport(()), semantic
    )
    assert changed is False


def test_does_not_normalize_when_semantic_report_has_multiple_errors():
    semantic = ResearchSemanticReport(
        issues=(
            *_useful_insufficient_semantic().issues,
            ResearchSemanticIssue(
                code=ResearchSemanticCode.LOW_QUALITY_COMPLETE,
                severity=ResearchSemanticSeverity.ERROR,
                message="x",
            ),
        ),
        useful_analysis=True,
        supported_dimensions=("technical_context", "event_context"),
    )
    output, changed = ResearchService._normalize_repaired_status(
        _output(), ResearchCoverageReport(()), semantic
    )
    assert changed is False


def test_does_not_normalize_when_useful_analysis_false():
    semantic = ResearchSemanticReport(
        issues=_useful_insufficient_semantic().issues,
        useful_analysis=False,
        supported_dimensions=(),
    )
    output, changed = ResearchService._normalize_repaired_status(
        _output(), ResearchCoverageReport(()), semantic
    )
    assert changed is False
