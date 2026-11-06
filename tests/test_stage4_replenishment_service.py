from datetime import datetime, timezone
from types import SimpleNamespace

from app.e2e.stage4_contracts import Stage4Mode
from app.e2e.stage4_replenishment import create_replenishment_session
from app.e2e.stage4_replenishment_contracts import (
    CandidateReplenishmentPolicy,
    CandidateWaveRecord,
    CandidateWaveStatus,
    ReplenishmentStopReason,
    candidate_wave_record_fingerprint,
)
from app.e2e.stage4_replenishment_service import (
    CandidateWaveExecution,
    Stage4CandidateReplenishmentService,
)
from app.e2e.stage4_replenishment_store import (
    Stage4CandidateReplenishmentStore,
)


NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


def reports(count=8):
    venues = ("BIT", "XETRA", "NASDAQ", "NYSE")
    eligibility = {"all_decisions": []}
    mapping = {"mappings": []}
    for index in range(count):
        exchange = venues[index % len(venues)]
        symbol = f"SYM{index:02d}"
        eligibility["all_decisions"].append({
            "exchange": exchange,
            "symbol": symbol,
            "status": "ELIGIBLE",
        })
        mapping["mappings"].append({
            "exchange": exchange,
            "symbol": symbol,
            "mapping_status": "RESOLVED",
            "resolved_symbol": f"{symbol}.Y",
        })
    return eligibility, mapping


def outcome(
    listing_key,
    kind,
    *,
    status="EXCLUDED",
    reason="RESEARCH_NOT_COMPLETE",
    opportunity_id=None,
):
    return SimpleNamespace(
        listing_key=listing_key,
        kind=kind,
        status=status,
        reason=reason,
        opportunity_id=opportunity_id,
    )


def record(session, plan, *, outcomes, status=CandidateWaveStatus.COMPLETED):
    opportunities = tuple(sorted({
        value.opportunity_id
        for value in outcomes
        if value.opportunity_id
    }))
    payload = {
        "session_id": session.session_id,
        "wave_index": plan.wave_index,
        "status": status,
        "started_at": NOW,
        "completed_at": NOW,
        "plan_fingerprint": plan.fingerprint,
        "listing_keys": tuple(
            value.listing_key for value in plan.selected_listings
        ),
        "child_run_id": f"child-{plan.fingerprint[:12]}",
        "opportunity_ids": opportunities,
    }
    fingerprint = candidate_wave_record_fingerprint(payload)
    return CandidateWaveRecord(
        record_id="wave-" + fingerprint[:24],
        fingerprint=fingerprint,
        **payload,
    )


class FakeExecutor:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.executed = []
        self.persisted = {}

    def execute(self, session, plan, *, now):
        self.executed.append(plan)
        value = tuple(self.scripted.pop(0)(plan))
        execution = CandidateWaveExecution(
            record=record(session, plan, outcomes=value),
            directional_outcomes=value,
        )
        self.persisted[execution.record.record_id] = execution
        return execution

    def resume(self, value):
        return self.persisted[value.record_id]


def excluded(plan):
    values = []
    for listing in plan.selected_listings:
        values.extend((
            outcome(listing.listing_key, "NEW_LONG"),
            outcome(listing.listing_key, "NEW_SHORT"),
        ))
    return values


def opportunity(plan):
    values = excluded(plan)
    first = values[0]
    values[0] = outcome(
        first.listing_key,
        first.kind,
        status="OPPORTUNITY_CREATED",
        reason="OPPORTUNITY_CREATED",
        opportunity_id="opp-001",
    )
    return values


def transient(plan):
    listing = plan.selected_listings[0]
    return (
        outcome(
            listing.listing_key,
            "NEW_LONG",
            status="FAILED",
            reason="PROCESSING_FAILED",
        ),
    )


def session(max_waves=5):
    return create_replenishment_session(
        root_run_id="s4a-root",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        as_of=NOW,
        mode=Stage4Mode.LIVE,
        policy=CandidateReplenishmentPolicy(max_waves=max_waves),
    )


def service(tmp_path, executor):
    store = Stage4CandidateReplenishmentStore(tmp_path / "state.db")
    return Stage4CandidateReplenishmentService(
        store=store,
        executor=executor,
    ), store


def test_replenishes_until_first_opportunity(tmp_path):
    eligibility, mapping = reports()
    executor = FakeExecutor((excluded, opportunity))
    value, store = service(tmp_path, executor)
    result = value.run(
        session=session(),
        eligibility=eligibility,
        mapping=mapping,
        now=NOW,
    )
    assert result.opportunity_found
    assert result.opportunity_ids == ("opp-001",)
    assert result.terminal_reason is (
        ReplenishmentStopReason.SELECTABLE_OPPORTUNITY_FOUND
    )
    assert len(executor.executed) == 2
    assert len(result.attempted_listing_keys) == 4
    assert len(store.list_waves(result.session_id)) == 2


def test_stops_only_after_bounded_wave_budget(tmp_path):
    eligibility, mapping = reports()
    executor = FakeExecutor((excluded, excluded))
    value, _ = service(tmp_path, executor)
    result = value.run(
        session=session(max_waves=2),
        eligibility=eligibility,
        mapping=mapping,
        now=NOW,
    )
    assert not result.opportunity_found
    assert result.terminal_reason is (
        ReplenishmentStopReason.REPLENISHMENT_BUDGET_EXHAUSTED
    )
    assert len(executor.executed) == 2
    assert len(result.attempted_listing_keys) == 4


def test_transient_failure_retries_same_listing_once(tmp_path):
    eligibility, mapping = reports()
    executor = FakeExecutor((transient, opportunity))
    value, _ = service(tmp_path, executor)
    result = value.run(
        session=session(),
        eligibility=eligibility,
        mapping=mapping,
        now=NOW,
    )
    assert result.opportunity_found
    assert len(executor.executed) == 2
    first, second = executor.executed
    assert first.wave_index == second.wave_index == 1
    assert first.selected_listings == second.selected_listings
    assert second.retry_count == 1


def test_resume_replays_immutable_waves_without_execution(tmp_path):
    eligibility, mapping = reports()
    executor = FakeExecutor((excluded, opportunity))
    value, store = service(tmp_path, executor)
    first = value.run(
        session=session(),
        eligibility=eligibility,
        mapping=mapping,
        now=NOW,
    )
    assert len(executor.executed) == 2
    second = Stage4CandidateReplenishmentService(
        store=store,
        executor=executor,
    ).run(
        session=session(),
        eligibility=eligibility,
        mapping=mapping,
        now=NOW,
    )
    assert second == first
    assert len(executor.executed) == 2


def test_result_preserves_zero_side_effects(tmp_path):
    eligibility, mapping = reports()
    value, _ = service(tmp_path, FakeExecutor((opportunity,)))
    result = value.run(
        session=session(),
        eligibility=eligibility,
        mapping=mapping,
        now=NOW,
    )
    assert result.broker_orders_submitted == 0
    assert result.portfolio_mutations == 0
    assert result.automatic_executions == 0
