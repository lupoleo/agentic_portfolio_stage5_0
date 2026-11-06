"""Pure bounded candidate-frontier planning for E2E-S4.0A.1."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime
import hashlib
from typing import Any

from app.e2e.stage4_contracts import Stage4Mode
from app.e2e.stage4_replenishment_contracts import (
    CandidateListingRef,
    CandidateReplenishmentPolicy,
    CandidateReplenishmentSession,
    CandidateWavePlan,
    ReplenishmentDisposition,
    ReplenishmentStopReason,
    candidate_replenishment_session_fingerprint,
    candidate_wave_plan_fingerprint,
    normalize_listing_key,
)


DIRECTIONAL_KINDS = frozenset({"NEW_LONG", "NEW_SHORT"})
TERMINAL_STATUSES = frozenset({
    "RESEARCHED", "OPPORTUNITY_CREATED", "EXCLUDED",
})
RETRYABLE_REASONS = frozenset({"PROCESSING_FAILED"})
FATAL_REASONS = frozenset({"ORCHESTRATION_INVARIANT_VIOLATION"})
REPLENISHABLE_REASONS = frozenset({
    "EVIDENCE_UNAVAILABLE",
    "RESEARCH_NOT_COMPLETE",
    "ADDITIONAL_RESEARCH_REQUIRED",
    "EVIDENCE_QUALITY_LOW",
    "SCORE_NOT_COMPLETE",
    "SCORE_BELOW_THRESHOLD",
    "SCORE_CONFIDENCE_TOO_LOW",
    "DIRECTIONAL_THESIS_MISSING",
    "SCORE_DIRECTION_MISMATCH",
    "SHORT_MATERIALIZATION_SUSPENDED",
})


def _value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().upper()


def create_replenishment_session(
    *, root_run_id: str, portfolio_snapshot_id: str,
    portfolio_risk_state_id: str, as_of: datetime,
    mode: Stage4Mode, policy: CandidateReplenishmentPolicy | None = None,
) -> CandidateReplenishmentSession:
    policy = policy or CandidateReplenishmentPolicy()
    fingerprint = candidate_replenishment_session_fingerprint(
        root_run_id=root_run_id,
        portfolio_snapshot_id=portfolio_snapshot_id,
        portfolio_risk_state_id=portfolio_risk_state_id,
        as_of=as_of,
        mode=mode,
        policy=policy,
    )
    return CandidateReplenishmentSession(
        session_id="s4a1-" + fingerprint[:24],
        root_run_id=root_run_id,
        portfolio_snapshot_id=portfolio_snapshot_id,
        portfolio_risk_state_id=portfolio_risk_state_id,
        as_of=as_of,
        mode=mode,
        policy=policy,
        fingerprint=fingerprint,
    )


def portfolio_watch_yahoo_symbols(universe: Any) -> tuple[str, ...]:
    """Return market-data identities already represented by positions."""
    values = set()
    for member in getattr(universe, "members", ()):
        provenances = {
            _value(value)
            for value in getattr(member, "provenances", ())
        }
        if "CURRENT_POSITION" not in provenances:
            continue
        values.update(
            str(value).strip().upper()
            for value in getattr(member, "yahoo_symbols", ())
            if str(value).strip()
        )
    return tuple(sorted(values))


def _frontier_state(
    eligibility: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *, attempted_listing_keys: Iterable[str] = (),
    excluded_yahoo_symbols: Iterable[str] = (),
    policy: CandidateReplenishmentPolicy | None = None,
):
    policy = policy or CandidateReplenishmentPolicy()
    attempted = {
        normalize_listing_key(value)
        for value in attempted_listing_keys
    }
    excluded_yahoo = {
        str(value).strip().upper()
        for value in excluded_yahoo_symbols
        if str(value).strip()
    }

    mapping_rows = tuple(mapping.get("mappings", ()))
    resolved: dict[str, str] = {}

    for row in mapping_rows:
        exchange = _value(row.get("exchange"))
        symbol = _value(row.get("symbol"))
        if not exchange or not symbol:
            continue

        key = f"{exchange}:{symbol}"
        canonical = str(
            row.get("yahoo_symbol") or ""
        ).strip().upper()
        legacy = str(
            row.get("resolved_symbol") or ""
        ).strip().upper()

        if canonical and legacy and canonical != legacy:
            raise ValueError(
                f"conflicting Yahoo symbols for {key}"
            )

        yahoo = canonical or legacy
        if (
            _value(row.get("mapping_status")) == "RESOLVED"
            and yahoo
        ):
            existing = resolved.get(key)
            if existing is not None and existing != yahoo:
                raise ValueError(
                    f"duplicate mapping differs for {key}"
                )
            resolved[key] = yahoo

    eligibility_rows = tuple(
        eligibility.get("all_decisions", ())
    )
    eligible_supported: dict[str, tuple[str, str]] = {}
    seen = set()

    for row in eligibility_rows:
        exchange = _value(row.get("exchange"))
        symbol = _value(row.get("symbol"))
        if not exchange or not symbol:
            continue

        key = f"{exchange}:{symbol}"
        if key in seen:
            raise ValueError(
                f"duplicate eligibility listing: {key}"
            )
        seen.add(key)

        if _value(row.get("status")) != "ELIGIBLE":
            continue
        if exchange not in policy.supported_venues:
            continue

        eligible_supported[key] = (exchange, symbol)

    frontier = tuple(sorted(
        (
            CandidateListingRef(
                listing_key=key,
                exchange=exchange,
                symbol=symbol,
                yahoo_symbol=resolved[key],
            )
            for key, (exchange, symbol)
            in eligible_supported.items()
            if (
                key not in attempted
                and key in resolved
                and resolved[key] not in excluded_yahoo
            )
        ),
        key=lambda value: value.listing_key,
    ))

    resolved_supported = (
        set(eligible_supported) & set(resolved)
    )
    attempted_supported = (
        set(eligible_supported) & attempted
    )
    portfolio_overlap = tuple(sorted(
        key
        for key in resolved_supported
        if resolved[key] in excluded_yahoo
    ))
    evidence = {
        "eligibility_input_count": len(eligibility_rows),
        "mapping_input_count": len(mapping_rows),
        "supported_eligible_count": len(eligible_supported),
        "resolved_supported_eligible_count": len(
            resolved_supported
        ),
        "unresolved_or_missing_supported_eligible_count": (
            len(eligible_supported) - len(resolved_supported)
        ),
        "attempted_supported_eligible_count": len(
            attempted_supported
        ),
        "acquirable_frontier_count": len(frontier),
        "acquirable_frontier_sample": tuple(
            value.listing_key for value in frontier[:20]
        ),
    }
    if excluded_yahoo:
        evidence.update({
            "portfolio_watch_yahoo_symbol_count": len(
                excluded_yahoo
            ),
            "portfolio_overlap_supported_eligible_count": len(
                portfolio_overlap
            ),
            "portfolio_overlap_excluded_listing_sample": (
                portfolio_overlap[:20]
            ),
        })
    return frontier, evidence


def candidate_frontier_evidence(
    eligibility: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *, attempted_listing_keys: Iterable[str] = (),
    excluded_yahoo_symbols: Iterable[str] = (),
    policy: CandidateReplenishmentPolicy | None = None,
) -> dict[str, Any]:
    _, evidence = _frontier_state(
        eligibility,
        mapping,
        attempted_listing_keys=attempted_listing_keys,
        excluded_yahoo_symbols=excluded_yahoo_symbols,
        policy=policy,
    )
    return evidence


def build_candidate_frontier(
    eligibility: Mapping[str, Any],
    mapping: Mapping[str, Any],
    *, attempted_listing_keys: Iterable[str] = (),
    excluded_yahoo_symbols: Iterable[str] = (),
    policy: CandidateReplenishmentPolicy | None = None,
) -> tuple[CandidateListingRef, ...]:
    frontier, _ = _frontier_state(
        eligibility,
        mapping,
        attempted_listing_keys=attempted_listing_keys,
        excluded_yahoo_symbols=excluded_yahoo_symbols,
        policy=policy,
    )
    return frontier


def order_candidate_frontier(
    frontier: Iterable[CandidateListingRef],
    *,
    session_seed: str,
    priority_listing_keys: Iterable[str] = (),
) -> tuple[CandidateListingRef, ...]:
    """Apply persisted priority, then a deterministic non-alphabetic order."""
    values = tuple(frontier)
    indexed = {value.listing_key: value for value in values}
    priority_values = tuple(
        normalize_listing_key(value)
        for value in priority_listing_keys
    )
    priority = tuple(
        value for value in priority_values
        if value in indexed
    )
    if len(set(priority)) != len(priority):
        raise ValueError("frontier priority contains duplicate listings")
    priority_set = set(priority)
    remainder = tuple(sorted(
        (
            value for value in values
            if value.listing_key not in priority_set
        ),
        key=lambda value: hashlib.sha256(
            f"{session_seed}|{value.listing_key}".encode("utf-8")
        ).hexdigest(),
    ))
    return tuple(indexed[key] for key in priority) + remainder


def classify_directional_outcomes(outcomes: Iterable[Any]) -> dict[str, Any]:
    directional = tuple(
        value for value in outcomes
        if _value(getattr(value, "kind", "")) in DIRECTIONAL_KINDS
    )
    opportunities = tuple(
        str(getattr(value, "opportunity_id", "") or "").strip()
        for value in directional
        if str(getattr(value, "opportunity_id", "") or "").strip()
    )
    pending = tuple(
        value for value in directional
        if _value(getattr(value, "status", "")) not in TERMINAL_STATUSES
    )
    retryable = tuple(
        value for value in directional
        if _value(getattr(value, "reason", "")) in RETRYABLE_REASONS
    )
    fatal = tuple(
        value for value in directional
        if _value(getattr(value, "reason", "")) in FATAL_REASONS
    )
    replenishable = tuple(
        value for value in directional
        if _value(getattr(value, "reason", "")) in REPLENISHABLE_REASONS
    )
    retryable_reasons = Counter(
        _value(getattr(value, "reason", ""))
        for value in retryable
    )
    retryable_hypothesis_ids = tuple(sorted({
        str(getattr(value, "hypothesis_id", "") or "").strip()
        for value in retryable
        if str(getattr(value, "hypothesis_id", "") or "").strip()
    }))
    retryable_subject_values = tuple(sorted({
        str(getattr(value, "subject_value", "") or "").strip().upper()
        for value in retryable
        if str(getattr(value, "subject_value", "") or "").strip()
    }))
    return {
        "directional_count": len(directional),
        "opportunity_ids": tuple(sorted(set(opportunities))),
        "pending_count": len(pending),
        "unresolved_pending_count": len(tuple(
            value for value in pending
            if (
                _value(getattr(value, "reason", ""))
                not in RETRYABLE_REASONS | FATAL_REASONS
            )
        )),
        "retryable_count": len(retryable),
        "retryable_hypothesis_ids": retryable_hypothesis_ids,
        "retryable_subject_values": retryable_subject_values,
        "retryable_reason_counts": tuple(
            f"{key}={value}"
            for key, value in sorted(retryable_reasons.items())
        ),
        "fatal_count": len(fatal),
        "replenishable_count": len(replenishable),
    }


def plan_candidate_wave(
    *, session: CandidateReplenishmentSession,
    wave_index: int,
    eligibility: Mapping[str, Any],
    mapping: Mapping[str, Any],
    prior_outcomes: Iterable[Any],
    attempted_listing_keys: Iterable[str],
    excluded_yahoo_symbols: Iterable[str] = (),
    current_listing_keys: Iterable[str] = (),
    retry_count: int = 0,
    safety_blocked: bool = False,
    frontier_priority_keys: Iterable[str] = (),
) -> CandidateWavePlan:
    policy = session.policy
    attempted = tuple(sorted({
        normalize_listing_key(value) for value in attempted_listing_keys
    }))
    current = tuple(sorted({
        normalize_listing_key(value) for value in current_listing_keys
    }))
    summary = classify_directional_outcomes(prior_outcomes)
    diagnostics = tuple(
        f"{key}={value}"
        for key, value in sorted(summary.items())
    )

    if safety_blocked:
        return _plan(
            session.session_id, wave_index,
            ReplenishmentDisposition.STOP_FAIL_CLOSED,
            ReplenishmentStopReason.SAFETY_BLOCK,
            (), attempted, retry_count, diagnostics,
        )
    if summary["opportunity_ids"]:
        return _plan(
            session.session_id, wave_index,
            ReplenishmentDisposition.STOP_FAIL_CLOSED,
            ReplenishmentStopReason.SELECTABLE_OPPORTUNITY_FOUND,
            (), attempted, retry_count, diagnostics,
        )
    if summary["fatal_count"]:
        return _plan(
            session.session_id, wave_index,
            ReplenishmentDisposition.STOP_FAIL_CLOSED,
            ReplenishmentStopReason.SAFETY_BLOCK,
            (), attempted, retry_count,
            diagnostics + ("fatal_orchestration_invariant",),
        )
    if summary["retryable_count"]:
        if (
            retry_count < policy.max_transient_retries
            and current
        ):
            frontier = build_candidate_frontier(
                eligibility, mapping,
                attempted_listing_keys=(),
                excluded_yahoo_symbols=excluded_yahoo_symbols,
                policy=policy,
            )
            indexed = {
                value.listing_key: value
                for value in frontier
            }
            selected = tuple(
                indexed[key]
                for key in current
                if key in indexed
            )
            if selected:
                return _plan(
                    session.session_id,
                    wave_index,
                    ReplenishmentDisposition.RETRY_CURRENT,
                    ReplenishmentStopReason.RETRY_CURRENT_READY,
                    selected,
                    attempted,
                    retry_count + 1,
                    diagnostics,
                )
        diagnostics += (
            "transient_retry_exhausted_quarantine",
            "quarantined_listing_keys=" + ",".join(current),
        )
        retry_count = 0
    if summary["unresolved_pending_count"]:
        return _plan(
            session.session_id, wave_index,
            ReplenishmentDisposition.STOP_FAIL_CLOSED,
            ReplenishmentStopReason.PENDING_DIRECTIONAL_WORK,
            (), attempted, retry_count, diagnostics,
        )
    if wave_index > policy.max_waves:
        return _plan(
            session.session_id, wave_index,
            ReplenishmentDisposition.STOP_FAIL_CLOSED,
            ReplenishmentStopReason.REPLENISHMENT_BUDGET_EXHAUSTED,
            (), attempted, retry_count, diagnostics,
        )
    frontier = build_candidate_frontier(
        eligibility, mapping,
        attempted_listing_keys=attempted,
        excluded_yahoo_symbols=excluded_yahoo_symbols,
        policy=policy,
    )
    frontier = order_candidate_frontier(
        frontier,
        session_seed=session.fingerprint,
        priority_listing_keys=frontier_priority_keys,
    )
    selected = frontier[:policy.listing_batch_size]
    if not selected:
        return _plan(
            session.session_id, wave_index,
            ReplenishmentDisposition.STOP_FAIL_CLOSED,
            ReplenishmentStopReason.UNIVERSE_EXHAUSTED,
            (), attempted, retry_count, diagnostics,
        )
    return _plan(
        session.session_id, wave_index,
        ReplenishmentDisposition.ADVANCE_FRONTIER,
        ReplenishmentStopReason.NEXT_WAVE_READY,
        selected, attempted, retry_count, diagnostics,
    )


def _plan(
    session_id, wave_index, disposition, reason, selected,
    attempted, retry_count, diagnostics,
) -> CandidateWavePlan:
    selected = tuple(sorted(
        selected,
        key=lambda value: value.listing_key,
    ))
    payload = {
        "session_id": session_id,
        "wave_index": wave_index,
        "disposition": disposition,
        "reason": reason,
        "selected_listings": selected,
        "attempted_listing_keys": tuple(attempted),
        "retry_count": retry_count,
        "diagnostics": tuple(diagnostics),
    }
    fingerprint = candidate_wave_plan_fingerprint(payload)
    return CandidateWavePlan(**payload, fingerprint=fingerprint)
