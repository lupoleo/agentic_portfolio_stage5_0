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


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def reports(count=6):
    eligibility = {"all_decisions": []}
    mapping = {"mappings": []}
    for index in range(count):
        symbol = f"SYM{index:02d}"
        eligibility["all_decisions"].append({
            "exchange": "BIT",
            "symbol": symbol,
            "status": "ELIGIBLE",
        })
        mapping["mappings"].append({
            "exchange": "BIT",
            "symbol": symbol,
            "mapping_status": "RESOLVED",
            "resolved_symbol": f"{symbol}.MI",
        })
    return eligibility, mapping


def outcome(
    listing,
    kind,
    *,
    status="EXCLUDED",
    reason="RESEARCH_NOT_COMPLETE",
    opportunity_id=None,
):
    suffix = "long" if kind == "NEW_LONG" else "short"
    return SimpleNamespace(
        hypothesis_id=f"cand-{listing.symbol.lower()}-{suffix}",
        subject_value=listing.yahoo_symbol,
        listing_key=listing.listing_key,
        kind=kind,
        status=status,
        reason=reason,
        opportunity_id=opportunity_id,
    )


def failed_wave(plan):
    values = []
    for listing in plan.selected_listings:
        values.extend((
            outcome(listing, "NEW_LONG"),
            outcome(listing, "NEW_SHORT"),
        ))
    values[0] = outcome(
        plan.selected_listings[0],
        "NEW_LONG",
        status="FAILED",
        reason="PROCESSING_FAILED",
    )
    return tuple(values)


def opportunity_wave(plan):
    values = []
    for listing in plan.selected_listings:
        values.extend((
            outcome(listing, "NEW_LONG"),
            outcome(listing, "NEW_SHORT"),
        ))
    values[0] = outcome(
        plan.selected_listings[0],
        "NEW_LONG",
        status="OPPORTUNITY_CREATED",
        reason="OPPORTUNITY_CREATED",
        opportunity_id="opp-after-quarantine",
    )
    return tuple(values)


def record(session, plan, outcomes, research_run_id):
    payload = {
        "session_id": session.session_id,
        "wave_index": plan.wave_index,
        "status": CandidateWaveStatus.COMPLETED,
        "started_at": NOW,
        "completed_at": NOW,
        "plan_fingerprint": plan.fingerprint,
        "listing_keys": tuple(
            value.listing_key for value in plan.selected_listings
        ),
        "child_run_id": f"child-{plan.fingerprint[:12]}",
        "research_run_id": research_run_id,
        "opportunity_ids": tuple(sorted({
            value.opportunity_id
            for value in outcomes
            if value.opportunity_id
        })),
    }
    fingerprint = candidate_wave_record_fingerprint(payload)
    return CandidateWaveRecord(
        record_id="wave-" + fingerprint[:24],
        fingerprint=fingerprint,
        **payload,
    )


class ScriptedExecutor:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.executed = []
        self.persisted = {}

    def execute(self, session, plan, *, now):
        self.executed.append(plan)
        outcomes = tuple(self.scripted.pop(0)(plan))
        research_run_id = f"research-{len(self.executed)}"
        execution = CandidateWaveExecution(
            record=record(
                session, plan, outcomes, research_run_id
            ),
            directional_outcomes=outcomes,
        )
        self.persisted[execution.record.record_id] = execution
        return execution

    def resume(self, value):
        return self.persisted[value.record_id]


def session(max_waves=5):
    return create_replenishment_session(
        root_run_id="s4a-root",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        as_of=NOW,
        mode=Stage4Mode.LIVE,
        policy=CandidateReplenishmentPolicy(max_waves=max_waves),
    )


def run(tmp_path, executor, *, max_waves=5):
    eligibility, mapping = reports()
    store = Stage4CandidateReplenishmentStore(tmp_path / "state.db")
    value = Stage4CandidateReplenishmentService(
        store=store,
        executor=executor,
    )
    return value.run(
        session=session(max_waves=max_waves),
        eligibility=eligibility,
        mapping=mapping,
        now=NOW,
    )


def test_retry_exhaustion_quarantines_candidate_and_advances(tmp_path):
    executor = ScriptedExecutor((
        failed_wave,
        failed_wave,
        opportunity_wave,
    ))
    result = run(tmp_path, executor)

    assert result.opportunity_ids == ("opp-after-quarantine",)
    assert result.terminal_reason is (
        ReplenishmentStopReason.SELECTABLE_OPPORTUNITY_FOUND
    )
    assert [value.wave_index for value in executor.executed] == [1, 1, 2]
    assert executor.executed[0].selected_listings == (
        executor.executed[1].selected_listings
    )
    assert executor.executed[2].selected_listings != (
        executor.executed[1].selected_listings
    )
    quarantined = executor.executed[0].selected_listings[0]
    assert result.retry_exhausted_outcome_count == 1
    assert result.quarantined_listing_keys == (
        quarantined.listing_key,
    )
    assert result.quarantined_hypothesis_ids == (
        f"cand-{quarantined.symbol.lower()}-long",
    )
    assert result.quarantine_reason_counts == (
        "PROCESSING_FAILED=1",
    )
    assert result.source_research_run_ids == ("research-2",)


def test_retry_exhaustion_respects_wave_budget(tmp_path):
    executor = ScriptedExecutor((failed_wave, failed_wave))
    result = run(tmp_path, executor, max_waves=1)

    assert result.terminal_reason is (
        ReplenishmentStopReason.REPLENISHMENT_BUDGET_EXHAUSTED
    )
    assert [value.wave_index for value in executor.executed] == [1, 1]
    quarantined = executor.executed[0].selected_listings[0]
    assert result.retry_exhausted_outcome_count == 1
    assert result.quarantined_listing_keys == (
        quarantined.listing_key,
    )
    assert not result.opportunity_ids


def test_quarantine_does_not_mutate_raw_failed_outcome(tmp_path):
    captured = []

    def capture_failed(plan):
        values = failed_wave(plan)
        captured.extend(values)
        return values

    executor = ScriptedExecutor((
        capture_failed,
        capture_failed,
        opportunity_wave,
    ))
    run(tmp_path, executor)

    failed = [
        value for value in captured
        if value.reason == "PROCESSING_FAILED"
    ]
    assert len(failed) == 2
    assert all(value.status == "FAILED" for value in failed)
    assert all(value.opportunity_id is None for value in failed)
