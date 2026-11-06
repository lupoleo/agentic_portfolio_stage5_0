"""Resumable bounded candidate replenishment orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Protocol

from app.e2e.stage4_replenishment import (
    build_candidate_frontier,
    classify_directional_outcomes,
    plan_candidate_wave,
)
from app.e2e.stage4_replenishment_contracts import (
    CandidateReplenishmentSession,
    CandidateWavePlan,
    CandidateWaveRecord,
    CandidateWaveStatus,
    ReplenishmentDisposition,
    ReplenishmentStopReason,
    normalize_listing_key,
)


@dataclass(frozen=True)
class CandidateWaveExecution:
    record: CandidateWaveRecord
    directional_outcomes: tuple[Any, ...]


@dataclass(frozen=True)
class CandidateReplenishmentResult:
    session_id: str
    root_run_id: str
    terminal_reason: ReplenishmentStopReason
    opportunity_ids: tuple[str, ...]
    attempted_listing_keys: tuple[str, ...]
    wave_record_ids: tuple[str, ...]
    retry_exhausted_outcome_count: int = 0
    quarantined_listing_keys: tuple[str, ...] = ()
    quarantined_hypothesis_ids: tuple[str, ...] = ()
    quarantine_reason_counts: tuple[str, ...] = ()
    source_research_run_ids: tuple[str, ...] = ()
    frontier_ranking_snapshot_id: str | None = None
    frontier_ranking_mode: str | None = None
    frontier_ranking_iteration_count: int = 0
    frontier_ranking_screened_count: int = 0
    frontier_ranking_qualified_count: int = 0
    frontier_ranking_fallback_count: int = 0
    frontier_ranking_network_calls: int = 0
    frontier_ranking_provider_circuit_open: bool = False
    broker_orders_submitted: int = 0
    portfolio_mutations: int = 0
    automatic_executions: int = 0

    @property
    def opportunity_found(self) -> bool:
        return bool(self.opportunity_ids)


class CandidateWaveExecutor(Protocol):
    def execute(
        self,
        session: CandidateReplenishmentSession,
        plan: CandidateWavePlan,
        *,
        now: datetime,
    ) -> CandidateWaveExecution: ...

    def resume(
        self,
        record: CandidateWaveRecord,
    ) -> CandidateWaveExecution: ...


class Stage4CandidateReplenishmentService:
    def __init__(
        self,
        *,
        store,
        executor: CandidateWaveExecutor,
        frontier_ranker=None,
    ) -> None:
        self.store = store
        self.executor = executor
        self.frontier_ranker = frontier_ranker

    def run(
        self,
        *,
        session: CandidateReplenishmentSession,
        eligibility,
        mapping,
        prior_outcomes=(),
        attempted_listing_keys=(),
        excluded_yahoo_symbols=(),
        current_listing_keys=(),
        retry_count=0,
        now: datetime | None = None,
    ) -> CandidateReplenishmentResult:
        now = now or datetime.now(timezone.utc)
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if now < session.as_of:
            raise ValueError("replenishment cannot precede session as_of")

        self.store.save_session(session)
        attempted = {
            normalize_listing_key(value)
            for value in attempted_listing_keys
        }
        excluded_yahoo = tuple(sorted({
            str(value).strip().upper()
            for value in excluded_yahoo_symbols
            if str(value).strip()
        }))
        current = tuple(
            normalize_listing_key(value)
            for value in current_listing_keys
        )
        outcomes = tuple(prior_outcomes)
        wave_index = 1
        wave_record_ids: list[str] = []
        retry_exhausted_outcome_count = 0
        quarantined_listing_keys: set[str] = set()
        quarantined_hypothesis_ids: set[str] = set()
        quarantine_reason_counts: Counter[str] = Counter()
        source_research_run_ids: set[str] = set()
        ranking = None
        frontier_priority_keys: tuple[str, ...] = ()
        initial_frontier = build_candidate_frontier(
            eligibility,
            mapping,
            attempted_listing_keys=attempted,
            excluded_yahoo_symbols=excluded_yahoo,
            policy=session.policy,
        )
        if self.frontier_ranker is not None and initial_frontier:
            ranking = self.frontier_ranker.rank(
                session=session,
                frontier=initial_frontier,
                eligibility=eligibility,
            )
            frontier_priority_keys = ranking.ordered_listing_keys

        while True:
            plan = plan_candidate_wave(
                session=session,
                wave_index=wave_index,
                eligibility=eligibility,
                mapping=mapping,
                prior_outcomes=outcomes,
                attempted_listing_keys=attempted,
                excluded_yahoo_symbols=excluded_yahoo,
                current_listing_keys=current,
                retry_count=retry_count,
                frontier_priority_keys=frontier_priority_keys,
            )

            if plan.disposition is ReplenishmentDisposition.STOP_FAIL_CLOSED:
                summary = classify_directional_outcomes(outcomes)
                return self._result(
                    session=session,
                    reason=plan.reason,
                    opportunity_ids=summary["opportunity_ids"],
                    attempted=attempted,
                    record_ids=wave_record_ids,
                    retry_exhausted_outcome_count=(
                        retry_exhausted_outcome_count
                    ),
                    quarantined_listing_keys=quarantined_listing_keys,
                    quarantined_hypothesis_ids=(
                        quarantined_hypothesis_ids
                    ),
                    quarantine_reason_counts=quarantine_reason_counts,
                    source_research_run_ids=source_research_run_ids,
                    ranking=ranking,
                )

            existing = self.store.get_wave_by_plan_fingerprint(
                session.session_id,
                plan.fingerprint,
            )
            execution = (
                self.executor.resume(existing)
                if existing is not None
                else self.executor.execute(session, plan, now=now)
            )
            self._validate_execution(session, plan, execution)
            self.store.save_wave(execution.record)
            wave_record_ids.append(execution.record.record_id)

            if plan.disposition is ReplenishmentDisposition.ADVANCE_FRONTIER:
                attempted.update(
                    value.listing_key
                    for value in plan.selected_listings
                )

            outcomes = tuple(execution.directional_outcomes)
            summary = classify_directional_outcomes(outcomes)
            if summary["opportunity_ids"]:
                return self._result(
                    session=session,
                    reason=(
                        ReplenishmentStopReason.SELECTABLE_OPPORTUNITY_FOUND
                    ),
                    opportunity_ids=summary["opportunity_ids"],
                    attempted=attempted,
                    record_ids=wave_record_ids,
                    retry_exhausted_outcome_count=(
                        retry_exhausted_outcome_count
                    ),
                    quarantined_listing_keys=quarantined_listing_keys,
                    quarantined_hypothesis_ids=(
                        quarantined_hypothesis_ids
                    ),
                    quarantine_reason_counts=quarantine_reason_counts,
                    source_research_run_ids=source_research_run_ids,
                    ranking=ranking,
                )

            current = tuple(
                value.listing_key
                for value in plan.selected_listings
            )
            if summary["retryable_count"]:
                if (
                    plan.retry_count
                    >= session.policy.max_transient_retries
                ):
                    evidence = self._retry_exhausted_evidence(
                        plan, outcomes,
                    )
                    retry_exhausted_outcome_count += evidence[
                        "outcome_count"
                    ]
                    quarantined_listing_keys.update(
                        evidence["listing_keys"]
                    )
                    quarantined_hypothesis_ids.update(
                        evidence["hypothesis_ids"]
                    )
                    quarantine_reason_counts.update(
                        evidence["reasons"]
                    )
                    if execution.record.research_run_id:
                        source_research_run_ids.add(
                            execution.record.research_run_id
                        )
                    current = ()
                    retry_count = (
                        session.policy.max_transient_retries
                    )
                    wave_index += 1
                else:
                    retry_count = plan.retry_count
            else:
                current = ()
                retry_count = 0
                wave_index += 1

    @staticmethod
    def _retry_exhausted_evidence(plan, outcomes) -> dict[str, Any]:
        retryable = tuple(
            value for value in outcomes
            if str(
                getattr(getattr(value, "reason", ""), "value", None)
                or getattr(value, "reason", "")
            ).strip().upper() == "PROCESSING_FAILED"
        )
        subjects = {
            str(getattr(value, "subject_value", "") or "")
            .strip().upper()
            for value in retryable
            if str(getattr(value, "subject_value", "") or "").strip()
        }
        explicit_keys = {
            normalize_listing_key(value)
            for value in (
                getattr(item, "listing_key", "")
                for item in retryable
            )
            if str(value).strip()
        }
        listing_keys = {
            value.listing_key
            for value in plan.selected_listings
            if (
                value.listing_key in explicit_keys
                or value.yahoo_symbol in subjects
            )
        }
        if retryable and not listing_keys:
            listing_keys = {
                value.listing_key for value in plan.selected_listings
            }
        return {
            "outcome_count": len(retryable),
            "listing_keys": tuple(sorted(listing_keys)),
            "hypothesis_ids": tuple(sorted({
                str(getattr(value, "hypothesis_id", "") or "").strip()
                for value in retryable
                if str(
                    getattr(value, "hypothesis_id", "") or ""
                ).strip()
            })),
            "reasons": tuple(
                str(
                    getattr(getattr(value, "reason", ""), "value", None)
                    or getattr(value, "reason", "")
                ).strip().upper()
                for value in retryable
            ),
        }

    @staticmethod
    def _validate_execution(session, plan, execution) -> None:
        record = execution.record
        if record.session_id != session.session_id:
            raise ValueError("wave execution belongs to another session")
        if record.wave_index != plan.wave_index:
            raise ValueError("wave execution index differs from plan")
        if record.plan_fingerprint != plan.fingerprint:
            raise ValueError("wave execution plan fingerprint differs")
        expected = tuple(sorted(
            value.listing_key for value in plan.selected_listings
        ))
        if record.listing_keys != expected:
            raise ValueError("wave execution listing keys differ from plan")
        if record.status not in {
            CandidateWaveStatus.PARTIAL,
            CandidateWaveStatus.COMPLETED,
            CandidateWaveStatus.BLOCKED,
        }:
            raise ValueError("wave execution is not terminal")
        if any((
            record.broker_orders_submitted,
            record.portfolio_mutations,
            record.automatic_executions,
        )):
            raise ValueError("wave execution recorded forbidden side effects")
        summary = classify_directional_outcomes(
            execution.directional_outcomes
        )
        if tuple(record.opportunity_ids) != summary["opportunity_ids"]:
            raise ValueError("wave opportunity evidence differs from outcomes")

    @staticmethod
    def _result(
        *, session, reason, opportunity_ids, attempted, record_ids,
        retry_exhausted_outcome_count,
        quarantined_listing_keys, quarantined_hypothesis_ids,
        quarantine_reason_counts, source_research_run_ids,
        ranking,
    ) -> CandidateReplenishmentResult:
        return CandidateReplenishmentResult(
            session_id=session.session_id,
            root_run_id=session.root_run_id,
            terminal_reason=reason,
            opportunity_ids=tuple(sorted(set(opportunity_ids))),
            attempted_listing_keys=tuple(sorted(attempted)),
            wave_record_ids=tuple(record_ids),
            retry_exhausted_outcome_count=retry_exhausted_outcome_count,
            quarantined_listing_keys=tuple(sorted(
                quarantined_listing_keys
            )),
            quarantined_hypothesis_ids=tuple(sorted(
                quarantined_hypothesis_ids
            )),
            quarantine_reason_counts=tuple(
                f"{key}={value}"
                for key, value in sorted(
                    quarantine_reason_counts.items()
                )
            ),
            source_research_run_ids=tuple(sorted(
                source_research_run_ids
            )),
            frontier_ranking_snapshot_id=(
                ranking.snapshot_id if ranking is not None else None
            ),
            frontier_ranking_mode=(
                ranking.mode.value if ranking is not None else None
            ),
            frontier_ranking_iteration_count=(
                ranking.iteration_count if ranking is not None else 0
            ),
            frontier_ranking_screened_count=(
                ranking.screened_listing_count
                if ranking is not None else 0
            ),
            frontier_ranking_qualified_count=(
                ranking.qualified_listing_count
                if ranking is not None else 0
            ),
            frontier_ranking_fallback_count=(
                len(ranking.fallback_listing_keys)
                if ranking is not None else 0
            ),
            frontier_ranking_network_calls=(
                ranking.network_calls if ranking is not None else 0
            ),
            frontier_ranking_provider_circuit_open=(
                ranking.provider_circuit_open
                if ranking is not None else False
            ),
        )
