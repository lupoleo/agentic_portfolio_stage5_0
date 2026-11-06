"""Deterministic E2E-S2.2F Scanner-to-Research integration primitives."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from app.ai.opportunity_score_models import (
    OpportunityScore,
    OpportunityScoringProfile,
    OpportunityScoringStatus,
)
from app.ai.research_models import EvidenceQuality, OpportunityResearch, ResearchStatus
from app.ai.scan_models import (
    CandidateAction,
    CandidateOrigin,
    MarketScan,
    MarketScanStatus,
    ScannerType,
    ScanCandidate,
    ScanUniverseType,
    SignalType,
)
from app.cio.models import (
    Direction,
    OpportunityStatus,
    TradeOpportunity,
    TradingHorizon,
)
from app.scanner.research_integration_contracts import (
    HypothesisOutcomeReason,
    OpportunityMaterializationDecision,
    ResearchHypothesis,
    ResearchHypothesisKind,
    ResearchWatchMemberInput,
    ResearchWatchUniverseInput,
    ScannerResearchIntegrationPolicy,
    TradeOpportunityProvenanceLink,
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _identifier(prefix: str, value: Any, length: int = 24) -> str:
    return f"{prefix}-{_digest(value)[:length]}"


def _candidate_key(value: Any) -> str:
    if isinstance(value, dict):
        exchange = str(value.get("exchange", "")).strip().upper()
        symbol = str(value.get("symbol", "")).strip().upper()
        if exchange and symbol:
            return f"{exchange}:{symbol}"
    return str(value).strip().upper()


def load_watch_universe_report(path: str | Path) -> ResearchWatchUniverseInput:
    """Load the narrow immutable S2.2E boundary needed by S2.2F."""
    source = Path(path)
    raw_bytes = source.read_bytes()
    report = json.loads(raw_bytes.decode("utf-8-sig"))
    if report.get("audit_id") != "E2E-S2.2E":
        raise ValueError("expected an E2E-S2.2E watch-universe report")
    if report.get("schema") != "scanner-research-watch-universe-v1":
        raise ValueError("unsupported Research Watch Universe schema")
    universe = report.get("universe")
    if not isinstance(universe, dict):
        raise ValueError("watch-universe payload is missing")
    members = []
    for raw in universe.get("members", ()):
        subject = raw.get("subject_key") or {}
        members.append(ResearchWatchMemberInput(
            subject_namespace=subject.get("namespace", ""),
            subject_value=subject.get("value", ""),
            provenances=tuple(raw.get("provenances", ())),
            candidate_keys=tuple(_candidate_key(x) for x in raw.get("candidate_keys", ())),
            position_refs=tuple(raw.get("position_refs", ())),
            yahoo_symbols=tuple(raw.get("yahoo_symbols", ())),
            isins=tuple(raw.get("isins", ())),
            history_routes=tuple(raw.get("history_routes", ())),
            readiness=raw.get("readiness", ""),
            diagnostics=tuple(raw.get("diagnostics", ())),
        ))
    return ResearchWatchUniverseInput(
        run_id=universe["run_id"],
        fingerprint=universe["fingerprint"],
        as_of=universe["as_of"],
        portfolio_snapshot_id=universe["portfolio_snapshot_id"],
        members=tuple(members),
        source_report_fingerprint=hashlib.sha256(raw_bytes).hexdigest(),
    )


def integration_run_id(
    universe: ResearchWatchUniverseInput,
    policy: ScannerResearchIntegrationPolicy,
) -> str:
    return _identifier("s2f", {
        "watch_universe_fingerprint": universe.fingerprint,
        "portfolio_snapshot_id": universe.portfolio_snapshot_id,
        "policy": policy.model_dump(mode="json"),
    })


def excluded_member_id(
    universe: ResearchWatchUniverseInput,
    member: ResearchWatchMemberInput,
    policy: ScannerResearchIntegrationPolicy,
) -> str:
    return _identifier("member", {
        "integration_run_id": integration_run_id(universe, policy),
        "subject": [member.subject_namespace, member.subject_value],
        "outcome": "EXCLUDED_BEFORE_HYPOTHESIS",
    })


def member_exclusion_reason(
    member: ResearchWatchMemberInput,
) -> HypothesisOutcomeReason | None:
    if member.readiness != "READY":
        return HypothesisOutcomeReason.MEMBER_NOT_READY
    if len(member.yahoo_symbols) != 1:
        return HypothesisOutcomeReason.AMBIGUOUS_MARKET_DATA_IDENTITY
    return None


def _hypothesis_specs(member: ResearchWatchMemberInput):
    provenances = set(member.provenances)
    specs = []
    if "NEW_CANDIDATE" in provenances:
        specs.extend((
            (ResearchHypothesisKind.NEW_LONG, CandidateOrigin.EXTERNAL, CandidateAction.NEW_LONG),
            (ResearchHypothesisKind.NEW_SHORT, CandidateOrigin.EXTERNAL, CandidateAction.NEW_SHORT),
        ))
    if "CURRENT_POSITION" in provenances:
        specs.append((
            ResearchHypothesisKind.PORTFOLIO_MONITOR,
            CandidateOrigin.PORTFOLIO,
            CandidateAction.NO_ACTION,
        ))
    return tuple(specs)


def build_research_hypotheses(
    universe: ResearchWatchUniverseInput,
    *,
    policy: ScannerResearchIntegrationPolicy | None = None,
    created_at: datetime | None = None,
) -> tuple[ResearchHypothesis, ...]:
    """Create unbiased directional hypotheses and monitor-only portfolio tasks."""
    policy = policy or ScannerResearchIntegrationPolicy()
    created_at = created_at or datetime.now(timezone.utc)
    if created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    run_id = integration_run_id(universe, policy)
    scan_id = _identifier("scan", {"integration_run_id": run_id})
    hypotheses = []
    for member in universe.members:
        if member_exclusion_reason(member) is not None:
            continue
        ticker = member.yahoo_symbols[0].upper()
        for kind, origin, action in _hypothesis_specs(member):
            identity = {
                "integration_run_id": run_id,
                "subject": [member.subject_namespace, member.subject_value],
                "kind": kind.value,
                "ticker": ticker,
            }
            hypothesis_id = _identifier("cand", identity)
            candidate = ScanCandidate(
                candidate_id=hypothesis_id,
                scan_id=scan_id,
                created_at=created_at,
                ticker=ticker,
                origin=origin,
                action=action,
                signal_type=SignalType.MULTI_FACTOR,
                raw_score=None,
                scanner_confidence=None,
                thesis_summary=(
                    "Directional hypothesis generated without directional bias "
                    "from an admitted Research Watch Universe member."
                    if kind is not ResearchHypothesisKind.PORTFOLIO_MONITOR
                    else "Research-only monitoring hypothesis for a current position."
                ),
                portfolio_snapshot_id=universe.portfolio_snapshot_id,
                risk_state_id=None,
                requires_research=True,
                metadata={
                    "integration_policy_id": policy.policy_id,
                    "integration_policy_version": policy.policy_version,
                    "integration_run_id": run_id,
                    "watch_universe_run_id": universe.run_id,
                    "watch_universe_fingerprint": universe.fingerprint,
                    "research_subject_namespace": member.subject_namespace,
                    "research_subject_value": member.subject_value,
                    "watch_provenances": list(member.provenances),
                    "candidate_keys": list(member.candidate_keys),
                    "position_refs": list(member.position_refs),
                    "hypothesis_kind": kind.value,
                },
            )
            hypotheses.append(ResearchHypothesis(
                hypothesis_id=hypothesis_id,
                integration_run_id=run_id,
                watch_universe_run_id=universe.run_id,
                watch_universe_fingerprint=universe.fingerprint,
                subject_namespace=member.subject_namespace,
                subject_value=member.subject_value,
                kind=kind,
                ticker=ticker,
                candidate=candidate,
                provenances=member.provenances,
            ))
    return tuple(sorted(hypotheses, key=lambda x: (
        x.subject_namespace, x.subject_value, x.kind.value, x.hypothesis_id,
    )))


def build_market_scan(
    universe: ResearchWatchUniverseInput,
    hypotheses: tuple[ResearchHypothesis, ...],
    *,
    policy: ScannerResearchIntegrationPolicy | None = None,
    created_at: datetime | None = None,
) -> MarketScan:
    policy = policy or ScannerResearchIntegrationPolicy()
    created_at = created_at or datetime.now(timezone.utc)
    run_id = integration_run_id(universe, policy)
    if any(value.integration_run_id != run_id for value in hypotheses):
        raise ValueError("hypothesis belongs to another integration run")
    candidate_ids = [value.hypothesis_id for value in hypotheses]
    symbols = sorted({value.ticker for value in hypotheses})
    return MarketScan(
        scan_id=_identifier("scan", {"integration_run_id": run_id}),
        created_at=created_at,
        scanner_type=ScannerType.HYBRID,
        scanner_version=f"{policy.policy_id}:{policy.policy_version}",
        universe_type=ScanUniverseType.WATCHLIST,
        universe_name="STAGE4_RESEARCH_WATCH_UNIVERSE",
        portfolio_snapshot_id=universe.portfolio_snapshot_id,
        risk_state_id=None,
        symbols_requested=symbols,
        symbols_scanned=symbols,
        candidate_ids=candidate_ids,
        status=MarketScanStatus.COMPLETED,
        started_at=created_at,
        completed_at=created_at,
        metadata={
            "integration_run_id": run_id,
            "watch_universe_run_id": universe.run_id,
            "watch_universe_fingerprint": universe.fingerprint,
        },
    )


def hypothesis_direction(kind: ResearchHypothesisKind) -> Direction | None:
    """Direction a hypothesis asks research and scoring to evaluate."""
    if kind is ResearchHypothesisKind.NEW_LONG:
        return Direction.LONG
    if kind is ResearchHypothesisKind.NEW_SHORT:
        return Direction.SHORT
    return None


# Directional scoring policies whose SHORT scores are trusted for
# materialization. v1 (AI-8C.3-R2) let the model invert fundamental and
# expectations itself, which live evidence showed to be unreliable.
ACCEPTED_DIRECTIONAL_SCORING_POLICIES = frozenset({"ai-8c3-directional-scoring-v3"})


def shared_company_assessment_used(score: OpportunityScore) -> bool:
    """AI-8C.3-R2.2: every scored company-frame component of this score came
    from the shared, direction-free company assessment."""
    diagnostics = (score.metadata or {}).get("scoring_diagnostics") or {}
    direction = diagnostics.get("direction") if isinstance(diagnostics, dict) else None
    company = (direction or {}).get("company_assessment") if isinstance(direction, dict) else None
    sources = (company or {}).get("sources") if isinstance(company, dict) else None
    if not isinstance(sources, dict):
        return False
    scored = {
        "fundamental": score.fundamental_score is not None,
        "expectations": score.expectations_score is not None,
    }
    return all(
        sources.get(name) == "SHARED_COMPANY_ASSESSMENT"
        for name, present in scored.items() if present
    )


# Safety switch (operator decisions 2026-09-30). SHORT materialization was
# suspended after the R2.1 live run showed company-frame scores diverging
# between the LONG and SHORT sides of a listing. AI-8C.3-R2.2 (one shared,
# direction-free company assessment) was accepted live, and the operator
# re-enabled SHORT. A SHORT still materializes only with a v3 directional
# score whose company-frame components all come from the shared assessment.
# Set to False to suspend SHORT again (outcome SHORT_MATERIALIZATION_SUSPENDED).
# Code-level constant, not a policy field: run identities are unaffected.
SHORT_MATERIALIZATION_ENABLED = True


def scored_direction(score: OpportunityScore) -> str | None:
    """Direction recorded by an accepted directional scoring policy, if any."""
    diagnostics = (score.metadata or {}).get("scoring_diagnostics") or {}
    direction = diagnostics.get("direction") if isinstance(diagnostics, dict) else None
    if not isinstance(direction, dict):
        return None
    if direction.get("direction_source") != "HYPOTHESIS":
        return None
    if direction.get("policy_version") not in ACCEPTED_DIRECTIONAL_SCORING_POLICIES:
        return None
    value = direction.get("direction")
    return str(value) if value else None


def build_opportunity_score(
    hypothesis: ResearchHypothesis,
    research: OpportunityResearch,
    scoring_result: Any,
    *,
    evidence_coverage_score: float,
    created_at: datetime,
    scoring_profile: OpportunityScoringProfile = OpportunityScoringProfile.STANDARD,
) -> OpportunityScore:
    components = scoring_result.components
    calculation = scoring_result.calculation
    values = {
        "thesis_score": components.thesis.score,
        "catalyst_score": components.catalyst.score,
        "fundamental_score": components.fundamental.score,
        "technical_score": components.technical.score,
        "expectations_score": components.expectations.score,
    }
    available = sum(value is not None for value in values.values())
    needs_more = research.requires_additional_research or available < 5
    if available == 0:
        status = OpportunityScoringStatus.NOT_SCORABLE
    elif available < 5 or needs_more:
        status = OpportunityScoringStatus.PARTIAL
    else:
        status = OpportunityScoringStatus.SCORED
    score_id = _identifier("score", {
        "hypothesis_id": hypothesis.hypothesis_id,
        "research_id": research.research_id,
        "profile": scoring_profile.value,
    })
    return OpportunityScore(
        opportunity_score_id=score_id,
        candidate_id=hypothesis.candidate.candidate_id,
        research_id=research.research_id,
        scan_id=hypothesis.candidate.scan_id,
        created_at=created_at,
        ticker=hypothesis.ticker,
        scoring_profile=scoring_profile,
        scoring_status=status,
        raw_score=calculation.raw_score,
        score_confidence=calculation.score_confidence,
        confidence_adjusted_score=calculation.confidence_adjusted_score,
        evidence_quality=research.evidence_quality,
        evidence_coverage_score=evidence_coverage_score,
        research_confidence=research.research_confidence,
        positive_factors=components.positive_factors,
        negative_factors=components.negative_factors,
        uncertainty_factors=components.uncertainty_factors,
        requires_additional_research=needs_more,
        evidence_ids=research.evidence_ids,
        inference_ids=(),
        portfolio_snapshot_id=research.portfolio_snapshot_id,
        risk_state_id=research.risk_state_id,
        metadata={
            "integration_run_id": hypothesis.integration_run_id,
            "hypothesis_kind": hypothesis.kind.value,
            "scoring_diagnostics": scoring_result.diagnostics,
        },
        **values,
    )


def opportunity_materialization_decision(
    hypothesis: ResearchHypothesis,
    research: OpportunityResearch,
    score: OpportunityScore,
    *,
    policy: ScannerResearchIntegrationPolicy | None = None,
) -> OpportunityMaterializationDecision:
    policy = policy or ScannerResearchIntegrationPolicy()
    if hypothesis.kind is ResearchHypothesisKind.PORTFOLIO_MONITOR:
        return OpportunityMaterializationDecision(
            eligible=False, reason=HypothesisOutcomeReason.MONITOR_ONLY,
            message="Portfolio NO_ACTION research cannot materialize an opportunity",
            score=score,
        )
    if research.research_status is not ResearchStatus.COMPLETE:
        return OpportunityMaterializationDecision(
            eligible=False, reason=HypothesisOutcomeReason.RESEARCH_NOT_COMPLETE,
            message="Opportunity requires COMPLETE research", score=score,
        )
    if research.requires_additional_research:
        return OpportunityMaterializationDecision(
            eligible=False, reason=HypothesisOutcomeReason.ADDITIONAL_RESEARCH_REQUIRED,
            message="Research still requires additional work", score=score,
        )
    if research.evidence_quality is EvidenceQuality.LOW:
        return OpportunityMaterializationDecision(
            eligible=False, reason=HypothesisOutcomeReason.EVIDENCE_QUALITY_LOW,
            message="LOW evidence quality cannot materialize an opportunity", score=score,
        )
    if score.scoring_status is not OpportunityScoringStatus.SCORED:
        return OpportunityMaterializationDecision(
            eligible=False, reason=HypothesisOutcomeReason.SCORE_NOT_COMPLETE,
            message="Opportunity requires a complete five-component score", score=score,
        )
    expected_direction = hypothesis_direction(hypothesis.kind)
    if expected_direction is Direction.SHORT and not SHORT_MATERIALIZATION_ENABLED:
        return OpportunityMaterializationDecision(
            eligible=False, reason=HypothesisOutcomeReason.SHORT_MATERIALIZATION_SUSPENDED,
            message="SHORT materialization is suspended until AI-8C.3-R2.2", score=score,
        )
    if (
        expected_direction is Direction.SHORT
        and not shared_company_assessment_used(score)
    ):
        return OpportunityMaterializationDecision(
            eligible=False, reason=HypothesisOutcomeReason.SCORE_DIRECTION_MISMATCH,
            message="SHORT score lacks the shared company assessment", score=score,
        )
    if expected_direction is None or scored_direction(score) != expected_direction.value:
        return OpportunityMaterializationDecision(
            eligible=False, reason=HypothesisOutcomeReason.SCORE_DIRECTION_MISMATCH,
            message="Score was not computed for the hypothesis direction", score=score,
        )
    if score.confidence_adjusted_score is None or (
        score.confidence_adjusted_score < policy.minimum_confidence_adjusted_score
    ):
        return OpportunityMaterializationDecision(
            eligible=False, reason=HypothesisOutcomeReason.SCORE_BELOW_THRESHOLD,
            message="Confidence-adjusted score is below policy threshold", score=score,
        )
    if score.score_confidence < policy.minimum_score_confidence:
        return OpportunityMaterializationDecision(
            eligible=False, reason=HypothesisOutcomeReason.SCORE_CONFIDENCE_TOO_LOW,
            message="Score confidence is below policy threshold", score=score,
        )
    thesis = (
        research.bull_case
        if hypothesis.kind is ResearchHypothesisKind.NEW_LONG
        else research.bear_case
    )
    if not thesis or not thesis.strip():
        return OpportunityMaterializationDecision(
            eligible=False, reason=HypothesisOutcomeReason.DIRECTIONAL_THESIS_MISSING,
            message="Directional research case is missing", score=score,
        )
    return OpportunityMaterializationDecision(
        eligible=True, reason=HypothesisOutcomeReason.OPPORTUNITY_CREATED,
        message="All deterministic materialization gates passed", score=score,
    )


def materialize_trade_opportunity(
    hypothesis: ResearchHypothesis,
    research: OpportunityResearch,
    score: OpportunityScore,
    *,
    portfolio_snapshot_id: str,
    created_at: datetime,
    policy: ScannerResearchIntegrationPolicy | None = None,
) -> tuple[OpportunityMaterializationDecision, TradeOpportunity | None, TradeOpportunityProvenanceLink | None]:
    policy = policy or ScannerResearchIntegrationPolicy()
    decision = opportunity_materialization_decision(
        hypothesis, research, score, policy=policy,
    )
    if not decision.eligible:
        return decision, None, None
    direction = hypothesis_direction(hypothesis.kind)
    if direction is None:  # unreachable: the decision above requires it
        raise ValueError("materialization requires a directional hypothesis")
    thesis = research.bull_case if direction is Direction.LONG else research.bear_case
    invalidation = research.bear_case if direction is Direction.LONG else research.bull_case
    opportunity_id = _identifier("opp", {
        "integration_run_id": hypothesis.integration_run_id,
        "hypothesis_id": hypothesis.hypothesis_id,
        "research_id": research.research_id,
        "opportunity_score_id": score.opportunity_score_id,
    })
    opportunity = TradeOpportunity(
        opportunity_id=opportunity_id,
        snapshot_id=portfolio_snapshot_id,
        created_at=created_at,
        updated_at=None,
        ticker=hypothesis.ticker,
        direction=direction,
        horizon=TradingHorizon(policy.default_horizon),
        expected_holding_min_days=None,
        expected_holding_max_days=None,
        confidence=score.score_confidence,
        target_exposure_eur=None,
        max_intended_loss_eur=None,
        thesis=thesis.strip(),
        catalyst=research.catalyst_assessment,
        key_risks=list(research.key_risks),
        evidence_ids=list(score.evidence_ids),
        status=OpportunityStatus.DISCOVERED,
        broker_instruments_required=True,
        broker_instruments_available=None,
        broker_instrument_count=None,
        notes=(
            f"Created by {policy.policy_id}:{policy.policy_version}; "
            f"research={research.research_id}; score={score.opportunity_score_id}"
        ),
    )
    link = TradeOpportunityProvenanceLink(
        opportunity_id=opportunity_id,
        integration_run_id=hypothesis.integration_run_id,
        watch_universe_run_id=hypothesis.watch_universe_run_id,
        watch_universe_fingerprint=hypothesis.watch_universe_fingerprint,
        subject_namespace=hypothesis.subject_namespace,
        subject_value=hypothesis.subject_value,
        hypothesis_id=hypothesis.hypothesis_id,
        candidate_id=hypothesis.candidate.candidate_id,
        research_id=research.research_id,
        opportunity_score_id=score.opportunity_score_id,
        portfolio_snapshot_id=portfolio_snapshot_id,
        evidence_ids=tuple(score.evidence_ids),
        inference_ids=tuple(research.inference_ids),
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        invalidation_conditions=(invalidation.strip(),) if invalidation else (),
        created_at=created_at,
    )
    return decision, opportunity, link
