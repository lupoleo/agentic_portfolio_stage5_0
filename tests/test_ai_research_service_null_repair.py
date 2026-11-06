from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    ResearchStatus,
)
from app.ai.research_service import ResearchModelOutput, ResearchService


def _output(**updates):
    data = dict(
        research_status=ResearchStatus.PARTIAL,
        market_context="Market context",
        fundamental_context=None,
        technical_context="Technical context",
        event_context="Event context",
        catalyst_assessment="Catalyst",
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Bull",
        bear_case="Bear",
        key_risks=["Risk"],
        contradictory_evidence=[],
        unknowns=["Unknown"],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=0.7,
        requires_additional_research=True,
    )
    data.update(updates)
    return ResearchModelOutput(**data)


def test_null_repair_does_not_overwrite_non_nullable_boolean():
    previous = _output(requires_additional_research=True)
    schema = ResearchService._build_repair_output_schema(
        {"requires_additional_research"}
    )
    repair = schema.model_validate({"requires_additional_research": None})

    merged = ResearchService._merge_repair_output(
        previous,
        repair,
        {"requires_additional_research"},
    )

    assert merged.requires_additional_research is True


def test_null_repair_does_not_overwrite_non_nullable_enum():
    previous = _output(research_status=ResearchStatus.PARTIAL)
    schema = ResearchService._build_repair_output_schema({"research_status"})
    repair = schema.model_validate({"research_status": None})

    merged = ResearchService._merge_repair_output(
        previous,
        repair,
        {"research_status"},
    )

    assert merged.research_status == ResearchStatus.PARTIAL


def test_null_repair_can_clear_nullable_canonical_field():
    previous = _output(technical_context="Old technical context")
    schema = ResearchService._build_repair_output_schema({"technical_context"})
    repair = schema.model_validate({"technical_context": None})

    merged = ResearchService._merge_repair_output(
        previous,
        repair,
        {"technical_context"},
    )

    assert merged.technical_context is None


def test_non_null_repair_still_overwrites_boolean():
    previous = _output(requires_additional_research=True)
    schema = ResearchService._build_repair_output_schema(
        {"requires_additional_research"}
    )
    repair = schema.model_validate({"requires_additional_research": False})

    merged = ResearchService._merge_repair_output(
        previous,
        repair,
        {"requires_additional_research"},
    )

    assert merged.requires_additional_research is False


def test_omitted_repair_field_preserves_previous_value():
    previous = _output(requires_additional_research=True)
    schema = ResearchService._build_repair_output_schema(
        {"research_status", "requires_additional_research"}
    )
    repair = schema.model_validate({"research_status": ResearchStatus.PARTIAL})

    merged = ResearchService._merge_repair_output(
        previous,
        repair,
        {"research_status", "requires_additional_research"},
    )

    assert merged.requires_additional_research is True
