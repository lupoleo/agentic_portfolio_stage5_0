from app.ai.research_service import ResearchEvidence, ResearchModelOutput, ResearchService
from app.ai.research_models import EvidenceQuality, ExpectationsAssessment, ResearchStatus
from app.ai.research_semantics import (
    ResearchSemanticCode,
    ResearchSemanticIssue,
    ResearchSemanticReport,
    ResearchSemanticSeverity,
)
from app.ai.research_validator import (
    ResearchCoverageCode,
    ResearchCoverageValidator,
)


def _output(*, unknowns):
    return ResearchModelOutput(
        research_status=ResearchStatus.PARTIAL,
        technical_context="PATH RSI14 is 72.94 and 20-session return is 43.07%.",
        event_context="Analysts have Hold ratings on UiPath.",
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Supported bull case.",
        bear_case="Supported bear case.",
        key_risks=["Risk"],
        unknowns=unknowns,
        evidence_quality=EvidenceQuality.LOW,
        research_confidence=0.7,
        requires_additional_research=True,
    )


def _evidence():
    return [
        ResearchEvidence(
            evidence_id="M1",
            source_type="MARKET",
            text="PATH 20-session return is 43.07%; RSI14 is 72.94.",
        ),
        ResearchEvidence(
            evidence_id="N1",
            source_type="NEWS",
            text="Analysts have Hold ratings on UiPath.",
        ),
    ]


def test_impact_of_supplied_fact_can_remain_unknown():
    report = ResearchCoverageValidator().validate(
        _output(unknowns=[
            "The impact of analyst Holds on future price performance is not established."
        ]),
        _evidence(),
    )
    assert ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT not in {
        issue.code for issue in report.errors
    }


def test_future_price_effect_of_known_return_can_remain_unknown():
    report = ResearchCoverageValidator().validate(
        _output(unknowns=[
            "The future price reaction to the recent price performance is unknown."
        ]),
        _evidence(),
    )
    assert ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT not in {
        issue.code for issue in report.errors
    }


def test_plain_supplied_technical_fact_still_cannot_be_called_unknown():
    report = ResearchCoverageValidator().validate(
        _output(unknowns=["RSI is unknown."]),
        _evidence(),
    )
    assert ResearchCoverageCode.UNKNOWN_CONTRADICTS_SUPPLIED_FACT in {
        issue.code for issue in report.errors
    }


def test_semantic_repair_instructions_prefer_partial_for_useful_incomplete_analysis():
    report = ResearchSemanticReport(
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
    text = ResearchService._semantic_repair_instructions(report)
    assert "use PARTIAL" in text
    assert "requires_additional_research=true" in text
    assert "Do not downgrade useful supported analysis" in text
