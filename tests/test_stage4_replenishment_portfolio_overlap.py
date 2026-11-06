from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from app.e2e.stage4_contracts import Stage4Mode
from app.e2e.stage4_replenishment import (
    build_candidate_frontier,
    candidate_frontier_evidence,
    create_replenishment_session,
    plan_candidate_wave,
    portfolio_watch_yahoo_symbols,
)
from app.e2e.stage4_replenishment_contracts import (
    CandidateWaveRecord,
    CandidateWaveStatus,
    ReplenishmentDisposition,
    ReplenishmentStopReason,
    candidate_wave_record_fingerprint,
)
from app.e2e.stage4_replenishment_runtime import (
    CanonicalStage4CandidateWaveExecutor,
    Stage4ReplenishmentRuntimeConfiguration,
)
from app.e2e.stage4_replenishment_store import (
    Stage4CandidateReplenishmentStore,
)


NOW = datetime(2026, 9, 27, 20, 10, tzinfo=timezone.utc)


def reports():
    keys = ("BZU", "CPR", "ENEL", "ENI")
    return (
        {
            "all_decisions": [
                {"exchange": "BIT", "symbol": key, "status": "ELIGIBLE"}
                for key in keys
            ]
        },
        {
            "mappings": [
                {
                    "exchange": "BIT",
                    "symbol": key,
                    "mapping_status": "RESOLVED",
                    "yahoo_symbol": f"{key}.MI",
                }
                for key in keys
            ]
        },
    )


def session():
    return create_replenishment_session(
        root_run_id="s4a-root",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        as_of=NOW,
        mode=Stage4Mode.LIVE,
    )


def portfolio_universe():
    return SimpleNamespace(members=(
        SimpleNamespace(
            provenances=("CURRENT_POSITION",),
            yahoo_symbols=("BZU.MI",),
        ),
        SimpleNamespace(
            provenances=("CURRENT_POSITION", "NEW_CANDIDATE"),
            yahoo_symbols=("CPR.MI",),
        ),
        SimpleNamespace(
            provenances=("NEW_CANDIDATE",),
            yahoo_symbols=("AMP.MI",),
        ),
    ))


def test_direct_and_proxy_portfolio_identities_leave_acquisition_frontier():
    eligibility, mapping = reports()
    excluded = portfolio_watch_yahoo_symbols(portfolio_universe())
    assert excluded == ("BZU.MI", "CPR.MI")

    frontier = build_candidate_frontier(
        eligibility,
        mapping,
        excluded_yahoo_symbols=excluded,
    )
    assert tuple(value.listing_key for value in frontier) == (
        "BIT:ENEL",
        "BIT:ENI",
    )

    evidence = candidate_frontier_evidence(
        eligibility,
        mapping,
        excluded_yahoo_symbols=excluded,
    )
    assert evidence["portfolio_watch_yahoo_symbol_count"] == 2
    assert evidence["portfolio_overlap_supported_eligible_count"] == 2
    assert evidence["portfolio_overlap_excluded_listing_sample"] == (
        "BIT:BZU",
        "BIT:CPR",
    )


def test_wave_plan_never_selects_portfolio_overlap():
    eligibility, mapping = reports()
    value = plan_candidate_wave(
        session=session(),
        wave_index=1,
        eligibility=eligibility,
        mapping=mapping,
        prior_outcomes=(),
        attempted_listing_keys=(),
        excluded_yahoo_symbols=("BZU.MI", "CPR.MI"),
    )
    assert value.disposition is ReplenishmentDisposition.ADVANCE_FRONTIER
    assert tuple(x.listing_key for x in value.selected_listings) == (
        "BIT:ENEL",
        "BIT:ENI",
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


def test_directional_count_invariant_is_fatal_and_not_retryable(tmp_path):
    eligibility, mapping = reports()
    # Use a clean two-listing plan to exercise the defensive runtime boundary.
    plan = plan_candidate_wave(
        session=session(),
        wave_index=1,
        eligibility=eligibility,
        mapping=mapping,
        prior_outcomes=(),
        attempted_listing_keys=("BIT:BZU", "BIT:CPR"),
    )

    def history_runner(value, now):
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

    child = SimpleNamespace(
        run_id="child-1",
        scanner_run_id="scanner-1",
        watch_universe_run_id="watch-1",
        research_run_id="research-1",
        terminal_reason=None,
    )
    executor = CanonicalStage4CandidateWaveExecutor(
        configuration(tmp_path),
        history_runner=history_runner,
        child_runner=lambda *args: (child, ()),
        clock=lambda: NOW,
    )
    execution = executor.execute(session(), plan, now=NOW)
    assert execution.record.status is CandidateWaveStatus.BLOCKED
    assert (
        execution.record.terminal_reason
        == "ORCHESTRATION_INVARIANT_VIOLATION"
    )
    assert {x.reason for x in execution.directional_outcomes} == {
        "ORCHESTRATION_INVARIANT_VIOLATION"
    }
    resumed = executor.resume(execution.record)
    assert {x.reason for x in resumed.directional_outcomes} == {
        "ORCHESTRATION_INVARIANT_VIOLATION"
    }

    stop = plan_candidate_wave(
        session=session(),
        wave_index=1,
        eligibility=eligibility,
        mapping=mapping,
        prior_outcomes=execution.directional_outcomes,
        attempted_listing_keys=("BIT:BZU", "BIT:CPR"),
        current_listing_keys=execution.record.listing_keys,
        retry_count=0,
    )
    assert stop.disposition is ReplenishmentDisposition.STOP_FAIL_CLOSED
    assert stop.reason is ReplenishmentStopReason.SAFETY_BLOCK
    assert stop.retry_count == 0


def wave_record(session_id, started_at, diagnostic):
    payload = {
        "session_id": session_id,
        "wave_index": 5,
        "status": CandidateWaveStatus.PARTIAL,
        "started_at": started_at,
        "completed_at": started_at + timedelta(seconds=1),
        "plan_fingerprint": "plan-" + diagnostic,
        "listing_keys": ("BIT:BZU", "BIT:CPR"),
        "terminal_reason": "PROCESSING_FAILED",
        "diagnostics": (diagnostic,),
    }
    fingerprint = candidate_wave_record_fingerprint(payload)
    return CandidateWaveRecord(
        record_id="wave-" + fingerprint[:24],
        fingerprint=fingerprint,
        **payload,
    )


def test_retry_attempts_are_reported_in_chronological_order(tmp_path):
    value = session()
    store = Stage4CandidateReplenishmentStore(tmp_path / "state.db")
    store.save_session(value)
    early = wave_record(value.session_id, NOW, "early")
    late = None
    for index in range(1000):
        candidate = wave_record(
            value.session_id,
            NOW + timedelta(minutes=1),
            f"late-{index}",
        )
        if candidate.record_id < early.record_id:
            late = candidate
            break
    assert late is not None
    store.save_wave(late)
    store.save_wave(early)
    assert store.list_waves(value.session_id) == (early, late)
