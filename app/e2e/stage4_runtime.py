"""Concrete adapters that compose the frozen S2.2E/F/G services."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.ai.canonical_technical import build_canonical_technical_input
from app.ai.analyst_evidence_provider import YahooAnalystEvidenceProvider
from app.ai.company_assessment import CompanyAssessmentService
from app.ai.fundamental_evidence_provider import YahooFundamentalEvidenceProvider
from app.ai.local_provider import LocalProvider
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.opportunity_scoring_service import OpportunityScoringService
from app.ai.research_service import ResearchService
from app.cio.portfolio_marginal_risk_provider import (
    build_canonical_portfolio_filter_service,
)
from app.cio.portfolio_simulation_factory import (
    build_canonical_portfolio_simulation_service,
)
from app.cio.storage import Stage3Store
from app.e2e.stage4_adapters import ExistingStage4ServicesAdapter
from app.e2e.stage4_contracts import (
    Stage4E2ERequest,
    Stage4E2ERun,
    Stage4Mode,
    Stage4Model,
    Stage4Stage,
    Stage4StageResult,
    Stage4StageStatus,
    Stage4TerminalReason,
    canonical_fingerprint,
)
from app.portfolio.fineco_importer import load_fineco_positions
from app.scanner.research_integration import load_watch_universe_report
from app.scanner.research_integration_contracts import IntegrationRunStatus
from app.scanner.research_integration_service import ScannerResearchIntegrationService
from app.scanner.research_integration_store import ScannerResearchIntegrationStore
from app.scanner.selected_opportunity_dry_run import (
    CanonicalSelectedOpportunityExecutor,
)
from app.scanner.selected_opportunity_dry_run_contracts import (
    DryRunMode,
    DryRunStatus,
    SelectedOpportunityDryRunRequest,
    SelectedOpportunityParameters,
)
from app.scanner.selected_opportunity_dry_run_service import (
    SelectedOpportunityDryRunService,
)
from app.scanner.selected_opportunity_dry_run_store import (
    SelectedOpportunityDryRunStore,
)
from tools.audit_scanner_watch_universe import build_watch_universe_audit


def file_fingerprint(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class Stage4RuntimeConfiguration(Stage4Model):
    database_path: str = "data/state/portfolio_cio.db"
    portfolio_snapshot_id: str
    portfolio_risk_state_id: str
    eligibility_report_path: str
    mapping_report_path: str
    history_report_paths: tuple[str, ...]
    watch_output_directory: str = "data/cache/e2e/stage4/watch_universe"
    model_name: str = "qwen3:8b"
    timeout_seconds: float = Field(default=600.0, gt=0)
    max_hypotheses: int | None = Field(default=None, ge=1)
    selected_parameters: dict[str, Any] | None = None

    @field_validator(
        "database_path", "portfolio_snapshot_id", "portfolio_risk_state_id",
        "eligibility_report_path", "mapping_report_path",
        "watch_output_directory", "model_name", mode="before",
    )
    @classmethod
    def nonblank(cls, value):
        result = str(value).strip()
        if not result:
            raise ValueError("runtime identity and path fields must be nonblank")
        return result

    @model_validator(mode="after")
    def validate_history(self):
        values = tuple(str(value).strip() for value in self.history_report_paths)
        if not values or any(not value for value in values):
            raise ValueError("at least one explicit history report is required")
        object.__setattr__(self, "history_report_paths", values)
        return self

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self)


class CanonicalStage4Runtime:
    def __init__(self, configuration: Stage4RuntimeConfiguration) -> None:
        self.configuration = configuration
        self.stage3_store = Stage3Store(configuration.database_path)
        self.integration_store = ScannerResearchIntegrationStore(
            configuration.database_path
        )
        self.dry_run_store = SelectedOpportunityDryRunStore(
            configuration.database_path
        )

    def adapters(self) -> ExistingStage4ServicesAdapter:
        return ExistingStage4ServicesAdapter(
            portfolio_analysis=self.portfolio_analysis,
            scanner_discovery=self.scanner_discovery,
            watch_universe_assembly=self.watch_universe_assembly,
            research_integration=self.research_integration,
            selected_opportunity_dry_run=self.selected_opportunity_dry_run,
        )

    def portfolio_analysis(self, request, run) -> Stage4StageResult:
        snapshot = self.stage3_store.get_portfolio_snapshot(
            self.configuration.portfolio_snapshot_id
        )
        risk = self.stage3_store.get_portfolio_risk_state(
            self.configuration.portfolio_risk_state_id
        )
        account = self.stage3_store.get_account_state(request.account_state_id)
        if snapshot is None or risk is None or account is None:
            return self._blocked(
                Stage4Stage.PORTFOLIO_ANALYSIS,
                Stage4TerminalReason.UPSTREAM_BLOCKED,
                "explicit PortfolioSnapshot, risk state or AccountState is missing",
            )
        if snapshot.source_file_hash != request.portfolio_file_fingerprint:
            return self._lineage(
                Stage4Stage.PORTFOLIO_ANALYSIS,
                "PortfolioSnapshot file fingerprint differs from the request",
            )
        if snapshot.account_state_id != request.account_state_id:
            return self._lineage(
                Stage4Stage.PORTFOLIO_ANALYSIS,
                "PortfolioSnapshot AccountState differs from the request",
            )
        if risk.snapshot_id != snapshot.snapshot_id:
            return self._lineage(
                Stage4Stage.PORTFOLIO_ANALYSIS,
                "PortfolioRiskState belongs to another snapshot",
            )
        return self._completed(
            Stage4Stage.PORTFOLIO_ANALYSIS,
            (snapshot.snapshot_id, risk.risk_state_id, account.account_state_id),
            {
                "portfolio_snapshot_id": snapshot.snapshot_id,
                "portfolio_risk_state_id": risk.risk_state_id,
                "account_state_id": account.account_state_id,
            },
            "explicit current portfolio state validated",
        )

    def scanner_discovery(self, request, run) -> Stage4StageResult:
        eligibility = _load_json(self.configuration.eligibility_report_path)
        mapping = _load_json(self.configuration.mapping_report_path)
        histories = tuple(
            _load_json(path) for path in self.configuration.history_report_paths
        )
        if eligibility.get("audit_id") != "E2E-S2.2B":
            return self._blocked(
                Stage4Stage.SCANNER_DISCOVERY,
                Stage4TerminalReason.UPSTREAM_BLOCKED,
                "eligibility report is not E2E-S2.2B",
            )
        if mapping.get("audit_id") != "E2E-S2.2C-MAPPING":
            return self._blocked(
                Stage4Stage.SCANNER_DISCOVERY,
                Stage4TerminalReason.UPSTREAM_BLOCKED,
                "mapping report is not E2E-S2.2C-MAPPING",
            )
        if any(
            value.get("audit_id") != "E2E-S2.2D-HISTORY-PILOT"
            for value in histories
        ):
            return self._blocked(
                Stage4Stage.SCANNER_DISCOVERY,
                Stage4TerminalReason.UPSTREAM_BLOCKED,
                "one or more history reports are not E2E-S2.2D",
            )
        sources = self._source_fingerprints(request)
        scanner_fp = canonical_fingerprint({
            "configuration": request.scanner_configuration_fingerprint,
            "sources": sources,
            "as_of": request.as_of.isoformat(),
        })
        scanner_id = "s4a-scan-" + scanner_fp[:24]
        return self._completed(
            Stage4Stage.SCANNER_DISCOVERY,
            (scanner_id,),
            {
                "scanner_run_id": scanner_id,
                "scanner_fingerprint": scanner_fp,
                "source_fingerprints": sources,
            },
            "explicit S2.2B/C/D reports validated",
        )

    def watch_universe_assembly(self, request, run) -> Stage4StageResult:
        paths = self._source_paths(request)
        sources = tuple(
            (f"source_{index:03d}:{path.name}", file_fingerprint(path))
            for index, path in enumerate(paths)
        )
        report = build_watch_universe_audit(
            _load_json(self.configuration.eligibility_report_path),
            _load_json(self.configuration.mapping_report_path),
            tuple(_load_json(path) for path in self.configuration.history_report_paths),
            tuple(load_fineco_positions(request.portfolio_file)),
            portfolio_snapshot_id=run.portfolio_snapshot_id,
            as_of=request.as_of,
            assembled_at=request.as_of,
            source_fingerprints=sources,
        )
        universe = report["universe"]
        output = Path(self.configuration.watch_output_directory)
        path = output / f"scanner_watch_universe_{universe['run_id']}.json"
        _atomic_json(path, report)
        return self._completed(
            Stage4Stage.WATCH_UNIVERSE_ASSEMBLY,
            (universe["run_id"],),
            {
                "watch_universe_run_id": universe["run_id"],
                "watch_universe_fingerprint": universe["fingerprint"],
                "watch_universe_report_path": str(path),
                "portfolio_snapshot_id": universe["portfolio_snapshot_id"],
                "candidate_count": report["summary"]["candidate_count"],
                "research_member_count": report["summary"]["research_member_count"],
            },
            "S2.2E assembled with zero network calls",
        )

    def research_integration(self, request, run) -> Stage4StageResult:
        universe = load_watch_universe_report(run.watch_universe_report_path)
        cache_only = request.mode is Stage4Mode.CACHE_ONLY
        if cache_only:
            research_service = scoring_service = market = news = None
            fundamental = analyst = None
            technical_loader = None
        else:
            provider = LocalProvider(
                model_name=self.configuration.model_name,
                timeout_seconds=self.configuration.timeout_seconds,
            )
            research_service = ResearchService(provider)
            scoring_service = OpportunityScoringService(
                provider,
                company_assessment_service=CompanyAssessmentService(provider),
            )
            market = YahooMarketEvidenceProvider()
            news = YahooNewsEvidenceProvider()
            fundamental = YahooFundamentalEvidenceProvider()
            analyst = YahooAnalystEvidenceProvider()
            technical_loader = build_canonical_technical_input
        service = ScannerResearchIntegrationService(
            stage3_store=self.stage3_store,
            integration_store=self.integration_store,
            research_service=research_service,
            scoring_service=scoring_service,
            market_provider=market,
            news_provider=news,
            fundamental_provider=fundamental,
            analyst_provider=analyst,
            canonical_technical_loader=technical_loader,
        )
        result = service.run(
            universe,
            now=request.as_of,
            max_hypotheses=self.configuration.max_hypotheses,
            cache_only=cache_only,
        )
        payload = {
            "research_run_id": result.run_id,
            "watch_universe_run_id": result.watch_universe_run_id,
            "watch_universe_fingerprint": result.watch_universe_fingerprint,
            "portfolio_snapshot_id": result.portfolio_snapshot_id,
            "opportunity_ids": list(result.opportunity_ids),
            "research_status": result.status.value,
        }
        if result.status is IntegrationRunStatus.COMPLETED:
            return self._completed(
                Stage4Stage.RESEARCH_INTEGRATION,
                (result.run_id, *result.opportunity_ids),
                payload,
                "S2.2F research integration completed",
            )
        outcomes = self.integration_store.list_outcomes(result.run_id)
        reasons = Counter(value.reason.value for value in outcomes)
        reason = (
            Stage4TerminalReason.CACHE_ONLY_MISS
            if reasons.get("CACHE_ONLY_MISS")
            else Stage4TerminalReason.UPSTREAM_PARTIAL
        )
        return Stage4StageResult(
            stage=Stage4Stage.RESEARCH_INTEGRATION,
            status=Stage4StageStatus.PARTIAL,
            reason=reason,
            output_ids=(result.run_id,),
            output_payload=payload,
            message=f"S2.2F stopped with {result.status.value}",
            diagnostics=tuple(f"{key}={value}" for key, value in sorted(reasons.items())),
        )

    def selected_opportunity_dry_run(self, request, run) -> Stage4StageResult:
        parameters = (
            SelectedOpportunityParameters(**self.configuration.selected_parameters)
            if self.configuration.selected_parameters is not None else None
        )
        dry_request = SelectedOpportunityDryRunRequest(
            scanner_research_run_id=run.research_run_id,
            opportunity_ids=(run.selected_opportunity_id,),
            portfolio_snapshot_id=run.portfolio_snapshot_id,
            as_of=request.as_of,
            mode=(
                DryRunMode.CACHE_ONLY
                if request.mode is Stage4Mode.CACHE_ONLY else DryRunMode.LIVE
            ),
            parameters=parameters,
        )
        executor = CanonicalSelectedOpportunityExecutor(
            stage3_store=self.stage3_store,
            integration_store=self.integration_store,
            portfolio_filter_service=build_canonical_portfolio_filter_service(
                self.stage3_store
            ),
            portfolio_simulation_service=build_canonical_portfolio_simulation_service(
                self.stage3_store
            ),
        )
        service = SelectedOpportunityDryRunService(
            store=self.dry_run_store,
            executor=executor,
        )
        result = service.run(dry_request, now=request.as_of)
        payload = {
            "selected_opportunity_dry_run_id": result.run_id,
            "research_run_id": result.scanner_research_run_id,
            "opportunity_id": result.opportunity_id,
            "portfolio_snapshot_id": result.portfolio_snapshot_id,
            "execution_plan_id": result.execution_plan_id,
            "dry_run": result.dry_run,
            "execution_authorized": result.execution_authorized,
            "broker_orders_submitted": result.broker_orders_submitted,
            "portfolio_mutations": result.portfolio_mutations,
            "automatic_executions": result.automatic_executions,
            "dry_run_status": result.status.value,
            "dry_run_terminal_reason": (
                result.terminal_reason.value if result.terminal_reason else None
            ),
        }
        if result.status is DryRunStatus.COMPLETED:
            return self._completed(
                Stage4Stage.SELECTED_OPPORTUNITY_DRY_RUN,
                tuple(value for value in (result.run_id, result.execution_plan_id) if value),
                payload,
                "S2.2G complete dry-run plan created",
            )
        return Stage4StageResult(
            stage=Stage4Stage.SELECTED_OPPORTUNITY_DRY_RUN,
            status=(
                Stage4StageStatus.BLOCKED
                if result.status is DryRunStatus.BLOCKED
                else Stage4StageStatus.FAILED
            ),
            reason=(
                Stage4TerminalReason.CACHE_ONLY_MISS
                if getattr(result.terminal_reason, "value", None) == "CACHE_ONLY_MISS"
                else Stage4TerminalReason.DOWNSTREAM_BLOCKED
            ),
            output_ids=(result.run_id,),
            output_payload=payload,
            message="S2.2G did not create an ExecutionPlan",
        )

    def _source_paths(self, request):
        return tuple(map(Path, (
            self.configuration.eligibility_report_path,
            self.configuration.mapping_report_path,
            *self.configuration.history_report_paths,
            request.portfolio_file,
        )))

    def _source_fingerprints(self, request):
        return tuple(
            (str(path), file_fingerprint(path))
            for path in self._source_paths(request)
        )

    @staticmethod
    def _completed(stage, output_ids, payload, message):
        return Stage4StageResult(
            stage=stage,
            status=Stage4StageStatus.COMPLETED,
            output_ids=tuple(output_ids),
            output_payload=payload,
            message=message,
        )

    @staticmethod
    def _blocked(stage, reason, message):
        return Stage4StageResult(
            stage=stage,
            status=Stage4StageStatus.BLOCKED,
            reason=reason,
            message=message,
        )

    @classmethod
    def _lineage(cls, stage, message):
        return cls._blocked(stage, Stage4TerminalReason.LINEAGE_MISMATCH, message)
