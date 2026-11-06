"""LIVE/CACHE_ONLY executor for E2E-S4.0A.1 candidate waves."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from pydantic import Field, field_validator

from app.e2e.stage4_contracts import (
    Stage4E2ERequest,
    Stage4Mode,
    Stage4Model,
)
from app.e2e.stage4_replenishment_contracts import (
    CandidateReplenishmentSession,
    CandidateWavePlan,
    CandidateWaveRecord,
    CandidateWaveStatus,
    candidate_wave_record_fingerprint,
)
from app.e2e.stage4_replenishment_service import CandidateWaveExecution
from app.e2e.stage4_runtime import (
    CanonicalStage4Runtime,
    Stage4RuntimeConfiguration,
    file_fingerprint,
)
from app.e2e.stage4_service import Stage4E2EService
from app.e2e.stage4_store import Stage4E2EStore
from app.scanner.ecb_session_fx import ECBSessionFXProvider
from app.scanner.exchange_session_calendar import ExchangeSessionCalendarProvider
from app.scanner.listing_start_reference import ListingStartReferenceRegistry
from app.scanner.research_integration_store import ScannerResearchIntegrationStore
from app.scanner.yahoo_history_snapshot import YahooHistorySnapshotProvider
from tools.live_scanner_history_quality import run_pilot


DIRECTIONAL_KINDS = frozenset({"NEW_LONG", "NEW_SHORT"})
TERMINAL_STATUSES = frozenset({
    "RESEARCHED", "OPPORTUNITY_CREATED", "EXCLUDED",
})


class DirectionalOutcomeInvariantError(RuntimeError):
    """A STANDARD wave did not preserve symmetric directional outcomes."""


class Stage4ReplenishmentRuntimeConfiguration(Stage4Model):
    database_path: str = "data/state/portfolio_cio.db"
    portfolio_file: str
    account_state_id: str
    portfolio_snapshot_id: str
    portfolio_risk_state_id: str
    eligibility_report_path: str
    mapping_report_path: str
    history_output_directory: str = (
        "data/cache/e2e/stage4/candidate_replenishment/history"
    )
    child_output_directory: str = (
        "data/cache/e2e/stage4/candidate_replenishment/children"
    )
    listing_references_path: str | None = None
    model_name: str = "qwen3:8b"
    timeout_seconds: float = Field(default=600.0, gt=0)
    history_timeout_seconds: float = Field(default=20.0, gt=0)
    pause_seconds: float = Field(default=2.0, ge=0, le=30)
    lookback_days: int = Field(default=365, ge=90, le=730)
    max_hypotheses: int = Field(default=4, ge=4)

    @field_validator(
        "database_path", "portfolio_file", "account_state_id",
        "portfolio_snapshot_id", "portfolio_risk_state_id",
        "eligibility_report_path", "mapping_report_path",
        "history_output_directory", "child_output_directory",
        "model_name", mode="before",
    )
    @classmethod
    def nonblank(cls, value):
        result = str(value).strip()
        if not result:
            raise ValueError("runtime fields must be nonblank")
        return result


class CanonicalStage4CandidateWaveExecutor:
    def __init__(
        self,
        configuration: Stage4ReplenishmentRuntimeConfiguration,
        *,
        history_runner: Callable | None = None,
        child_runner: Callable | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.configuration = configuration
        self.integration_store = ScannerResearchIntegrationStore(
            configuration.database_path
        )
        self.stage4_store = Stage4E2EStore(configuration.database_path)
        self.history_runner = history_runner or self._run_history
        self.child_runner = child_runner or self._run_child
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def execute(self, session, plan, *, now):
        started_at = self.clock()
        if started_at.utcoffset() is None:
            raise ValueError("wave clock must be timezone-aware")
        if session.mode is Stage4Mode.CACHE_ONLY:
            outcomes = self._synthetic_outcomes(
                plan,
                status="PENDING",
                reason="CACHE_ONLY_MISS",
            )
            record = self._record(
                session, plan,
                status=CandidateWaveStatus.PARTIAL,
                started_at=started_at,
                completed_at=started_at,
                terminal_reason="CACHE_ONLY_MISS",
                diagnostics=("network_calls=0",),
            )
            return CandidateWaveExecution(record, outcomes)

        try:
            history_path = Path(self.history_runner(plan, started_at))
            (
                standard_listings,
                review_listings,
                history_diagnostics,
            ) = self._partition_history_routes(plan, history_path)

            review_outcomes = self._synthetic_outcomes_for_listings(
                review_listings,
                status="EXCLUDED",
                reason="EVIDENCE_UNAVAILABLE",
            )
            child = None
            child_outcomes = ()
            if standard_listings:
                child, child_outcomes = self.child_runner(
                    session, plan, history_path, started_at,
                )
                child_outcomes = tuple(
                    value for value in child_outcomes
                    if self._value(getattr(value, "kind", ""))
                    in DIRECTIONAL_KINDS
                )
                expected_child = len(standard_listings) * 2
                if len(child_outcomes) != expected_child:
                    raise DirectionalOutcomeInvariantError(
                        "directional child outcome count differs from "
                        "STANDARD history routes"
                    )

            outcomes = tuple(child_outcomes) + tuple(review_outcomes)
            expected = len(plan.selected_listings) * 2
            if len(outcomes) != expected:
                raise DirectionalOutcomeInvariantError(
                    "directional outcome count differs from symmetric wave"
                )
            terminal = all(
                self._value(getattr(value, "status", ""))
                in TERMINAL_STATUSES
                for value in outcomes
            )
            opportunities = tuple(sorted({
                str(getattr(value, "opportunity_id", "") or "").strip()
                for value in outcomes
                if str(getattr(value, "opportunity_id", "") or "").strip()
            }))
            completed_at = self.clock()
            child_terminal_reason = None
            if child is not None and child.terminal_reason:
                child_terminal_reason = self._value(
                    child.terminal_reason
                )
            terminal_reason = child_terminal_reason
            if child is None and review_listings:
                terminal_reason = "EVIDENCE_UNAVAILABLE"
            record = self._record(
                session, plan,
                status=(
                    CandidateWaveStatus.COMPLETED
                    if terminal else CandidateWaveStatus.PARTIAL
                ),
                started_at=started_at,
                completed_at=completed_at,
                child_run_id=(child.run_id if child is not None else None),
                scanner_run_id=(
                    child.scanner_run_id if child is not None else None
                ),
                watch_universe_run_id=(
                    child.watch_universe_run_id
                    if child is not None else None
                ),
                research_run_id=(
                    child.research_run_id if child is not None else None
                ),
                opportunity_ids=opportunities,
                terminal_reason=terminal_reason,
                diagnostics=(
                    f"history_report={history_path}",
                    *history_diagnostics,
                    f"directional_outcome_count={len(outcomes)}",
                ),
            )
            return CandidateWaveExecution(record, outcomes)
        except DirectionalOutcomeInvariantError as exc:
            completed_at = self.clock()
            outcomes = self._synthetic_outcomes(
                plan,
                status="FAILED",
                reason="ORCHESTRATION_INVARIANT_VIOLATION",
            )
            record = self._record(
                session, plan,
                status=CandidateWaveStatus.BLOCKED,
                started_at=started_at,
                completed_at=completed_at,
                terminal_reason="ORCHESTRATION_INVARIANT_VIOLATION",
                diagnostics=(f"{type(exc).__name__}: {exc}",),
            )
            return CandidateWaveExecution(record, outcomes)
        except Exception as exc:
            completed_at = self.clock()
            outcomes = self._synthetic_outcomes(
                plan,
                status="FAILED",
                reason="PROCESSING_FAILED",
            )
            record = self._record(
                session, plan,
                status=CandidateWaveStatus.PARTIAL,
                started_at=started_at,
                completed_at=completed_at,
                terminal_reason="PROCESSING_FAILED",
                diagnostics=(f"{type(exc).__name__}: {exc}",),
            )
            return CandidateWaveExecution(record, outcomes)

    def resume(self, record):
        if record.research_run_id:
            outcomes = tuple(
                value
                for value in self.integration_store.list_outcomes(
                    record.research_run_id
                )
                if self._value(getattr(value, "kind", ""))
                in DIRECTIONAL_KINDS
            )
        elif record.terminal_reason == "CACHE_ONLY_MISS":
            outcomes = self._synthetic_from_record(
                record, "PENDING", "CACHE_ONLY_MISS"
            )
        elif (
            record.terminal_reason
            == "ORCHESTRATION_INVARIANT_VIOLATION"
        ):
            outcomes = self._synthetic_from_record(
                record,
                "FAILED",
                "ORCHESTRATION_INVARIANT_VIOLATION",
            )
        else:
            outcomes = self._synthetic_from_record(
                record, "FAILED", "PROCESSING_FAILED"
            )
        return CandidateWaveExecution(record, outcomes)

    def _run_history(self, plan, now):
        eligibility = json.loads(
            Path(self.configuration.eligibility_report_path).read_text(
                encoding="utf-8-sig"
            )
        )
        output = Path(self.configuration.history_output_directory)
        output.mkdir(parents=True, exist_ok=True)
        registry = (
            ListingStartReferenceRegistry.load(
                self.configuration.listing_references_path
            )
            if self.configuration.listing_references_path else None
        )
        code = run_pilot(
            eligibility,
            venues=sorted({
                value.exchange for value in plan.selected_listings
            }),
            output=output,
            history_provider=YahooHistorySnapshotProvider(
                timeout_seconds=self.configuration.history_timeout_seconds,
                now=lambda: now,
            ),
            calendar_provider=ExchangeSessionCalendarProvider(),
            fx_provider=ECBSessionFXProvider(
                timeout_seconds=self.configuration.history_timeout_seconds,
                now=lambda: now,
            ),
            now=lambda: now,
            pause_seconds=self.configuration.pause_seconds,
            lookback_days=self.configuration.lookback_days,
            listing_references=registry,
            listing_keys=[
                value.listing_key for value in plan.selected_listings
            ],
        )
        path = output / (
            "scanner_history_quality_"
            + now.strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )
        if not path.is_file():
            raise RuntimeError(
                f"history wave produced no report; exit code {code}"
            )
        report = json.loads(path.read_text(encoding="utf-8-sig"))
        if code not in (0, 2):
            raise RuntimeError(f"history wave failed with exit code {code}")
        if self._value(report.get("run_status", "")) != "COMPLETED":
            raise RuntimeError(
                "history wave report is not COMPLETED"
            )
        return path

    @classmethod
    def _partition_history_routes(cls, plan, history_path):
        report = json.loads(
            Path(history_path).read_text(encoding="utf-8-sig")
        )
        if cls._value(report.get("run_status", "")) != "COMPLETED":
            raise RuntimeError("history route report is not COMPLETED")
        results = report.get("results", ())
        if isinstance(results, dict):
            results = tuple(results.values())
        if not isinstance(results, (list, tuple)):
            raise RuntimeError("history results container is invalid")

        expected = {
            value.listing_key: value
            for value in plan.selected_listings
        }
        routes = {}
        for result in results:
            if not isinstance(result, dict):
                raise RuntimeError("history result is not an object")
            quality = result.get("quality")
            if quality is None:
                # The history tool records a listing whose snapshot could not
                # be acquired (or whose listing reference is unusable) with
                # quality=None. Identify it from its source decision and route
                # it terminally as UNAVAILABLE instead of failing the wave.
                source = result.get("source_decision") or {}
                exchange = cls._value(source.get("exchange", ""))
                symbol = cls._value(source.get("symbol", ""))
                route_override = "UNAVAILABLE"
            else:
                identity = quality.get("listing_key") or {}
                exchange = cls._value(identity.get("exchange", ""))
                symbol = cls._value(identity.get("symbol", ""))
                route_override = None
            listing_key = f"{exchange}:{symbol}"
            if listing_key not in expected:
                raise RuntimeError(
                    f"unexpected history listing result: {listing_key}"
                )
            if listing_key in routes:
                raise RuntimeError(
                    f"duplicate history listing result: {listing_key}"
                )
            route = route_override or cls._value(quality.get("route", ""))
            if route not in {"STANDARD", "REVIEW_REQUIRED", "BLOCKED", "UNAVAILABLE"}:
                raise RuntimeError(
                    f"unsupported history route for {listing_key}: {route}"
                )
            routes[listing_key] = route

        missing = tuple(sorted(set(expected) - set(routes)))
        if missing:
            raise RuntimeError(
                "history results missing selected listings: "
                + ", ".join(missing)
            )
        standard = tuple(
            value for value in plan.selected_listings
            if routes[value.listing_key] == "STANDARD"
        )
        review = tuple(
            value for value in plan.selected_listings
            if routes[value.listing_key] in {"REVIEW_REQUIRED", "BLOCKED", "UNAVAILABLE"}
        )
        diagnostics = (
            f"history_standard_count={len(standard)}",
            f"history_terminal_review_count={len(review)}",
            "history_unavailable_count=" + str(sum(
                1 for route in routes.values() if route == "UNAVAILABLE"
            )),
            "history_routes=" + ",".join(
                f"{key}={routes[key]}" for key in sorted(routes)
            ),
        )
        return standard, review, diagnostics

    def _run_child(self, session, plan, history_path, now):
        configuration = Stage4RuntimeConfiguration(
            database_path=self.configuration.database_path,
            portfolio_snapshot_id=self.configuration.portfolio_snapshot_id,
            portfolio_risk_state_id=self.configuration.portfolio_risk_state_id,
            eligibility_report_path=self.configuration.eligibility_report_path,
            mapping_report_path=self.configuration.mapping_report_path,
            history_report_paths=(str(history_path),),
            watch_output_directory=str(
                Path(self.configuration.child_output_directory)
                / "watch_universe"
            ),
            model_name=self.configuration.model_name,
            timeout_seconds=self.configuration.timeout_seconds,
            max_hypotheses=self.configuration.max_hypotheses,
        )
        request = Stage4E2ERequest(
            portfolio_file=self.configuration.portfolio_file,
            portfolio_file_fingerprint=file_fingerprint(
                self.configuration.portfolio_file
            ),
            account_state_id=self.configuration.account_state_id,
            scanner_configuration_id=(
                "stage4-bounded-replenishment-"
                + plan.fingerprint[:16]
            ),
            scanner_configuration_fingerprint=configuration.fingerprint,
            as_of=now,
            mode=Stage4Mode.LIVE,
        )
        runtime = CanonicalStage4Runtime(configuration)
        child = Stage4E2EService(
            store=self.stage4_store,
            adapters=runtime.adapters(),
        ).run(request, now=now)
        outcomes = (
            self.integration_store.list_outcomes(child.research_run_id)
            if child.research_run_id else ()
        )
        return child, outcomes

    @classmethod
    def _synthetic_outcomes(cls, plan, *, status, reason):
        return cls._synthetic_outcomes_for_listings(
            plan.selected_listings,
            status=status,
            reason=reason,
        )

    @staticmethod
    def _synthetic_outcomes_for_listings(
        listings, *, status, reason,
    ):
        return tuple(
            SimpleNamespace(
                listing_key=listing.listing_key,
                kind=kind,
                status=status,
                reason=reason,
                opportunity_id=None,
            )
            for listing in listings
            for kind in ("NEW_LONG", "NEW_SHORT")
        )

    @staticmethod
    def _synthetic_from_record(record, status, reason):
        return tuple(
            SimpleNamespace(
                listing_key=key,
                kind=kind,
                status=status,
                reason=reason,
                opportunity_id=None,
            )
            for key in record.listing_keys
            for kind in ("NEW_LONG", "NEW_SHORT")
        )

    @staticmethod
    def _value(value: Any) -> str:
        return str(getattr(value, "value", value)).strip().upper()

    @staticmethod
    def _record(
        session, plan, *, status, started_at, completed_at,
        child_run_id=None, scanner_run_id=None,
        watch_universe_run_id=None, research_run_id=None,
        opportunity_ids=(), terminal_reason=None, diagnostics=(),
    ):
        payload = {
            "session_id": session.session_id,
            "wave_index": plan.wave_index,
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "plan_fingerprint": plan.fingerprint,
            "listing_keys": tuple(
                value.listing_key for value in plan.selected_listings
            ),
            "child_run_id": child_run_id,
            "scanner_run_id": scanner_run_id,
            "watch_universe_run_id": watch_universe_run_id,
            "research_run_id": research_run_id,
            "opportunity_ids": tuple(opportunity_ids),
            "terminal_reason": terminal_reason,
            "diagnostics": tuple(diagnostics),
        }
        fingerprint = candidate_wave_record_fingerprint(payload)
        return CandidateWaveRecord(
            record_id="wave-" + fingerprint[:24],
            fingerprint=fingerprint,
            **payload,
        )
