from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.e2e.stage4_contracts import Stage4Mode
from app.e2e.stage4_replenishment import (
    create_replenishment_session,
    plan_candidate_wave,
)
from app.e2e.stage4_replenishment_runtime import (
    CanonicalStage4CandidateWaveExecutor,
    Stage4ReplenishmentRuntimeConfiguration,
)


NOW = datetime(2026, 9, 27, 15, 30, tzinfo=timezone.utc)


def reports():
    return (
        {"all_decisions": [
            {"exchange": "BIT", "symbol": "AAA", "status": "ELIGIBLE"},
            {"exchange": "XETRA", "symbol": "BBB", "status": "ELIGIBLE"},
        ]},
        {"mappings": [
            {
                "exchange": "BIT", "symbol": "AAA",
                "mapping_status": "RESOLVED", "resolved_symbol": "AAA.MI",
            },
            {
                "exchange": "XETRA", "symbol": "BBB",
                "mapping_status": "RESOLVED", "resolved_symbol": "BBB.DE",
            },
        ]},
    )


def session(mode):
    return create_replenishment_session(
        root_run_id="root-1",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        as_of=NOW,
        mode=mode,
    )


def plan(value):
    eligibility, mapping = reports()
    return plan_candidate_wave(
        session=value,
        wave_index=1,
        eligibility=eligibility,
        mapping=mapping,
        prior_outcomes=(),
        attempted_listing_keys=(),
    )


def configuration(tmp_path):
    return Stage4ReplenishmentRuntimeConfiguration(
        database_path=str(tmp_path / "state.db"),
        portfolio_file=str(tmp_path / "portfolio.xlsx"),
        account_state_id="account-1",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        eligibility_report_path=str(tmp_path / "eligibility.json"),
        mapping_report_path=str(tmp_path / "mapping.json"),
        history_output_directory=str(tmp_path / "history"),
        child_output_directory=str(tmp_path / "children"),
    )


def test_cache_only_wave_never_calls_network_runners(tmp_path):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("network runner called")

    value = session(Stage4Mode.CACHE_ONLY)
    executor = CanonicalStage4CandidateWaveExecutor(
        configuration(tmp_path),
        history_runner=forbidden,
        child_runner=forbidden,
        clock=lambda: NOW,
    )
    execution = executor.execute(value, plan(value), now=NOW)
    assert not calls
    assert execution.record.terminal_reason == "CACHE_ONLY_MISS"
    assert len(execution.directional_outcomes) == 4
    assert {
        item.reason for item in execution.directional_outcomes
    } == {"CACHE_ONLY_MISS"}


def test_live_wave_preserves_symmetric_directional_outcomes(tmp_path):
    calls = []

    def history_runner(value, now):
        calls.append(("history", value.fingerprint))
        path = tmp_path / "history.json"
        path.write_text(json.dumps({
            "run_status": "COMPLETED",
            "results": [
                {
                    "quality": {
                        "listing_key": {
                            "exchange": listing.exchange,
                            "symbol": listing.symbol,
                        },
                        "route": "STANDARD",
                    }
                }
                for listing in value.selected_listings
            ],
        }), encoding="utf-8")
        return path

    def child_runner(session_value, plan_value, history_path, now):
        calls.append(("child", str(history_path)))
        outcomes = tuple(
            SimpleNamespace(
                kind=kind,
                status="EXCLUDED",
                reason="RESEARCH_NOT_COMPLETE",
                opportunity_id=None,
            )
            for listing in plan_value.selected_listings
            for kind in ("NEW_LONG", "NEW_SHORT")
        )
        child = SimpleNamespace(
            run_id="child-1",
            scanner_run_id="scanner-1",
            watch_universe_run_id="watch-1",
            research_run_id="research-1",
            terminal_reason=None,
        )
        return child, outcomes

    value = session(Stage4Mode.LIVE)
    executor = CanonicalStage4CandidateWaveExecutor(
        configuration(tmp_path),
        history_runner=history_runner,
        child_runner=child_runner,
        clock=lambda: NOW,
    )
    execution = executor.execute(value, plan(value), now=NOW)
    assert [item[0] for item in calls] == ["history", "child"]
    assert execution.record.status.value == "COMPLETED"
    assert execution.record.child_run_id == "child-1"
    assert execution.record.research_run_id == "research-1"
    assert len(execution.directional_outcomes) == 4
    assert execution.record.broker_orders_submitted == 0
    assert execution.record.portfolio_mutations == 0
    assert execution.record.automatic_executions == 0


def test_live_processing_failure_becomes_one_retryable_wave(tmp_path):
    def failed(*args, **kwargs):
        raise RuntimeError("temporary provider failure")

    value = session(Stage4Mode.LIVE)
    executor = CanonicalStage4CandidateWaveExecutor(
        configuration(tmp_path),
        history_runner=failed,
        clock=lambda: NOW,
    )
    execution = executor.execute(value, plan(value), now=NOW)
    assert execution.record.status.value == "PARTIAL"
    assert execution.record.terminal_reason == "PROCESSING_FAILED"
    assert {
        item.reason for item in execution.directional_outcomes
    } == {"PROCESSING_FAILED"}


def test_runtime_requires_capacity_for_two_symmetric_listings(tmp_path):
    values = configuration(tmp_path).model_dump()
    values["max_hypotheses"] = 3
    with pytest.raises(ValueError):
        Stage4ReplenishmentRuntimeConfiguration(**values)
