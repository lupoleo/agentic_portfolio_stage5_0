from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    ResearchStatus,
)
from app.ai.research_service import (
    ResearchEvidence,
    ResearchModelOutput,
    ResearchService,
)


def _evidence(evidence_id: str, text: str) -> ResearchEvidence:
    return ResearchEvidence(
        evidence_id=evidence_id,
        source_type="NEWS",
        text=text,
    )


def _output() -> ResearchModelOutput:
    return ResearchModelOutput(
        research_status=ResearchStatus.COMPLETE,
        market_context="market market market",
        fundamental_context="fundamental fundamental fundamental",
        technical_context="technical technical technical",
        event_context="event event event",
        catalyst_assessment="catalyst catalyst catalyst",
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="bull bull bull",
        bear_case="bear bear bear",
        key_risks=["risk"],
        contradictory_evidence=[],
        unknowns=["material missing evidence"],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=0.8,
        requires_additional_research=True,
    )


def test_status_only_repair_has_no_raw_evidence():
    evidence = [_evidence("E1", "raw evidence")]
    assert ResearchService._scope_repair_evidence(
        evidence,
        {"research_status", "requires_additional_research"},
    ) == []


def test_content_repair_without_semantics_keeps_complete_evidence_set():
    evidence = [
        _evidence("E1", "technical fact"),
        _evidence("E2", "event fact"),
    ]
    assert ResearchService._scope_repair_evidence(
        evidence,
        {"technical_context"},
    ) == evidence


def test_status_only_previous_output_is_compact():
    block = ResearchService._scoped_previous_output(
        _output(),
        {"research_status", "requires_additional_research"},
    )
    assert "has_technical_context" in block
    assert "has_event_context" in block
    assert "technical technical technical" not in block
    assert "material missing evidence" not in block


def test_content_repair_keeps_target_and_compact_dependency_state():
    block = ResearchService._scoped_previous_output(
        _output(),
        {"technical_context"},
    )
    # 3c contract: exact previous value of the repair target remains available.
    assert "technical technical technical" in block
    # Governance/dependency state remains available.
    assert "has_technical_context" in block
    assert "has_event_context" in block
    assert '"research_status": "COMPLETE"' in block
    # Unrelated long-form protected content is intentionally not repeated.
    assert "material missing evidence" not in block
    assert "fundamental fundamental fundamental" not in block
    assert "bull bull bull" not in block


def test_null_nonnullable_repair_value_preserves_previous_value():
    previous = _output()
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
