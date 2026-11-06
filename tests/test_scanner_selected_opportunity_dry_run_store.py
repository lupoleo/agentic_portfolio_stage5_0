from datetime import datetime, timezone

import pytest

from app.scanner.selected_opportunity_dry_run_contracts import (
    DryRunMode,
    DryRunStage,
    DryRunStageRecord,
    DryRunStageStatus,
    DryRunStatus,
    SelectedOpportunityDryRun,
    SelectedOpportunityDryRunRequest,
)
from app.scanner.selected_opportunity_dry_run_store import (
    SelectedOpportunityDryRunStore,
)


NOW = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)


def test_store_round_trip_and_completed_stage_immutability(tmp_path):
    store = SelectedOpportunityDryRunStore(tmp_path / "state.db")
    request = SelectedOpportunityDryRunRequest(
        scanner_research_run_id="s2f-run",
        opportunity_ids=("opp-1",),
        portfolio_snapshot_id="snapshot-1",
        as_of=NOW,
    )
    run = SelectedOpportunityDryRun(
        run_id="s2g-run",
        scanner_research_run_id="s2f-run",
        opportunity_id="opp-1",
        portfolio_snapshot_id="snapshot-1",
        policy_id="policy",
        policy_version="1",
        as_of=NOW,
        started_at=NOW,
        mode=DryRunMode.LIVE,
        status=DryRunStatus.RUNNING,
        request_fingerprint="a" * 64,
        fingerprint="b" * 64,
    )
    store.save_run(run, request)
    assert store.get_run(run.run_id) == run
    assert store.get_request(run.run_id) == request

    stage = DryRunStageRecord(
        run_id=run.run_id,
        stage=DryRunStage.SELECTION_VALIDATION,
        status=DryRunStageStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW,
        input_fingerprint="c" * 64,
        output_fingerprint="d" * 64,
        message="validated",
    )
    store.save_stage(stage)
    store.save_stage(stage)
    assert store.list_stages(run.run_id) == [stage]

    changed = stage.model_copy(update={"message": "changed"})
    with pytest.raises(ValueError, match="immutable"):
        store.save_stage(changed)
