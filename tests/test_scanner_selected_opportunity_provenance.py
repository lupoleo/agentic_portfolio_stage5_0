from datetime import datetime, timezone

from app.cio.models import (
    Direction,
    OpportunityStatus,
    PortfolioSnapshot,
    TradeOpportunity,
    TradingHorizon,
)
from app.cio.storage import Stage3Store
from app.scanner.research_integration_contracts import (
    HypothesisOutcomeReason,
    HypothesisOutcomeStatus,
    IntegrationRunStatus,
    ResearchHypothesisKind,
    ResearchHypothesisOutcome,
    ScannerResearchIntegrationPolicy,
    ScannerResearchRun,
    TradeOpportunityProvenanceLink,
)
from app.scanner.research_integration_store import ScannerResearchIntegrationStore
from app.scanner.selected_opportunity_dry_run import validate_selected_opportunity
from app.scanner.selected_opportunity_dry_run_contracts import (
    DryRunStageStatus,
    DryRunTerminalReason,
    SelectedOpportunityDryRunRequest,
)


NOW = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)


def seeded(tmp_path, *, kind=ResearchHypothesisKind.NEW_LONG):
    db = tmp_path / "state.db"
    stage3 = Stage3Store(db)
    integration = ScannerResearchIntegrationStore(db)
    snapshot = PortfolioSnapshot(
        snapshot_id="snapshot-1",
        timestamp=NOW,
        source_file="controlled.xlsx",
        source_file_hash="hash",
        quant_engine_version="test",
        analyzed_positions=1,
        gross_exposure_eur=1000,
        net_exposure_eur=1000,
    )
    stage3.save_portfolio_snapshot(snapshot)
    opportunity = TradeOpportunity(
        opportunity_id="opp-1",
        snapshot_id=snapshot.snapshot_id,
        created_at=NOW,
        ticker="TEST",
        direction=Direction.LONG,
        horizon=TradingHorizon.SWING,
        confidence=0.8,
        thesis="Controlled directional thesis",
        status=OpportunityStatus.DISCOVERED,
    )
    stage3.save_trade_opportunity(opportunity)
    run = ScannerResearchRun(
        run_id="s2f-run",
        watch_universe_run_id="watch-run",
        watch_universe_fingerprint="watch-fingerprint",
        portfolio_snapshot_id=snapshot.snapshot_id,
        policy=ScannerResearchIntegrationPolicy(),
        as_of=NOW,
        started_at=NOW,
        completed_at=NOW,
        status=IntegrationRunStatus.COMPLETED,
        hypothesis_ids=("hyp-1",),
        completed_hypothesis_ids=("hyp-1",),
        opportunity_ids=(opportunity.opportunity_id,),
        fingerprint="run-fingerprint",
    )
    integration.save_run(run)
    integration.save_outcome(ResearchHypothesisOutcome(
        integration_run_id=run.run_id,
        hypothesis_id="hyp-1",
        subject_namespace="YAHOO",
        subject_value="TEST",
        kind=kind,
        status=HypothesisOutcomeStatus.OPPORTUNITY_CREATED,
        reason=HypothesisOutcomeReason.OPPORTUNITY_CREATED,
        updated_at=NOW,
        research_id="research-1",
        opportunity_score_id="score-1",
        opportunity_id=opportunity.opportunity_id,
    ))
    integration.save_provenance_link(TradeOpportunityProvenanceLink(
        opportunity_id=opportunity.opportunity_id,
        integration_run_id=run.run_id,
        watch_universe_run_id=run.watch_universe_run_id,
        watch_universe_fingerprint=run.watch_universe_fingerprint,
        subject_namespace="YAHOO",
        subject_value="TEST",
        hypothesis_id="hyp-1",
        candidate_id="hyp-1",
        research_id="research-1",
        opportunity_score_id="score-1",
        portfolio_snapshot_id=snapshot.snapshot_id,
        evidence_ids=("evidence-1",),
        inference_ids=("inference-1",),
        policy_id=run.policy.policy_id,
        policy_version=run.policy.policy_version,
        invalidation_conditions=("Controlled invalidation",),
        created_at=NOW,
    ))
    request = SelectedOpportunityDryRunRequest(
        scanner_research_run_id=run.run_id,
        opportunity_ids=(opportunity.opportunity_id,),
        portfolio_snapshot_id=snapshot.snapshot_id,
        as_of=NOW,
    )
    return request, integration, stage3


def test_selection_accepts_complete_s2f_provenance(tmp_path):
    request, integration, stage3 = seeded(tmp_path)
    result = validate_selected_opportunity(
        request, integration_store=integration, stage3_store=stage3
    )
    assert result.status is DryRunStageStatus.COMPLETED
    assert result.output_ids == ("opp-1", "hyp-1", "score-1")


def test_portfolio_monitor_cannot_be_selected(tmp_path):
    request, integration, stage3 = seeded(
        tmp_path, kind=ResearchHypothesisKind.PORTFOLIO_MONITOR
    )
    result = validate_selected_opportunity(
        request, integration_store=integration, stage3_store=stage3
    )
    assert result.status is DryRunStageStatus.BLOCKED
    assert result.reason is DryRunTerminalReason.INVALID_SELECTION


def test_snapshot_mismatch_blocks_before_downstream_work(tmp_path):
    request, integration, stage3 = seeded(tmp_path)
    request = request.model_copy(update={"portfolio_snapshot_id": "snapshot-2"})
    result = validate_selected_opportunity(
        request, integration_store=integration, stage3_store=stage3
    )
    assert result.status is DryRunStageStatus.BLOCKED
    assert result.reason is DryRunTerminalReason.PORTFOLIO_SNAPSHOT_MISMATCH
