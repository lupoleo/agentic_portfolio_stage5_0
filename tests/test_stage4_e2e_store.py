from datetime import datetime, timedelta, timezone

import pytest

from app.e2e.stage4_contracts import (
    Stage4E2ERequest,
    Stage4E2ERun,
    Stage4Mode,
    Stage4RunStatus,
    Stage4Stage,
    Stage4StageRecord,
    Stage4StageStatus,
    Stage4TerminalReason,
    canonical_fingerprint,
    stage4_request_fingerprint,
    stage4_run_id,
)
from app.e2e.stage4_store import Stage4E2EStore


NOW = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)


def request():
    return Stage4E2ERequest(
        portfolio_file="data/input/portafoglio-export.xlsx",
        portfolio_file_fingerprint="portfolio-fp",
        account_state_id="account-001",
        scanner_configuration_id="scanner-v1",
        scanner_configuration_fingerprint="scanner-fp",
        as_of=NOW,
        mode=Stage4Mode.PREFER_CACHE,
    )


def run(value, *, status=Stage4RunStatus.RUNNING, suffix="running"):
    terminal = status in {
        Stage4RunStatus.BLOCKED,
        Stage4RunStatus.PARTIAL,
        Stage4RunStatus.FAILED,
        Stage4RunStatus.WAITING_FOR_OPERATOR_SELECTION,
    }
    return Stage4E2ERun(
        run_id=stage4_run_id(value),
        policy_id=value.policy.policy_id,
        policy_version=value.policy.policy_version,
        mode=value.mode,
        status=status,
        terminal_reason=(
            Stage4TerminalReason.UPSTREAM_BLOCKED if terminal else None
        ),
        as_of=value.as_of,
        started_at=NOW,
        completed_at=NOW if terminal else None,
        portfolio_file_fingerprint=value.portfolio_file_fingerprint,
        account_state_id=value.account_state_id,
        request_fingerprint=stage4_request_fingerprint(value),
        fingerprint=canonical_fingerprint({"state": suffix}),
    )


def test_run_round_trip_and_idempotent_save(tmp_path):
    store = Stage4E2EStore(tmp_path / "stage4.db")
    value = request()
    current = run(value)
    store.save_run(current, value)
    store.save_run(current, value)
    assert store.get_run(current.run_id) == current
    assert store.get_request(current.run_id) == value


def test_terminal_run_is_immutable(tmp_path):
    store = Stage4E2EStore(tmp_path / "stage4.db")
    value = request()
    terminal = run(value, status=Stage4RunStatus.BLOCKED, suffix="terminal")
    store.save_run(terminal, value)
    with pytest.raises(ValueError, match="immutable"):
        store.save_run(
            terminal.model_copy(update={"fingerprint": "different"}), value
        )


def test_partial_run_can_resume_without_weakening_terminal_immutability(tmp_path):
    store = Stage4E2EStore(tmp_path / "stage4.db")
    value = request()
    partial = run(value, status=Stage4RunStatus.PARTIAL, suffix="partial")
    store.save_run(partial, value)
    running = run(value, status=Stage4RunStatus.RUNNING, suffix="resumed")
    store.save_run(running, value)
    assert store.get_run(running.run_id) == running


def test_stage_requires_parent_and_is_immutable(tmp_path):
    store = Stage4E2EStore(tmp_path / "stage4.db")
    value = request()
    current = run(value)
    record = Stage4StageRecord(
        run_id=current.run_id,
        stage=Stage4Stage.PORTFOLIO_ANALYSIS,
        status=Stage4StageStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
        input_ids=(value.portfolio_file_fingerprint,),
        output_ids=("snapshot-1", "risk-1"),
        input_fingerprint=value.portfolio_file_fingerprint,
        output_fingerprint="output-fp",
        message="portfolio persisted",
    )
    with pytest.raises(ValueError, match="parent"):
        store.save_stage(record)
    store.save_run(current, value)
    store.save_stage(record)
    store.save_stage(record)
    assert store.get_stage(current.run_id, record.stage) == record
    with pytest.raises(ValueError, match="immutable"):
        store.save_stage(record.model_copy(update={"message": "changed"}))


def test_stages_are_returned_in_canonical_order(tmp_path):
    store = Stage4E2EStore(tmp_path / "stage4.db")
    value = request()
    current = run(value)
    store.save_run(current, value)
    for stage in (Stage4Stage.RESEARCH_INTEGRATION, Stage4Stage.PORTFOLIO_ANALYSIS):
        store.save_stage(
            Stage4StageRecord(
                run_id=current.run_id,
                stage=stage,
                status=Stage4StageStatus.COMPLETED,
                started_at=NOW,
                completed_at=NOW,
                input_fingerprint="input",
                output_fingerprint="output",
                message="done",
            )
        )
    assert [item.stage for item in store.list_stages(current.run_id)] == [
        Stage4Stage.PORTFOLIO_ANALYSIS,
        Stage4Stage.RESEARCH_INTEGRATION,
    ]


def test_partial_attempts_are_append_only_and_idempotent(tmp_path):
    store = Stage4E2EStore(tmp_path / "stage4.db")
    value = request()
    current = run(value)
    store.save_run(current, value)

    first = Stage4StageRecord(
        run_id=current.run_id,
        stage=Stage4Stage.RESEARCH_INTEGRATION,
        status=Stage4StageStatus.PARTIAL,
        started_at=NOW,
        completed_at=NOW,
        input_ids=("watch-001",),
        output_ids=("research-001",),
        input_fingerprint="input-001",
        output_fingerprint="output-001",
        output_payload={
            "research_run_id": "research-001",
            "opportunity_ids": [],
        },
        reason=Stage4TerminalReason.UPSTREAM_PARTIAL,
        message="research remains partial",
        diagnostics=("MONITOR_ONLY=4",),
    )

    store.save_attempt(first)
    store.save_attempt(first)

    second = first.model_copy(
        update={
            "completed_at": NOW + timedelta(seconds=1),
            "output_ids": ("research-001", "hypothesis-005"),
            "output_fingerprint": "output-002",
            "diagnostics": ("MONITOR_ONLY=8",),
        }
    )
    store.save_attempt(second)

    attempts = store.list_attempts(current.run_id)

    assert attempts == (first, second)
    assert store.get_stage(
        current.run_id,
        Stage4Stage.RESEARCH_INTEGRATION,
    ) is None


def test_attempt_requires_partial_status_and_parent(tmp_path):
    store = Stage4E2EStore(tmp_path / "stage4.db")
    value = request()
    current = run(value)

    partial = Stage4StageRecord(
        run_id=current.run_id,
        stage=Stage4Stage.RESEARCH_INTEGRATION,
        status=Stage4StageStatus.PARTIAL,
        started_at=NOW,
        completed_at=NOW,
        input_fingerprint="input",
        output_fingerprint="output",
        reason=Stage4TerminalReason.UPSTREAM_PARTIAL,
        message="partial",
    )

    with pytest.raises(ValueError, match="parent"):
        store.save_attempt(partial)

    store.save_run(current, value)

    completed = partial.model_copy(
        update={
            "status": Stage4StageStatus.COMPLETED,
            "reason": None,
        }
    )

    with pytest.raises(ValueError, match="only PARTIAL"):
        store.save_attempt(completed)
