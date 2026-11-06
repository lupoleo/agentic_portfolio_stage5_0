from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.semantic_acceptance_replay import replay_acceptance


def _summary_row(ticker, iteration, status="PASS"):
    return {
        "ticker": ticker,
        "iteration": iteration,
        "returncode": 0 if status == "PASS" else 1,
        "duration_seconds": 1.0,
        "timed_out": False,
        "status": status,
        "failure_class": None if status == "PASS" else "TEST_FAILURE",
        "stdout_file": "x",
        "stderr_file": "y",
        "variance_diagnostics": {},
    }


def _variance_report(
    *,
    classification="TRUE_SEMANTIC_VARIANCE",
    fundamental=5.0,
    thesis=5.0,
    catalyst=5.0,
    technical=0.0,
    adjusted=1.5,
):
    return {
        "pairs": [
            {
                "ticker": "PATH",
                "classification": classification,
                "null_transitions": [],
                "confidence_adjusted_delta": adjusted,
                "component_deltas": {
                    "thesis": {"abs_delta": thesis},
                    "catalyst": {"abs_delta": catalyst},
                    "fundamental": {"abs_delta": fundamental},
                    "technical": {"abs_delta": technical},
                    "expectations": {"abs_delta": None},
                },
            }
        ]
    }


def _artifact(tmp_path: Path, summary, variance):
    (tmp_path / "summary.json").write_text(
        json.dumps(summary),
        encoding="utf-8",
    )
    (tmp_path / "semantic_variance.json").write_text(
        json.dumps(variance),
        encoding="utf-8",
    )
    return tmp_path


def test_replay_accepts_historical_batch_inside_envelope(tmp_path):
    artifact = _artifact(
        tmp_path,
        [_summary_row("PATH", 1), _summary_row("PATH", 2)],
        _variance_report(),
    )
    result = replay_acceptance(artifact)
    assert result["accepted"] is True
    assert result["replay"]["live_inference_performed"] is False


def test_replay_rejects_historical_live_failure(tmp_path):
    artifact = _artifact(
        tmp_path,
        [_summary_row("PATH", 1, "FAIL"), _summary_row("PATH", 2)],
        _variance_report(),
    )
    result = replay_acceptance(artifact)
    assert result["accepted"] is False
    assert "live_run_failures=1" in result["failures"]


def test_replay_rejects_semantic_envelope_violation(tmp_path):
    artifact = _artifact(
        tmp_path,
        [_summary_row("PATH", 1), _summary_row("PATH", 2)],
        _variance_report(fundamental=7.5),
    )
    result = replay_acceptance(artifact)
    assert result["accepted"] is False


def test_replay_accepts_input_variance_without_semantic_penalty(tmp_path):
    artifact = _artifact(
        tmp_path,
        [_summary_row("PATH", 1), _summary_row("PATH", 2)],
        _variance_report(
            classification="INPUT_VARIANCE",
            fundamental=50,
            thesis=50,
            catalyst=50,
            technical=50,
            adjusted=20,
        ),
    )
    result = replay_acceptance(artifact)
    assert result["accepted"] is True


def test_replay_fails_when_summary_missing(tmp_path):
    (tmp_path / "semantic_variance.json").write_text(
        json.dumps(_variance_report()),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="Required artifact not found"):
        replay_acceptance(tmp_path)


def test_replay_fails_when_variance_missing(tmp_path):
    (tmp_path / "summary.json").write_text(
        json.dumps([_summary_row("PATH", 1), _summary_row("PATH", 2)]),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="Required artifact not found"):
        replay_acceptance(tmp_path)


def test_replay_ignores_future_summary_fields(tmp_path):
    rows = [_summary_row("PATH", 1), _summary_row("PATH", 2)]
    rows[0]["future_field"] = "ignored"
    artifact = _artifact(tmp_path, rows, _variance_report())
    result = replay_acceptance(artifact)
    assert result["accepted"] is True
