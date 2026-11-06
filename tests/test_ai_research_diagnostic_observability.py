from app.ai.research_service import (
    ResearchCoverageValidationError,
    ResearchModelOutput,
)
from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    ResearchStatus,
)
from app.ai.research_semantics import ResearchSemanticReport
from app.ai.research_validator import ResearchCoverageReport


def _output():
    return ResearchModelOutput(
        research_status=ResearchStatus.PARTIAL,
        technical_context="RSI14 is 70.",
        event_context="A company event was supplied.",
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Bull case.",
        bear_case="Bear case.",
        key_risks=["Risk"],
        unknowns=["Fundamental impact is unknown."],
        evidence_quality=EvidenceQuality.LOW,
        research_confidence=0.5,
        requires_additional_research=True,
    )


def test_validation_error_preserves_legacy_constructor_contract():
    initial = ResearchCoverageReport(issues=[])
    final = ResearchCoverageReport(issues=[])

    exc = ResearchCoverageValidationError(
        "invalid",
        initial_report=initial,
        final_report=final,
        inference_ids=["AI-1", "AI-2"],
    )

    assert exc.initial_report is initial
    assert exc.final_report is final
    assert exc.inference_ids == ["AI-1", "AI-2"]
    assert exc.initial_semantic_report is None
    assert exc.final_semantic_report is None
    assert exc.initial_output is None
    assert exc.final_output is None


def test_validation_error_retains_diagnostic_payload():
    initial = ResearchCoverageReport(issues=[])
    final = ResearchCoverageReport(issues=[])
    initial_semantic = ResearchSemanticReport(
        issues=[],
        useful_analysis=True,
        supported_dimensions=["technical_context", "event_context"],
    )
    final_semantic = ResearchSemanticReport(
        issues=[],
        useful_analysis=True,
        supported_dimensions=["technical_context", "event_context"],
    )
    initial_output = _output()
    final_output = _output()

    exc = ResearchCoverageValidationError(
        "invalid",
        initial_report=initial,
        final_report=final,
        inference_ids=["AI-1", "AI-2"],
        initial_semantic_report=initial_semantic,
        final_semantic_report=final_semantic,
        initial_output=initial_output,
        final_output=final_output,
    )

    assert exc.initial_semantic_report is initial_semantic
    assert exc.final_semantic_report is final_semantic
    assert exc.initial_output is initial_output
    assert exc.final_output is final_output
