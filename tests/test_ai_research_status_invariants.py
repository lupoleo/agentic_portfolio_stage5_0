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


def _output(status, *, requires_more):
    return ResearchModelOutput(
        research_status=status,
        market_context="market",
        fundamental_context="No revenue, earnings or margins were supplied.",
        technical_context="RSI14 72.94; SMA20 supplied; RVOL 0.88x.",
        event_context="Material deployment news was supplied.",
        catalyst_assessment="Deployment may affect positioning.",
        bull_case="Momentum and deployment support the bull case.",
        bear_case="Missing fundamentals and expectations limit confidence.",
        unknowns=["Revenue, earnings and margins are not provided."],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=0.70,
        requires_additional_research=requires_more,
    )


def _cov(*codes):
    return ResearchCoverageReport(tuple(
        ResearchCoverageIssue(
            code=code,
            severity=ResearchCoverageSeverity.ERROR,
            message=code.value,
        )
        for code in codes
    ))


def _sem(*codes, useful=True):
    return ResearchSemanticReport(
        issues=tuple(
            ResearchSemanticIssue(
                code=code,
                severity=ResearchSemanticSeverity.ERROR,
                message=code.value,
            )
            for code in codes
        ),
        useful_analysis=useful,
        supported_dimensions=("technical_context", "event_context"),
    )


def test_path_case_complete_with_material_unknowns_normalizes_to_partial():
    output, changed = ResearchService._normalize_repaired_status(
        _output(ResearchStatus.COMPLETE, requires_more=True),
        _cov(
            ResearchCoverageCode.COMPLETE_REQUIRES_MORE,
            ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS,
        ),
        _sem(ResearchSemanticCode.COMPLETE_WITH_MORE_RESEARCH),
    )
    assert changed is True
    assert output.research_status == ResearchStatus.PARTIAL
    assert output.requires_additional_research is True


def test_complete_material_unknown_normalizes_even_if_repair_left_requires_more_false():
    output, changed = ResearchService._normalize_repaired_status(
        _output(ResearchStatus.COMPLETE, requires_more=False),
        _cov(ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS),
        _sem(),
    )
    assert changed is True
    assert output.research_status == ResearchStatus.PARTIAL
    assert output.requires_additional_research is True


def test_normalization_preserves_research_content():
    original = _output(ResearchStatus.COMPLETE, requires_more=True)
    normalized, changed = ResearchService._normalize_repaired_status(
        original,
        _cov(
            ResearchCoverageCode.COMPLETE_REQUIRES_MORE,
            ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS,
        ),
        _sem(ResearchSemanticCode.COMPLETE_WITH_MORE_RESEARCH),
    )
    assert changed is True
    assert normalized.market_context == original.market_context
    assert normalized.fundamental_context == original.fundamental_context
    assert normalized.technical_context == original.technical_context
    assert normalized.event_context == original.event_context
    assert normalized.catalyst_assessment == original.catalyst_assessment
    assert normalized.bull_case == original.bull_case
    assert normalized.bear_case == original.bear_case
    assert normalized.unknowns == original.unknowns


def test_does_not_normalize_complete_without_material_unknown_error():
    output, changed = ResearchService._normalize_repaired_status(
        _output(ResearchStatus.COMPLETE, requires_more=True),
        _cov(ResearchCoverageCode.COMPLETE_REQUIRES_MORE),
        _sem(ResearchSemanticCode.COMPLETE_WITH_MORE_RESEARCH),
    )
    assert changed is False
    assert output.research_status == ResearchStatus.COMPLETE


def test_does_not_normalize_when_useful_analysis_false():
    output, changed = ResearchService._normalize_repaired_status(
        _output(ResearchStatus.COMPLETE, requires_more=True),
        _cov(
            ResearchCoverageCode.COMPLETE_REQUIRES_MORE,
            ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS,
        ),
        _sem(ResearchSemanticCode.COMPLETE_WITH_MORE_RESEARCH, useful=False),
    )
    assert changed is False


def test_does_not_normalize_when_unrelated_coverage_error_exists():
    output, changed = ResearchService._normalize_repaired_status(
        _output(ResearchStatus.COMPLETE, requires_more=True),
        _cov(
            ResearchCoverageCode.COMPLETE_REQUIRES_MORE,
            ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS,
            ResearchCoverageCode.TECHNICAL_CONTEXT_MISSING,
        ),
        _sem(ResearchSemanticCode.COMPLETE_WITH_MORE_RESEARCH),
    )
    assert changed is False


def test_does_not_normalize_when_unrelated_semantic_error_exists():
    output, changed = ResearchService._normalize_repaired_status(
        _output(ResearchStatus.COMPLETE, requires_more=True),
        _cov(
            ResearchCoverageCode.COMPLETE_REQUIRES_MORE,
            ResearchCoverageCode.COMPLETE_WITH_MATERIAL_UNKNOWNS,
        ),
        _sem(
            ResearchSemanticCode.COMPLETE_WITH_MORE_RESEARCH,
            ResearchSemanticCode.UNSUPPORTED_GENERALIZATION_RISK,
        ),
    )
    assert changed is False


def test_existing_7d34a_insufficient_case_still_normalizes():
    output, changed = ResearchService._normalize_repaired_status(
        _output(ResearchStatus.INSUFFICIENT_EVIDENCE, requires_more=True),
        ResearchCoverageReport(()),
        _sem(ResearchSemanticCode.USEFUL_ANALYSIS_MARKED_INSUFFICIENT),
    )
    assert changed is True
    assert output.research_status == ResearchStatus.PARTIAL
    assert output.requires_additional_research is True
