from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from app.e2e.stage4_contracts import Stage4Mode
from app.e2e.stage4_replenishment import (
    create_replenishment_session,
    plan_candidate_wave,
)
from app.e2e.stage4_replenishment_runtime import (
    CanonicalStage4CandidateWaveExecutor,
    Stage4ReplenishmentRuntimeConfiguration,
)


NOW = datetime(2026, 9, 27, 19, 46, tzinfo=timezone.utc)


def reports():
    return (
        {"all_decisions": [
            {"exchange": "BIT", "symbol": "AMP", "status": "ELIGIBLE"},
            {"exchange": "BIT", "symbol": "AVIO", "status": "ELIGIBLE"},
        ]},
        {"mappings": [
            {
                "exchange": "BIT", "symbol": "AMP",
                "mapping_status": "RESOLVED", "yahoo_symbol": "AMP.MI",
            },
            {
                "exchange": "BIT", "symbol": "AVIO",
                "mapping_status": "RESOLVED", "yahoo_symbol": "AVIO.MI",
            },
        ]},
    )


def session():
    return create_replenishment_session(
        root_run_id="root-1",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        as_of=NOW,
        mode=Stage4Mode.LIVE,
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
    eligibility, mapping = reports()
    eligibility_path = tmp_path / "eligibility.json"
    mapping_path = tmp_path / "mapping.json"
    eligibility_path.write_text(json.dumps(eligibility), encoding="utf-8")
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    portfolio = tmp_path / "portfolio.xlsx"
    portfolio.write_bytes(b"portfolio")
    return Stage4ReplenishmentRuntimeConfiguration(
        database_path=str(tmp_path / "state.db"),
        portfolio_file=str(portfolio),
        account_state_id="account-1",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        eligibility_report_path=str(eligibility_path),
        mapping_report_path=str(mapping_path),
        history_output_directory=str(tmp_path / "history"),
        child_output_directory=str(tmp_path / "children"),
        pause_seconds=0,
    )


def history_report(tmp_path, plan_value, routes):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({
        "run_status": "COMPLETED",
        "planned_count": len(plan_value.selected_listings),
        "completed_count": len(plan_value.selected_listings),
        "results": [
            {
                "quality": {
                    "listing_key": {
                        "exchange": listing.exchange,
                        "symbol": listing.symbol,
                    },
                    "route": routes[listing.listing_key],
                }
            }
            for listing in plan_value.selected_listings
        ],
    }), encoding="utf-8")
    return path


def test_review_routes_are_terminal_and_skip_child(tmp_path):
    value = session()
    plan_value = plan(value)
    calls = []

    def history_runner(current, now):
        return history_report(tmp_path, current, {
            listing.listing_key: "REVIEW_REQUIRED"
            for listing in current.selected_listings
        })

    def forbidden_child(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("review-only wave called child runtime")

    executor = CanonicalStage4CandidateWaveExecutor(
        configuration(tmp_path),
        history_runner=history_runner,
        child_runner=forbidden_child,
        clock=lambda: NOW,
    )
    execution = executor.execute(value, plan_value, now=NOW)
    assert not calls
    assert execution.record.status.value == "COMPLETED"
    assert execution.record.terminal_reason == "EVIDENCE_UNAVAILABLE"
    assert len(execution.directional_outcomes) == 4
    assert {
        item.status for item in execution.directional_outcomes
    } == {"EXCLUDED"}
    assert {
        item.reason for item in execution.directional_outcomes
    } == {"EVIDENCE_UNAVAILABLE"}
    assert execution.record.broker_orders_submitted == 0
    assert execution.record.portfolio_mutations == 0
    assert execution.record.automatic_executions == 0


def test_mixed_wave_runs_standard_and_routes_review_terminally(tmp_path):
    value = session()
    plan_value = plan(value)
    routes = {
        plan_value.selected_listings[0].listing_key: "STANDARD",
        plan_value.selected_listings[1].listing_key: "REVIEW_REQUIRED",
    }

    def history_runner(current, now):
        return history_report(tmp_path, current, routes)

    def child_runner(session_value, current, history_path, now):
        child = SimpleNamespace(
            run_id="child-1",
            scanner_run_id="scanner-1",
            watch_universe_run_id="watch-1",
            research_run_id="research-1",
            terminal_reason=None,
        )
        outcomes = tuple(
            SimpleNamespace(
                kind=kind,
                status="EXCLUDED",
                reason="RESEARCH_NOT_COMPLETE",
                opportunity_id=None,
            )
            for kind in ("NEW_LONG", "NEW_SHORT")
        )
        return child, outcomes

    executor = CanonicalStage4CandidateWaveExecutor(
        configuration(tmp_path),
        history_runner=history_runner,
        child_runner=child_runner,
        clock=lambda: NOW,
    )
    execution = executor.execute(value, plan_value, now=NOW)
    assert execution.record.status.value == "COMPLETED"
    assert execution.record.child_run_id == "child-1"
    assert len(execution.directional_outcomes) == 4
    assert sorted(
        item.reason for item in execution.directional_outcomes
    ) == [
        "EVIDENCE_UNAVAILABLE",
        "EVIDENCE_UNAVAILABLE",
        "RESEARCH_NOT_COMPLETE",
        "RESEARCH_NOT_COMPLETE",
    ]


def test_real_history_exit_two_with_completed_report_is_accepted(
    tmp_path, monkeypatch,
):
    value = session()
    plan_value = plan(value)
    config = configuration(tmp_path)

    def fake_run_pilot(*args, output, now, **kwargs):
        instant = now()
        path = Path(output) / (
            "scanner_history_quality_"
            + instant.strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )
        path.write_text(json.dumps({
            "run_status": "COMPLETED",
            "results": [],
        }), encoding="utf-8")
        return 2

    monkeypatch.setattr(
        "app.e2e.stage4_replenishment_runtime.run_pilot",
        fake_run_pilot,
    )
    executor = CanonicalStage4CandidateWaveExecutor(config)
    path = executor._run_history(plan_value, NOW)
    assert path.is_file()


def test_unavailable_history_snapshot_is_terminal_for_that_listing_only(tmp_path):
    # Live regression 2026-09-30: a listing without a history snapshot is
    # recorded with quality=None; it used to fail the whole wave with
    # "unexpected history listing result: :" and quarantine both listings.
    value = session()
    plan_value = plan(value)
    standard, unavailable = plan_value.selected_listings

    def history_runner(current, now):
        path = tmp_path / "history.json"
        path.write_text(json.dumps({
            "run_status": "COMPLETED",
            "results": [
                {"quality": {
                    "listing_key": {"exchange": standard.exchange, "symbol": standard.symbol},
                    "route": "STANDARD",
                }},
                {
                    "source_decision": {"exchange": unavailable.exchange, "symbol": unavailable.symbol},
                    "quality": None,
                },
            ],
        }), encoding="utf-8")
        return path

    def child_runner(session_value, current, history_path, now):
        child = SimpleNamespace(
            run_id="child-1", scanner_run_id="scanner-1",
            watch_universe_run_id="watch-1", research_run_id="research-1",
            terminal_reason=None,
        )
        outcomes = tuple(
            SimpleNamespace(kind=kind, status="EXCLUDED",
                            reason="RESEARCH_NOT_COMPLETE", opportunity_id=None)
            for kind in ("NEW_LONG", "NEW_SHORT")
        )
        return child, outcomes

    executor = CanonicalStage4CandidateWaveExecutor(
        configuration(tmp_path),
        history_runner=history_runner,
        child_runner=child_runner,
        clock=lambda: NOW,
    )
    execution = executor.execute(value, plan_value, now=NOW)
    assert execution.record.status.value == "COMPLETED"
    assert execution.record.child_run_id == "child-1"
    assert sorted(item.reason for item in execution.directional_outcomes) == [
        "EVIDENCE_UNAVAILABLE", "EVIDENCE_UNAVAILABLE",
        "RESEARCH_NOT_COMPLETE", "RESEARCH_NOT_COMPLETE",
    ]
    assert "history_unavailable_count=1" in execution.record.diagnostics
    routes = {standard.listing_key: "STANDARD", unavailable.listing_key: "UNAVAILABLE"}
    expected = "history_routes=" + ",".join(f"{key}={routes[key]}" for key in sorted(routes))
    assert expected in execution.record.diagnostics


def test_result_without_quality_or_source_still_fails_closed(tmp_path):
    value = session()
    plan_value = plan(value)

    def history_runner(current, now):
        path = tmp_path / "history.json"
        path.write_text(json.dumps({
            "run_status": "COMPLETED",
            "results": [{"quality": None}, {"quality": None}],
        }), encoding="utf-8")
        return path

    executor = CanonicalStage4CandidateWaveExecutor(
        configuration(tmp_path),
        history_runner=history_runner,
        child_runner=lambda *args: (_ for _ in ()).throw(AssertionError("child called")),
        clock=lambda: NOW,
    )
    execution = executor.execute(value, plan_value, now=NOW)
    assert execution.record.terminal_reason == "PROCESSING_FAILED"
