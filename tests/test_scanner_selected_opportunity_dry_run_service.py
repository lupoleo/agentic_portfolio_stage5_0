from collections import Counter
from datetime import datetime, timedelta, timezone

from app.scanner.selected_opportunity_dry_run_contracts import (
    DryRunMode,
    DryRunStage,
    DryRunStageResult,
    DryRunStageStatus,
    DryRunStatus,
    DryRunTerminalReason,
    SelectedOpportunityDryRunRequest,
    SelectedOpportunityParameters,
)
from app.scanner.selected_opportunity_dry_run_service import (
    SelectedOpportunityDryRunService,
)
from app.scanner.selected_opportunity_dry_run_store import (
    SelectedOpportunityDryRunStore,
)


NOW = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)


def request(mode=DryRunMode.LIVE):
    return SelectedOpportunityDryRunRequest(
        scanner_research_run_id="s2f-run",
        opportunity_ids=("opp-1",),
        portfolio_snapshot_id="snapshot-1",
        as_of=NOW,
        mode=mode,
        parameters=SelectedOpportunityParameters(
            requested_exposure_eur=10_000,
            max_intended_loss_eur=250,
            reference_price=100,
            fx_to_eur=0.85,
            stop_price=95,
            market_observed_at=NOW - timedelta(minutes=5),
        ),
    )


class Executor:
    def __init__(self, blocked_stage=None):
        self.calls = Counter()
        self.blocked_stage = blocked_stage

    def execute(self, stage, _request, _run_id):
        self.calls[stage] += 1
        if stage is self.blocked_stage:
            return DryRunStageResult(
                stage=stage,
                status=DryRunStageStatus.BLOCKED,
                reason=DryRunTerminalReason.CIO_REJECTED,
                message="deterministic rejection",
            )
        output_id = "plan-1" if stage is DryRunStage.EXECUTION_PLAN else stage.value
        return DryRunStageResult(
            stage=stage,
            status=DryRunStageStatus.COMPLETED,
            output_ids=(output_id,),
            output_payload={"stage": stage.value},
            message="completed",
        )


def test_full_controlled_dry_run_is_resumable_and_non_executing(tmp_path):
    store = SelectedOpportunityDryRunStore(tmp_path / "state.db")
    executor = Executor()
    service = SelectedOpportunityDryRunService(store=store, executor=executor)
    first = service.run(request(), now=NOW)
    assert first.status is DryRunStatus.COMPLETED
    assert first.terminal_reason is DryRunTerminalReason.DRY_RUN_PLAN_CREATED
    assert first.execution_plan_id == "plan-1"
    assert len(first.completed_stages) == len(DryRunStage)
    assert first.broker_orders_submitted == 0
    assert first.portfolio_mutations == 0
    assert first.automatic_executions == 0

    second = service.run(request(), now=NOW + timedelta(minutes=1))
    assert second == first
    assert all(count == 1 for count in executor.calls.values())


def test_cache_only_miss_makes_no_stage_calls_and_live_can_resume(tmp_path):
    store = SelectedOpportunityDryRunStore(tmp_path / "state.db")
    executor = Executor()
    service = SelectedOpportunityDryRunService(store=store, executor=executor)
    cached = service.run(request(DryRunMode.CACHE_ONLY), now=NOW)
    assert cached.status is DryRunStatus.BLOCKED
    assert cached.terminal_reason is DryRunTerminalReason.CACHE_ONLY_MISS
    assert not executor.calls

    live = service.run(request(DryRunMode.LIVE), now=NOW + timedelta(minutes=1))
    assert live.status is DryRunStatus.COMPLETED
    assert len(executor.calls) == len(DryRunStage)


def test_business_block_stops_all_downstream_stages(tmp_path):
    store = SelectedOpportunityDryRunStore(tmp_path / "state.db")
    executor = Executor(blocked_stage=DryRunStage.CIO_DECISION)
    run = SelectedOpportunityDryRunService(
        store=store, executor=executor
    ).run(request(), now=NOW)
    assert run.status is DryRunStatus.BLOCKED
    assert run.terminal_reason is DryRunTerminalReason.CIO_REJECTED
    assert executor.calls[DryRunStage.EXECUTION_PLAN] == 0
    assert run.execution_plan_id is None


def test_zero_real_opportunity_is_explicitly_blocked(tmp_path):
    class SelectionExecutor:
        def execute(self, stage, _request, _run_id):
            assert stage is DryRunStage.SELECTION_VALIDATION
            return DryRunStageResult(
                stage=stage,
                status=DryRunStageStatus.BLOCKED,
                reason=DryRunTerminalReason.NO_SELECTABLE_OPPORTUNITY,
                message="zero S2.2F opportunities",
            )

    value = request().model_copy(
        update={"opportunity_ids": (), "parameters": None}
    )
    store = SelectedOpportunityDryRunStore(tmp_path / "state.db")
    run = SelectedOpportunityDryRunService(
        store=store, executor=SelectionExecutor()
    ).run(value, now=NOW)
    assert run.status is DryRunStatus.BLOCKED
    assert run.terminal_reason is DryRunTerminalReason.NO_SELECTABLE_OPPORTUNITY
