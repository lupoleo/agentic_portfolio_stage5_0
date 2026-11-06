from app.ai.research_service import (
    ResearchCoverageValidationError,
    ResearchModelOutput,
)
from app.ai.research_models import EvidenceQuality, ResearchStatus
from app.ai.research_validator import ResearchCoverageReport
from app.ai.research_semantics import ResearchSemanticReport


def _output(status=ResearchStatus.PARTIAL):
    return ResearchModelOutput(
        research_status=status,
        evidence_quality=EvidenceQuality.LOW,
        research_confidence=0.5,
        requires_additional_research=True,
    )


def test_validation_error_preserves_diagnostic_payload():
    coverage = ResearchCoverageReport(())
    semantic = ResearchSemanticReport(
        issues=(),
        useful_analysis=False,
        supported_dimensions=(),
    )
    initial = _output()
    final = _output()

    exc = ResearchCoverageValidationError(
        "invalid",
        initial_report=coverage,
        final_report=coverage,
        inference_ids=["AI-1", "AI-2"],
        initial_semantic_report=semantic,
        final_semantic_report=semantic,
        initial_output=initial,
        final_output=final,
    )

    assert exc.initial_report is coverage
    assert exc.final_report is coverage
    assert exc.initial_semantic_report is semantic
    assert exc.final_semantic_report is semantic
    assert exc.initial_output is initial
    assert exc.final_output is final
    assert exc.inference_ids == ["AI-1", "AI-2"]


def test_diagnostic_attributes_are_backward_compatible_optional():
    coverage = ResearchCoverageReport(())
    exc = ResearchCoverageValidationError(
        "invalid",
        initial_report=coverage,
        final_report=coverage,
        inference_ids=[],
    )
    assert exc.initial_semantic_report is None
    assert exc.final_semantic_report is None
    assert exc.initial_output is None
    assert exc.final_output is None
