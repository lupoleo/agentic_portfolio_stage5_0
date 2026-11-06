from types import SimpleNamespace

from app.ai.research_models import EvidenceQuality, ExpectationsAssessment, ResearchStatus
from app.ai.research_service import ResearchModelOutput, ResearchService


def output(expectations=ExpectationsAssessment.UNKNOWN):
    return ResearchModelOutput(
        research_status=ResearchStatus.PARTIAL,
        market_context="Market context.",
        fundamental_context="Fundamental context.",
        technical_context="Technical context.",
        event_context="Analyst expectations are available.",
        catalyst_assessment="A catalyst is identified.",
        expectations_assessment=expectations,
        bull_case="Bull case.",
        bear_case="Bear case.",
        key_risks=["Execution risk."],
        contradictory_evidence=[],
        unknowns=[],
        evidence_quality=EvidenceQuality.MEDIUM,
        research_confidence=0.6,
        requires_additional_research=True,
    )


def semantics():
    return [SimpleNamespace(dimensions={"ANALYST_EXPECTATIONS"})]


def item(scorable):
    evidence = SimpleNamespace(metadata={"expectations_scorable": scorable})
    return SimpleNamespace(evidence=evidence)


def test_scorable_analyst_evidence_requires_expectations_assessment():
    required = ResearchService._required_context_fields(
        semantics(), evidence_items=[item(True)],
    )
    assert "event_context" in required
    assert "expectations_assessment" in required
    assert ResearchService._missing_required_context_fields(
        output(), required,
    ) == {"expectations_assessment"}


def test_grounded_expectations_satisfy_context_presence_gate():
    required = ResearchService._required_context_fields(
        semantics(), evidence_items=[item(True)],
    )
    assert not ResearchService._missing_required_context_fields(
        output(ExpectationsAssessment.LARGELY_PRICED_IN), required,
    )


def test_unscorable_analyst_evidence_preserves_unknown_semantics():
    required = ResearchService._required_context_fields(
        semantics(), evidence_items=[item(False)],
    )
    assert required == {"event_context"}
    assert not ResearchService._missing_required_context_fields(
        output(), required,
    )


def test_expectations_presence_repair_does_not_authorize_governance_rewrite():
    fields = ResearchService._context_presence_repairable_fields({
        "expectations_assessment",
    })
    assert fields == {"expectations_assessment"}
    assert "research_status" not in fields
    assert "requires_additional_research" not in fields


def test_expectations_repair_instructions_preserve_fail_closed_policy():
    instructions = ResearchService._context_presence_repair_instructions({
        "expectations_assessment",
    })
    assert "explicit analyst/current-price comparisons" in instructions
    assert "does not authorize promotion" in instructions
    assert "independent deterministic validator" in instructions
