from types import SimpleNamespace

from app.ai.research_models import (
    EvidenceQuality, ExpectationsAssessment, ResearchStatus,
)
from app.ai.research_service import (
    ResearchEvidence, ResearchModelOutput, ResearchService,
)


def ev(eid, text):
    return ResearchEvidence(evidence_id=eid, source_type="NEWS", text=text)


def sem(eid, *dims):
    return SimpleNamespace(
        evidence_id=eid,
        dimensions=[SimpleNamespace(value=d) for d in dims],
    )


def previous():
    return ResearchModelOutput(
        research_status=ResearchStatus.PARTIAL,
        market_context="market " * 500,
        fundamental_context="fundamental " * 500,
        technical_context=None,
        event_context=None,
        catalyst_assessment="catalyst " * 500,
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="bull " * 500,
        bear_case="bear " * 500,
        key_risks=["risk"] * 20,
        contradictory_evidence=[],
        unknowns=["unknown"] * 20,
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=0.7,
        requires_additional_research=True,
    )


def test_technical_repair_selects_price_technical_only():
    evidence = [ev("M1", "market"), ev("N1", "event"), ev("F1", "fund")]
    assessments = [
        sem("M1", "PRICE_TECHNICAL"),
        sem("N1", "CATALYST_EVENT", "NEWS_CONTEXT"),
        sem("F1", "FUNDAMENTAL"),
    ]
    result = ResearchService._scope_repair_evidence(
        evidence, {"technical_context"}, evidence_semantics=assessments
    )
    assert [x.evidence_id for x in result] == ["M1"]


def test_event_repair_selects_event_news_and_analyst():
    evidence = [
        ev("M1", "market"), ev("N1", "event"),
        ev("A1", "analyst"), ev("F1", "fund"),
    ]
    assessments = [
        sem("M1", "PRICE_TECHNICAL"),
        sem("N1", "CATALYST_EVENT", "NEWS_CONTEXT"),
        sem("A1", "ANALYST_EXPECTATIONS"),
        sem("F1", "FUNDAMENTAL"),
    ]
    result = ResearchService._scope_repair_evidence(
        evidence, {"event_context"}, evidence_semantics=assessments
    )
    assert [x.evidence_id for x in result] == ["N1", "A1"]


def test_missing_semantics_keeps_all_evidence():
    evidence = [ev("M1", "market"), ev("N1", "event")]
    assert ResearchService._scope_repair_evidence(
        evidence, {"event_context"}, evidence_semantics=[]
    ) == evidence


def test_status_only_repair_has_no_raw_evidence():
    evidence = [ev("M1", "market"), ev("N1", "event")]
    assert ResearchService._scope_repair_evidence(
        evidence,
        {"research_status", "requires_additional_research"},
        evidence_semantics=[
            sem("M1", "PRICE_TECHNICAL"),
            sem("N1", "NEWS_CONTEXT"),
        ],
    ) == []


def test_zero_semantic_match_falls_back_to_all_evidence():
    evidence = [ev("F1", "fund")]
    assert ResearchService._scope_repair_evidence(
        evidence,
        {"technical_context"},
        evidence_semantics=[sem("F1", "FUNDAMENTAL")],
    ) == evidence


def test_previous_output_keeps_target_not_long_protected_prose():
    block = ResearchService._scoped_previous_output(
        previous(), {"event_context", "research_status"}
    )
    assert '"event_context": null' in block
    assert '"research_status"' in block
    assert ("fundamental " * 20) not in block
    assert ("bull " * 20) not in block


def test_previous_output_is_materially_smaller():
    item = previous()
    scoped = ResearchService._scoped_previous_output(
        item, {"event_context", "research_status"}
    )
    assert len(scoped) < len(item.model_dump_json(indent=2)) * 0.20
