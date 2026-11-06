from tools.batch_validate_opportunity_scoring_8c2 import (
    RunResult, compare_pair, parse_variance_diagnostics, build_variance_report
)


def diag(score=55, evidence=None, scorable=True):
    return {
        "ticker": "PATH",
        "selected_evidence_ids": evidence or ["E1", "E2"],
        "required_context_fields": ["market_context"],
        "final_missing_required_contexts": [],
        "evidence_semantic_dimensions": {"E1": ["FUNDAMENTAL"]},
        "components": {
            "thesis": {"score": score, "supporting_evidence_ids": ["E1"]},
            "catalyst": {"score": 50, "supporting_evidence_ids": ["E2"]},
            "fundamental": {"score": 60, "supporting_evidence_ids": ["E1"]},
            "technical": {"score": 37.5, "supporting_evidence_ids": ["E2"]},
            "expectations": {"score": None, "supporting_evidence_ids": []},
        },
        "scorability": {"thesis": {"context_present": True, "deterministic_scorable": scorable, "final_null": False}},
        "raw_score": 52.0,
        "confidence_adjusted_score": 51.2,
    }


def run(i, d):
    return RunResult("PATH", i, 0, 1.0, False, "PASS", None, "x", "y", d)


def test_parse_boundary():
    s = '=== AI-8C.3d.1 VARIANCE JSON ===\n{"ticker":"PATH"}\n=== END AI-8C.3d.1 VARIANCE JSON ==='
    assert parse_variance_diagnostics(s)["ticker"] == "PATH"


def test_stable_pair():
    assert compare_pair(run(1, diag()), run(2, diag()))["classification"] == "STABLE"


def test_true_semantic_variance():
    p = compare_pair(run(1, diag(55)), run(2, diag(65)))
    assert p["classification"] == "TRUE_SEMANTIC_VARIANCE"
    assert p["component_deltas"]["thesis"]["delta"] == 10.0


def test_input_variance_on_evidence():
    p = compare_pair(run(1, diag(evidence=["E1"])), run(2, diag(evidence=["E1", "E2"])))
    assert p["classification"] == "INPUT_VARIANCE"


def test_input_variance_on_scorability():
    assert compare_pair(run(1, diag(scorable=True)), run(2, diag(scorable=False)))["classification"] == "INPUT_VARIANCE"


def test_null_transition():
    a, b = diag(), diag()
    b["components"]["thesis"]["score"] = None
    assert "thesis" in compare_pair(run(1, a), run(2, b))["null_transitions"]


def test_aggregate_delta():
    report = build_variance_report([run(1, diag(55)), run(2, diag(65))])
    assert report["components"]["thesis"]["mean_abs_delta"] == 10.0
    assert report["components"]["thesis"]["max_abs_delta"] == 10.0
