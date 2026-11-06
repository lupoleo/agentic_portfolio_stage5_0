"""Resumable orchestration for E2E-S2.2F Scanner-to-Research integration."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable

from app.ai.evidence_aggregator import EvidenceAggregator
from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceRequest,
)
from app.ai.opportunity_score_models import OpportunityScoringProfile
from app.ai.technical_evidence_adapter import CanonicalTechnicalEvidenceAdapter
from app.scanner.research_integration import (
    build_market_scan,
    build_opportunity_score,
    build_research_hypotheses,
    excluded_member_id,
    hypothesis_direction,
    integration_run_id,
    member_exclusion_reason,
    materialize_trade_opportunity,
)
from app.scanner.research_integration_contracts import (
    HypothesisOutcomeReason,
    HypothesisOutcomeStatus,
    IntegrationRunStatus,
    PersistedEvidenceBundle,
    ResearchHypothesisOutcome,
    ResearchHypothesisKind,
    ResearchWatchUniverseInput,
    ScannerResearchIntegrationPolicy,
    ScannerResearchRun,
)


_TERMINAL_OUTCOME_STATUSES = frozenset({
    HypothesisOutcomeStatus.RESEARCHED,
    HypothesisOutcomeStatus.OPPORTUNITY_CREATED,
    HypothesisOutcomeStatus.EXCLUDED,
})


def _select_pending_hypotheses(
    hypotheses: tuple[ResearchHypothesis, ...],
    outcomes: tuple[ResearchOutcome, ...] | list[ResearchOutcome],
    max_hypotheses: int | None,
) -> tuple[ResearchHypothesis, ...]:
    """Select pending work without allowing monitors to starve candidates."""
    terminal_ids = {
        outcome.hypothesis_id
        for outcome in outcomes
        if outcome.status in _TERMINAL_OUTCOME_STATUSES
    }
    pending = tuple(
        hypothesis
        for hypothesis in hypotheses
        if hypothesis.hypothesis_id not in terminal_ids
    )

    if max_hypotheses is None:
        return pending

    directional_kinds = {
        ResearchHypothesisKind.NEW_LONG,
        ResearchHypothesisKind.NEW_SHORT,
    }

    directional = tuple(
        hypothesis
        for hypothesis in pending
        if getattr(
            hypothesis,
            "kind",
            ResearchHypothesisKind.PORTFOLIO_MONITOR,
        )
        in directional_kinds
    )
    monitoring = tuple(
        hypothesis
        for hypothesis in pending
        if getattr(
            hypothesis,
            "kind",
            ResearchHypothesisKind.PORTFOLIO_MONITOR,
        )
        not in directional_kinds
    )

    scheduled = directional + monitoring
    return scheduled[:max_hypotheses]


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ScannerResearchIntegrationService:
    """Connect accepted S2.2E members to frozen Research and Scoring services."""

    def __init__(
        self,
        *,
        stage3_store,
        integration_store,
        research_service,
        scoring_service,
        market_provider,
        news_provider,
        fundamental_provider=None,
        analyst_provider=None,
        evidence_aggregator: EvidenceAggregator | None = None,
        canonical_technical_loader: Callable[[str], Any] | None = None,
        technical_evidence_adapter: CanonicalTechnicalEvidenceAdapter | None = None,
        policy: ScannerResearchIntegrationPolicy | None = None,
    ) -> None:
        self.stage3_store = stage3_store
        self.integration_store = integration_store
        self.research_service = research_service
        self.scoring_service = scoring_service
        self.market_provider = market_provider
        self.news_provider = news_provider
        self.fundamental_provider = fundamental_provider
        self.analyst_provider = analyst_provider
        self.evidence_aggregator = evidence_aggregator or EvidenceAggregator()
        self.canonical_technical_loader = canonical_technical_loader
        self.technical_evidence_adapter = (
            technical_evidence_adapter
            if technical_evidence_adapter is not None
            else (
                CanonicalTechnicalEvidenceAdapter()
                if canonical_technical_loader is not None else None
            )
        )
        self.policy = policy or ScannerResearchIntegrationPolicy()

    def run(
        self,
        universe: ResearchWatchUniverseInput,
        *,
        now: datetime | None = None,
        max_hypotheses: int | None = None,
        cache_only: bool = False,
    ) -> ScannerResearchRun:
        now = now or datetime.now(timezone.utc)
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if now < universe.as_of:
            raise ValueError("integration cannot precede watch-universe as_of")
        if max_hypotheses is not None and max_hypotheses < 1:
            raise ValueError("max_hypotheses must be positive")
        hypotheses = build_research_hypotheses(
            universe, policy=self.policy, created_at=now,
        )
        excluded_members = tuple(
            (member, member_exclusion_reason(member))
            for member in universe.members
            if member_exclusion_reason(member) is not None
        )
        run_id = integration_run_id(universe, self.policy)
        scan = build_market_scan(
            universe, hypotheses, policy=self.policy, created_at=now,
        )
        self.stage3_store.save_market_scan(scan)
        for hypothesis in hypotheses:
            self.stage3_store.save_scan_candidate(hypothesis.candidate)

        prior = self.integration_store.get_run(run_id)
        if prior is not None and prior.watch_universe_fingerprint != universe.fingerprint:
            raise ValueError("resume fingerprint differs from persisted integration run")
        prior_outcomes = self.integration_store.list_outcomes(run_id)
        selected_hypotheses = _select_pending_hypotheses(
            hypotheses,
            prior_outcomes,
            max_hypotheses,
        )
        all_work_ids = tuple(value.hypothesis_id for value in hypotheses) + tuple(
            excluded_member_id(universe, member, self.policy)
            for member, _ in excluded_members
        )
        run_fingerprint = _fingerprint({
            "watch_universe_fingerprint": universe.fingerprint,
            "policy": self.policy.model_dump(mode="json"),
            "hypothesis_ids": list(all_work_ids),
        })
        running = ScannerResearchRun(
            run_id=run_id,
            watch_universe_run_id=universe.run_id,
            watch_universe_fingerprint=universe.fingerprint,
            portfolio_snapshot_id=universe.portfolio_snapshot_id,
            policy=self.policy,
            as_of=universe.as_of,
            started_at=prior.started_at if prior is not None else now,
            status=IntegrationRunStatus.RUNNING,
            hypothesis_ids=all_work_ids,
            completed_hypothesis_ids=(),
            opportunity_ids=(),
            fingerprint=run_fingerprint,
        )
        self.integration_store.save_run(running)

        for member, reason in excluded_members:
            member_id = excluded_member_id(universe, member, self.policy)
            if self.integration_store.get_outcome(run_id, member_id) is None:
                self.integration_store.save_outcome(ResearchHypothesisOutcome(
                    integration_run_id=run_id,
                    hypothesis_id=member_id,
                    subject_namespace=member.subject_namespace,
                    subject_value=member.subject_value,
                    kind=ResearchHypothesisKind.MEMBER_REVIEW,
                    status=HypothesisOutcomeStatus.EXCLUDED,
                    reason=reason,
                    updated_at=now,
                    diagnostics=member.diagnostics,
                ))

        rate_limited = False
        for hypothesis in selected_hypotheses:
            existing = self.integration_store.get_outcome(
                run_id, hypothesis.hypothesis_id,
            )
            if existing is not None and existing.status in {
                HypothesisOutcomeStatus.RESEARCHED,
                HypothesisOutcomeStatus.OPPORTUNITY_CREATED,
                HypothesisOutcomeStatus.EXCLUDED,
            }:
                continue
            if cache_only:
                self._save_outcome(
                    hypothesis, now,
                    HypothesisOutcomeStatus.PENDING,
                    HypothesisOutcomeReason.CACHE_ONLY_MISS,
                    diagnostics=("CACHE_ONLY_MISS",),
                )
                continue
            try:
                self._process(hypothesis, universe, now)
            except Exception as exc:  # isolate one member; fail closed
                name = type(exc).__name__
                if "RateLimit" in name or "TooManyRequests" in name:
                    rate_limited = True
                    break
                self._save_outcome(
                    hypothesis, now,
                    HypothesisOutcomeStatus.FAILED,
                    HypothesisOutcomeReason.PROCESSING_FAILED,
                    diagnostics=(f"{name}: {str(exc)[:500]}",),
                )

        outcomes = self.integration_store.list_outcomes(run_id)
        terminal_statuses = _TERMINAL_OUTCOME_STATUSES
        complete_ids = tuple(sorted(
            value.hypothesis_id
            for value in outcomes
            if value.status in terminal_statuses
        ))
        opportunity_ids = tuple(sorted(
            value.opportunity_id for value in outcomes if value.opportunity_id
        ))
        statuses = Counter(value.status for value in outcomes)
        if rate_limited:
            status = IntegrationRunStatus.RATE_LIMITED
        elif len(complete_ids) != len(all_work_ids):
            status = IntegrationRunStatus.PARTIAL
        elif (
            statuses[HypothesisOutcomeStatus.FAILED]
            or statuses[HypothesisOutcomeStatus.DEGRADED]
            or statuses[HypothesisOutcomeStatus.PENDING]
        ):
            status = IntegrationRunStatus.PARTIAL
        else:
            status = IntegrationRunStatus.COMPLETED
        final = running.model_copy(update={
            "status": status,
            "completed_at": now,
            "completed_hypothesis_ids": complete_ids,
            "opportunity_ids": opportunity_ids,
        })
        self.integration_store.save_run(final)
        return final

    def _process(self, hypothesis, universe, now) -> None:
        evidence_request = EvidenceRequest(
            ticker=hypothesis.ticker,
            as_of=universe.as_of,
            max_items=max(
                self.policy.max_market_items,
                self.policy.max_news_items,
            ),
            metadata={
                "integration_run_id": hypothesis.integration_run_id,
                "hypothesis_id": hypothesis.hypothesis_id,
            },
        )
        requests = (
            (self.market_provider, self.policy.max_market_items),
            (self.news_provider, self.policy.max_news_items),
        )
        results = [
            provider.fetch(evidence_request.model_copy(update={
                "max_items": max_items,
            }))
            for provider, max_items in requests
        ]
        for provider in (self.fundamental_provider, self.analyst_provider):
            if provider is not None:
                results.append(self._fetch_optional(provider, evidence_request, now))

        technical = None
        if self.canonical_technical_loader is not None:
            try:
                technical = self.canonical_technical_loader(hypothesis.ticker)
            except Exception as exc:
                results.append(self._unavailable_result(
                    "CANONICAL_TECHNICAL",
                    hypothesis.ticker,
                    now,
                    exc,
                ))
            else:
                if self.technical_evidence_adapter is not None:
                    try:
                        results.append(self.technical_evidence_adapter.adapt(
                            evidence_request,
                            technical,
                            fetched_at=universe.as_of,
                        ))
                    except Exception as exc:
                        results.append(self._unavailable_result(
                            self.technical_evidence_adapter.provider_name,
                            hypothesis.ticker,
                            now,
                            exc,
                        ))
        aggregated = self.evidence_aggregator.aggregate(
            hypothesis.ticker, results, now=now,
        )
        items_by_id = {
            item.evidence.evidence_id: item
            for result in results for item in result.items
        }
        items = [items_by_id[value] for value in aggregated.evidence_ids]
        bundle_payload = [item.model_dump(mode="json") for item in items]
        bundle_fingerprint = _fingerprint({
            "ticker": hypothesis.ticker,
            "evidence_ids": aggregated.evidence_ids,
            "source_ids": aggregated.source_ids,
            "items": bundle_payload,
        })
        bundle_id = f"evidence-{bundle_fingerprint[:24]}"
        bundle = PersistedEvidenceBundle(
            bundle_id=bundle_id,
            integration_run_id=hypothesis.integration_run_id,
            hypothesis_id=hypothesis.hypothesis_id,
            ticker=hypothesis.ticker,
            created_at=now,
            evidence_ids=tuple(aggregated.evidence_ids),
            source_ids=tuple(aggregated.source_ids),
            provider_statuses=tuple(sorted(
                (result.provider, result.status.value) for result in results
            )),
            items=tuple(bundle_payload),
            warnings=tuple(aggregated.warnings),
            fingerprint=bundle_fingerprint,
        )
        self.integration_store.save_evidence_bundle(bundle)
        if not aggregated.evidence:
            self._save_outcome(
                hypothesis, now,
                HypothesisOutcomeStatus.DEGRADED,
                HypothesisOutcomeReason.EVIDENCE_UNAVAILABLE,
                evidence_bundle_id=bundle_id,
                diagnostics=tuple(aggregated.warnings) or ("NO_USABLE_EVIDENCE",),
            )
            return

        research_result = self.research_service.research(
            hypothesis.candidate,
            aggregated.evidence,
            portfolio_snapshot_id=universe.portfolio_snapshot_id,
            now=now,
            evidence_items=items,
        )
        for inference in research_result.inferences or [research_result.inference]:
            self.stage3_store.save_ai_inference(inference)
        research = research_result.research
        self.stage3_store.save_opportunity_research(research)
        if hypothesis.kind is ResearchHypothesisKind.PORTFOLIO_MONITOR:
            self._save_outcome(
                hypothesis, now,
                HypothesisOutcomeStatus.RESEARCHED,
                HypothesisOutcomeReason.MONITOR_ONLY,
                evidence_bundle_id=bundle_id,
                research_id=research.research_id,
            )
            return

        coverage_score = research_result.evidence_quality_report.get("coverage_score")
        if coverage_score is None:
            raise ValueError("Research result has no evidence coverage score")
        scoring_result = self.scoring_service.score(
            research,
            evidence_coverage_score=float(coverage_score),
            scoring_profile=OpportunityScoringProfile.STANDARD,
            canonical_technical_input=technical,
            direction=hypothesis_direction(hypothesis.kind),
            company_evidence=list(aggregated.evidence),
        )
        score = build_opportunity_score(
            hypothesis, research, scoring_result,
            evidence_coverage_score=float(coverage_score),
            created_at=now,
        )
        self.integration_store.save_opportunity_score(score)
        decision, opportunity, link = materialize_trade_opportunity(
            hypothesis, research, score,
            portfolio_snapshot_id=universe.portfolio_snapshot_id,
            created_at=now,
            policy=self.policy,
        )
        if opportunity is not None and link is not None:
            self.stage3_store.save_trade_opportunity(opportunity)
            self.integration_store.save_provenance_link(link)
            status = HypothesisOutcomeStatus.OPPORTUNITY_CREATED
        else:
            status = HypothesisOutcomeStatus.EXCLUDED
        self._save_outcome(
            hypothesis, now, status, decision.reason,
            evidence_bundle_id=bundle_id,
            research_id=research.research_id,
            opportunity_score_id=score.opportunity_score_id,
            opportunity_id=opportunity.opportunity_id if opportunity else None,
            diagnostics=(decision.message,),
        )

    @staticmethod
    def _fetch_optional(provider, request, now) -> EvidenceFetchResult:
        try:
            return provider.fetch(request)
        except Exception as exc:
            return ScannerResearchIntegrationService._unavailable_result(
                provider.provider_name,
                request.ticker,
                now,
                exc,
            )

    @staticmethod
    def _unavailable_result(provider, ticker, now, exc) -> EvidenceFetchResult:
        return EvidenceFetchResult(
            provider=provider,
            ticker=ticker,
            status=EvidenceFetchStatus.PARTIAL,
            items=[],
            warnings=[
                f"{provider} unavailable: {type(exc).__name__}: {str(exc)[:300]}"
            ],
            fetched_at=now,
            metadata={"failure_isolated": True},
        )

    def _save_outcome(
        self,
        hypothesis,
        now,
        status,
        reason,
        *,
        evidence_bundle_id=None,
        research_id=None,
        opportunity_score_id=None,
        opportunity_id=None,
        diagnostics=(),
    ) -> None:
        self.integration_store.save_outcome(ResearchHypothesisOutcome(
            integration_run_id=hypothesis.integration_run_id,
            hypothesis_id=hypothesis.hypothesis_id,
            subject_namespace=hypothesis.subject_namespace,
            subject_value=hypothesis.subject_value,
            kind=hypothesis.kind,
            status=status,
            reason=reason,
            updated_at=now,
            evidence_bundle_id=evidence_bundle_id,
            research_id=research_id,
            opportunity_score_id=opportunity_score_id,
            opportunity_id=opportunity_id,
            diagnostics=tuple(diagnostics),
        ))
