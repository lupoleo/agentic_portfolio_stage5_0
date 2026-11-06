from tools.batch_validate_opportunity_scoring_8c2 import (
    RunResult,
    evaluate_semantic_reproducibility_acceptance,
)


def run(i, status="PASS"):
    return RunResult(
        ticker="PATH",
        iteration=i,
        returncode=0 if status == "PASS" else 1,
        duration_seconds=1.0,
        timed_out=False,
        status=status,
        failure_class=None if status == "PASS" else "OTHER",
        stdout_file="x",
        stderr_file="y",
        variance_diagnostics={},
    )


def report(classification="TRUE_SEMANTIC_VARIANCE",
           thesis=5.0, catalyst=5.0, fundamental=5.0,
           technical=0.0, adjusted=1.5, nulls=None):
    nulls = nulls or []
    return {
        "pairs": [{
            "classification": classification,
            "null_transitions": nulls,
            "confidence_adjusted_delta": adjusted,
            "component_deltas": {
                "thesis": {"abs_delta": thesis},
                "catalyst": {"abs_delta": catalyst},
                "fundamental": {"abs_delta": fundamental},
                "technical": {"abs_delta": technical},
                "expectations": {"abs_delta": None},
            },
        }]
    }


def test_accepts_observed_frozen_envelope():
    result = evaluate_semantic_reproducibility_acceptance(
        [run(1), run(2)],
        report(fundamental=5.0, technical=0.0, adjusted=1.58),
    )
    assert result["accepted"] is True
    assert result["failures"] == []


def test_rejects_live_failure():
    result = evaluate_semantic_reproducibility_acceptance(
        [run(1, "FAIL"), run(2)],
        report(),
    )
    assert result["accepted"] is False
    assert "live_run_failures=1" in result["failures"]


def test_input_variance_is_not_semantic_failure():
    result = evaluate_semantic_reproducibility_acceptance(
        [run(1), run(2)],
        report(
            classification="INPUT_VARIANCE",
            thesis=50, catalyst=50, fundamental=50,
            technical=50, adjusted=20,
        ),
    )
    assert result["accepted"] is True


def test_rejects_fundamental_above_frozen_envelope():
    result = evaluate_semantic_reproducibility_acceptance(
        [run(1), run(2)],
        report(fundamental=7.5),
    )
    assert result["accepted"] is False
    assert any(
        item.startswith("fundamental_max_abs_delta=")
        for item in result["failures"]
    )


def test_rejects_confidence_adjusted_above_limit():
    result = evaluate_semantic_reproducibility_acceptance(
        [run(1), run(2)],
        report(adjusted=2.5),
    )
    assert result["accepted"] is False


def test_rejects_true_semantic_null_transition():
    result = evaluate_semantic_reproducibility_acceptance(
        [run(1), run(2)],
        report(nulls=["fundamental"]),
    )
    assert result["accepted"] is False
    assert "true_semantic_null_transitions=1" in result["failures"]


def test_rejects_missing_comparable_pair():
    result = evaluate_semantic_reproducibility_acceptance(
        [run(1), run(2)],
        report(classification="NOT_COMPARABLE"),
    )
    assert result["accepted"] is False
    assert "comparable_pairs=0/1" in result["failures"]


def test_technical_delta_of_five_is_inside_empirical_envelope():
    result = evaluate_semantic_reproducibility_acceptance(
        [run(1), run(2)],
        report(technical=5.0, adjusted=1.5),
    )
    assert result["accepted"] is True


def test_technical_delta_above_five_is_rejected():
    result = evaluate_semantic_reproducibility_acceptance(
        [run(1), run(2)],
        report(technical=7.5, adjusted=1.5),
    )
    assert result["accepted"] is False
    assert any(
        item.startswith("technical_max_abs_delta=")
        for item in result["failures"]
    )
