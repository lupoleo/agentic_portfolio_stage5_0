from datetime import datetime, timezone

from app.e2e.stage4_adapters import ExistingStage4ServicesAdapter
from app.e2e.stage4_contracts import (
    Stage4E2ERequest,
    Stage4Mode,
    Stage4RunStatus,
    Stage4Stage,
    Stage4StageResult,
    Stage4StageStatus,
    Stage4TerminalReason,
)
from app.e2e.stage4_service import Stage4E2EService
from app.e2e.stage4_store import Stage4E2EStore


NOW = datetime(2026, 9, 27, 8, 30, tzinfo=timezone.utc)


def request(*, mode=Stage4Mode.LIVE, selected=("opp-1",)):
    return Stage4E2ERequest(
        portfolio_file="data/input/portafoglio-export.xlsx",
        portfolio_file_fingerprint="portfolio-file-fp",
        account_state_id="account-001",
        scanner_configuration_id="scanner-config-v1",
        scanner_configuration_fingerprint="scanner-config-fp",
        as_of=NOW,
        mode=mode,
        selected_opportunity_ids=selected,
    )


class ControlledAdapters:
    def __init__(self, *, cache_miss=False, bad_watch_lineage=False, unsafe=False):
        self.cache_miss = cache_miss
        self.bad_watch_lineage = bad_watch_lineage
        self.unsafe = unsafe
        self.calls = []

    def adapter(self):
        return ExistingStage4ServicesAdapter(
            portfolio_analysis=self.portfolio,
            scanner_discovery=self.scanner,
            watch_universe_assembly=self.watch,
            research_integration=self.research,
            selected_opportunity_dry_run=self.dry_run,
        )

    def portfolio(self, req, run):
        self.calls.append(Stage4Stage.PORTFOLIO_ANALYSIS)
        if self.cache_miss and req.mode is Stage4Mode.CACHE_ONLY:
            return Stage4StageResult(
                stage=Stage4Stage.PORTFOLIO_ANALYSIS,
                status=Stage4StageStatus.PARTIAL,
                reason=Stage4TerminalReason.CACHE_ONLY_MISS,
                message="portfolio cache miss",
            )
        return self.completed(
            Stage4Stage.PORTFOLIO_ANALYSIS,
            {
                "portfolio_snapshot_id": "snapshot-001",
                "portfolio_risk_state_id": "risk-001",
            },
            "snapshot-001", "risk-001",
        )

    def scanner(self, req, run):
        self.calls.append(Stage4Stage.SCANNER_DISCOVERY)
        return self.completed(
            Stage4Stage.SCANNER_DISCOVERY,
            {"scanner_run_id": "scanner-001"},
            "scanner-001",
        )

    def watch(self, req, run):
        self.calls.append(Stage4Stage.WATCH_UNIVERSE_ASSEMBLY)
        return self.completed(
            Stage4Stage.WATCH_UNIVERSE_ASSEMBLY,
            {
                "watch_universe_run_id": "watch-001",
                "watch_universe_fingerprint": "watch-fp",
                "watch_universe_report_path": "watch-001.json",
                "portfolio_snapshot_id": (
                    "wrong-snapshot" if self.bad_watch_lineage
                    else run.portfolio_snapshot_id
                ),
            },
            "watch-001",
        )

    def research(self, req, run):
        self.calls.append(Stage4Stage.RESEARCH_INTEGRATION)
        return self.completed(
            Stage4Stage.RESEARCH_INTEGRATION,
            {
                "research_run_id": "research-001",
                "watch_universe_run_id": run.watch_universe_run_id,
                "watch_universe_fingerprint": run.watch_universe_fingerprint,
                "portfolio_snapshot_id": run.portfolio_snapshot_id,
                "opportunity_ids": ["opp-1"],
            },
            "research-001", "opp-1",
        )

    def dry_run(self, req, run):
        self.calls.append(Stage4Stage.SELECTED_OPPORTUNITY_DRY_RUN)
        return self.completed(
            Stage4Stage.SELECTED_OPPORTUNITY_DRY_RUN,
            {
                "selected_opportunity_dry_run_id": "s2g-001",
                "research_run_id": run.research_run_id,
                "opportunity_id": run.selected_opportunity_id,
                "portfolio_snapshot_id": run.portfolio_snapshot_id,
                "execution_plan_id": "plan-001",
                "dry_run": True,
                "execution_authorized": False,
                "broker_orders_submitted": 1 if self.unsafe else 0,
                "portfolio_mutations": 0,
                "automatic_executions": 0,
            },
            "s2g-001", "plan-001",
        )

    @staticmethod
    def completed(stage, payload, *ids):
        return Stage4StageResult(
            stage=stage,
            status=Stage4StageStatus.COMPLETED,
            output_ids=tuple(ids),
            output_payload=payload,
            message="controlled acceptance",
        )


def service(tmp_path, adapters):
    return Stage4E2EService(
        store=Stage4E2EStore(tmp_path / "stage4.db"),
        adapters=adapters.adapter(),
    )


def test_controlled_complete_path_preserves_lineage_and_zero_side_effects(tmp_path):
    adapters = ControlledAdapters()
    value = service(tmp_path, adapters).run(request(), now=NOW)
    assert value.status is Stage4RunStatus.COMPLETED
    assert value.terminal_reason is Stage4TerminalReason.E2E_DRY_RUN_COMPLETED
    assert value.portfolio_snapshot_id == "snapshot-001"
    assert value.watch_universe_run_id == "watch-001"
    assert value.watch_universe_report_path == "watch-001.json"
    assert value.research_run_id == "research-001"
    assert value.selected_opportunity_id == "opp-1"
    assert value.execution_plan_id == "plan-001"
    assert value.execution_authorized is False
    assert value.broker_orders_submitted == 0
    assert value.portfolio_mutations == 0
    assert value.automatic_executions == 0
    assert value.completed_stages == tuple(Stage4Stage)


def test_terminal_replay_is_immutable_and_does_not_call_adapters(tmp_path):
    adapters = ControlledAdapters()
    target = service(tmp_path, adapters)
    first = target.run(request(), now=NOW)
    call_count = len(adapters.calls)
    second = target.run(request(), now=NOW)
    assert second == first
    assert len(adapters.calls) == call_count


def test_missing_selection_waits_after_research(tmp_path):
    adapters = ControlledAdapters()
    value = service(tmp_path, adapters).run(request(selected=()), now=NOW)
    assert value.status is Stage4RunStatus.WAITING_FOR_OPERATOR_SELECTION
    assert value.terminal_reason is Stage4TerminalReason.WAITING_FOR_OPERATOR_SELECTION
    assert value.research_run_id == "research-001"
    assert Stage4Stage.SELECTED_OPPORTUNITY_DRY_RUN not in adapters.calls


def test_unknown_selection_is_blocked_fail_closed(tmp_path):
    adapters = ControlledAdapters()
    value = service(tmp_path, adapters).run(
        request(selected=("unknown",)), now=NOW,
    )
    assert value.status is Stage4RunStatus.BLOCKED
    assert value.terminal_reason is Stage4TerminalReason.LINEAGE_MISMATCH


def test_cache_only_miss_is_zero_network_and_resumable_live(tmp_path):
    adapters = ControlledAdapters(cache_miss=True)
    target = service(tmp_path, adapters)
    cached = target.run(request(mode=Stage4Mode.CACHE_ONLY), now=NOW)
    assert cached.status is Stage4RunStatus.PARTIAL
    assert cached.terminal_reason is Stage4TerminalReason.CACHE_ONLY_MISS
    assert cached.network_calls == 0
    live = target.run(request(mode=Stage4Mode.LIVE), now=NOW)
    assert live.status is Stage4RunStatus.COMPLETED


def test_lineage_mismatch_blocks_before_research(tmp_path):
    adapters = ControlledAdapters(bad_watch_lineage=True)
    value = service(tmp_path, adapters).run(request(), now=NOW)
    assert value.status is Stage4RunStatus.BLOCKED
    assert value.terminal_reason is Stage4TerminalReason.LINEAGE_MISMATCH
    assert Stage4Stage.RESEARCH_INTEGRATION not in adapters.calls


def test_downstream_safety_violation_is_blocked(tmp_path):
    adapters = ControlledAdapters(unsafe=True)
    value = service(tmp_path, adapters).run(request(), now=NOW)
    assert value.status is Stage4RunStatus.BLOCKED
    assert value.terminal_reason is Stage4TerminalReason.SAFETY_INVARIANT_VIOLATION


class PartialResearchAdapters(ControlledAdapters):
    def research(self, req, run):
        self.calls.append(Stage4Stage.RESEARCH_INTEGRATION)
        return Stage4StageResult(
            stage=Stage4Stage.RESEARCH_INTEGRATION,
            status=Stage4StageStatus.PARTIAL,
            reason=Stage4TerminalReason.UPSTREAM_PARTIAL,
            output_ids=("research-partial-001",),
            output_payload={
                "research_run_id": "research-partial-001",
                "watch_universe_run_id": (
                    run.watch_universe_run_id
                ),
                "watch_universe_fingerprint": (
                    run.watch_universe_fingerprint
                ),
                "portfolio_snapshot_id": (
                    run.portfolio_snapshot_id
                ),
                "opportunity_ids": [],
                "research_status": "PARTIAL",
            },
            message="research integration remains partial",
            diagnostics=("MONITOR_ONLY=4",),
        )


def test_partial_research_preserves_lineage_and_attempt_evidence(
    tmp_path,
):
    adapters = PartialResearchAdapters()
    store = Stage4E2EStore(tmp_path / "stage4.db")
    target = Stage4E2EService(
        store=store,
        adapters=adapters.adapter(),
    )

    value = target.run(request(), now=NOW)

    assert value.status is Stage4RunStatus.PARTIAL
    assert (
        value.terminal_reason
        is Stage4TerminalReason.UPSTREAM_PARTIAL
    )
    assert value.research_run_id == "research-partial-001"
    assert value.selectable_opportunity_ids == ()
    assert (
        Stage4Stage.RESEARCH_INTEGRATION
        not in value.completed_stages
    )
    assert store.get_stage(
        value.run_id,
        Stage4Stage.RESEARCH_INTEGRATION,
    ) is None

    attempts = store.list_attempts(value.run_id)

    assert len(attempts) == 1
    assert (
        attempts[0].stage
        is Stage4Stage.RESEARCH_INTEGRATION
    )
    assert (
        attempts[0].status
        is Stage4StageStatus.PARTIAL
    )
    assert attempts[0].output_ids == (
        "research-partial-001",
    )
    assert attempts[0].diagnostics == (
        "MONITOR_ONLY=4",
    )
