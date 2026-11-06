from types import SimpleNamespace

from tools.live_opportunity_scoring_8c2 import (
    _canonical_research_semantic_input,
    _research_semantic_fingerprint,
)
from tools.batch_validate_opportunity_scoring_8c2 import RunResult, compare_pair


class EnumValue:
    def __init__(self, value):
        self.value = value


def research(fundamental="Revenue grew 20%.", unknowns=None):
    return SimpleNamespace(
        research_status=EnumValue("PARTIAL"),
        market_context=" Market context. ",
        fundamental_context=fundamental,
        technical_context="Technical.",
        event_context="Event.",
        catalyst_assessment="Catalyst.",
        expectations_assessment=EnumValue("UNKNOWN"),
        bull_case="Bull.",
        bear_case="Bear.",
        key_risks=[" Risk "],
        contradictory_evidence=[],
        unknowns=unknowns or [],
        evidence_quality=EnumValue("HIGH"),
        research_confidence=.7,
        requires_additional_research=True,
    )


def diag(fingerprint, semantic_input):
    return {
        "selected_evidence_ids": ["E1"],
        "required_context_fields": ["fundamental_context"],
        "final_missing_required_contexts": [],
        "evidence_semantic_dimensions": {"E1": ["FUNDAMENTAL"]},
        "scorability": {
            "fundamental": {
                "context_present": True,
                "deterministic_scorable": True,
                "final_null": False,
            }
        },
        "research_semantic_fingerprint": fingerprint,
        "research_semantic_input": semantic_input,
        "components": {
            "thesis": {"score": 60},
            "catalyst": {"score": 55},
            "fundamental": {"score": 60},
            "technical": {"score": 50},
            "expectations": {"score": None},
        },
        "raw_score": 57,
        "confidence_adjusted_score": 53,
    }


def run(iteration, d):
    return RunResult(
        ticker="MRVL",
        iteration=iteration,
        returncode=0,
        duration_seconds=1,
        timed_out=False,
        status="PASS",
        failure_class=None,
        stdout_file="x",
        stderr_file="y",
        variance_diagnostics=d,
    )


def test_semantic_fingerprint_ignores_whitespace_only_changes():
    a = _canonical_research_semantic_input(
        research(" Revenue   grew 20%. ")
    )
    b = _canonical_research_semantic_input(
        research("Revenue grew 20%.")
    )
    assert _research_semantic_fingerprint(a) == _research_semantic_fingerprint(b)


def test_semantic_fingerprint_changes_when_unknowns_change():
    a = _canonical_research_semantic_input(research(unknowns=[]))
    b = _canonical_research_semantic_input(
        research(unknowns=["Margins are unknown"])
    )
    assert _research_semantic_fingerprint(a) != _research_semantic_fingerprint(b)


def test_pair_is_input_variance_when_research_semantics_differ():
    a_input = _canonical_research_semantic_input(research(unknowns=[]))
    b_input = _canonical_research_semantic_input(
        research(unknowns=["Margins are unknown"])
    )
    a = diag(_research_semantic_fingerprint(a_input), a_input)
    b = diag(_research_semantic_fingerprint(b_input), b_input)
    b["components"]["fundamental"]["score"] = 70

    pair = compare_pair(run(1, a), run(2, b))

    assert pair["classification"] == "INPUT_VARIANCE"
    assert "unknowns" in pair["research_semantic_changed_fields"]


def test_same_research_semantics_different_score_is_true_semantic_variance():
    semantic_input = _canonical_research_semantic_input(research())
    fingerprint = _research_semantic_fingerprint(semantic_input)
    a = diag(fingerprint, semantic_input)
    b = diag(fingerprint, semantic_input)
    b["components"]["fundamental"]["score"] = 65

    pair = compare_pair(run(1, a), run(2, b))

    assert pair["classification"] == "TRUE_SEMANTIC_VARIANCE"
