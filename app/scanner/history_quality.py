"""Pure history/quality/liquidity evaluation; supplied evidence only."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from statistics import median

from app.scanner.market_data_contracts import MarketDataIdentityStatus
from app.scanner.history_quality_contracts import (
    GateStatus as S, HistoryRoute as R, HistoryGate as G,
    HistoryQualityPolicy, HistoryQualityResult, IndicatorCapability,
    aware, positive,
)


def _canonical(value):
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_float": repr(value)}
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(_canonical(value), sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


def _valid_prices(bar):
    return (all(positive(v) for v in (bar.open, bar.high, bar.low, bar.close))
            and bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high)


def _valid_volume(value):
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def history_quality_result_to_dict(result):
    """JSON-safe audit representation; durations are expressed in seconds."""
    return _canonical(asdict(result))


def evaluate_history_quality(*, snapshot, verification, as_of, calendar=None,
                             listing_start=None, fx_rates=(), policy=None):
    """Evaluate new-candidate data gates, not portfolio membership or trade signals.

    Supplied listing-start evidence must have been verified by its producer.
    A first history row is never substituted for that reference datum.
    """
    aware(as_of)
    policy = policy or HistoryQualityPolicy()
    fx_rates = tuple(fx_rates)
    mapping = verification.mapping
    if (snapshot.listing_key != mapping.listing_key
            or snapshot.yahoo_symbol != mapping.resolved_symbol
            or snapshot.mapping_version != mapping.mapping_version
            or mapping.provider_id != "yahoo"):
        raise ValueError("Snapshot and verified mapping identities differ")
    if listing_start is not None and listing_start.listing_key != snapshot.listing_key:
        raise ValueError("Listing-start evidence belongs to another listing")
    if calendar is not None and calendar.exchange != snapshot.listing_key.exchange:
        raise ValueError("Calendar belongs to another venue")
    fx_keys = [(r.session, r.currency) for r in fx_rates]
    if len(set(fx_keys)) != len(fx_keys):
        raise ValueError("Duplicate FX evidence")

    gates = []
    metrics = {}
    def gate(name, status, reason):
        gates.append(G(name, status, reason))

    identity_ok = verification.ready_for_market_data(as_of=as_of)
    gate("market_data", S.PASS if identity_ok else
         S.FAIL if verification.identity_status is MarketDataIdentityStatus.MISMATCH else S.UNDETERMINED,
         "VERIFICATION_READY" if identity_ok else "VERIFICATION_NOT_READY")
    future_snapshot = snapshot.fetched_at > as_of
    gate("snapshot_time", S.UNDETERMINED if future_snapshot else S.PASS,
         "SNAPSHOT_NOT_KNOWN_AS_OF" if future_snapshot else "SNAPSHOT_KNOWN")

    listing_known = listing_start is not None and listing_start.known_at <= as_of
    first = listing_start.first_session if listing_known else None
    gate("listing_reference", S.UNDETERMINED if listing_start is not None and not listing_known else S.PASS,
         "LISTING_EVIDENCE_NOT_KNOWN_AS_OF" if listing_start is not None and not listing_known else "REFERENCE_AS_OF_OK")

    calendar_ok = (calendar is not None and calendar.coverage_start <= snapshot.start_session
                   and calendar.coverage_end >= max(snapshot.end_session, as_of.date()))
    gate("calendar", S.PASS if calendar_ok else S.UNDETERMINED,
         "CALENDAR_COVERED" if calendar_ok else "CALENDAR_MISSING_OR_INCOMPLETE")
    due = [s.session for s in calendar.sessions
           if snapshot.start_session <= s.session
           and s.closes_at + policy.publication_grace <= as_of
           and (first is None or s.session >= first)] if calendar_ok else []
    scheduled = {s.session for s in calendar.sessions} if calendar_ok else set()
    due_set = set(due)
    raw_dates = [b.session for b in snapshot.bars]
    duplicates = len(raw_dates) - len(set(raw_dates))
    outside = sum(not snapshot.start_session <= b.session <= snapshot.end_session for b in snapshot.bars)
    unexpected = sum(b.session not in scheduled for b in snapshot.bars) if calendar_ok else 0
    before_listing = sum(b.session < first for b in snapshot.bars) if first else 0
    gate("structure", S.FAIL if duplicates or outside or unexpected or before_listing else S.PASS,
         "HISTORY_SESSION_CONFLICT" if duplicates or outside or unexpected or before_listing else "SESSION_LABELS_VALID")
    # Until the calendar is known, do not infer which bars are final.
    completed = [b for b in snapshot.bars if b.session in due_set]
    good = {b.session: b for b in completed if _valid_prices(b)}
    invalid = len(completed) - sum(_valid_prices(b) for b in completed)
    gate("prices", S.FAIL if invalid else S.PASS if calendar_ok else S.UNDETERMINED,
         "INVALID_OHLC" if invalid else "VALID_COMPLETED_OHLC" if calendar_ok else "COMPLETED_BARS_UNKNOWN")
    invalid_volumes = sum(b.volume is not None
                         and not (isinstance(b.volume, float) and math.isnan(b.volume))
                         and not _valid_volume(b.volume) for b in completed)
    gate("volume_integrity", S.FAIL if invalid_volumes else S.PASS if calendar_ok else S.UNDETERMINED,
         "INVALID_VOLUME" if invalid_volumes else "NO_INVALID_VOLUME" if calendar_ok else "COMPLETED_BARS_UNKNOWN")
    metrics.update(input_bars=len(snapshot.bars), completed_bars=len(completed),
                   valid_price_bars=len(good), invalid_price_bars=invalid,
                   invalid_volume_bars=invalid_volumes,
                   excluded_pending_bars=sum(b.session in scheduled and b.session not in due_set
                                             and (first is None or b.session >= first) for b in snapshot.bars),
                   duplicate_sessions=duplicates, unexpected_sessions=unexpected,
                   first_observed_session=min(good) if good else None,
                   last_observed_session=max(good) if good else None,
                   expected_latest_session=due[-1] if due else None)

    recent_proven = bool(first is not None and calendar_ok and first in scheduled
                         and first >= snapshot.start_session and len(due) < policy.min_standard_sessions
                         and due and due[0] == first)
    reference_conflict = first is not None and calendar_ok and (
        first > as_of.date() or (first >= calendar.coverage_start and first not in scheduled))
    if reference_conflict:
        gate("listing_date", S.UNDETERMINED, "LISTING_START_NOT_ESTABLISHED")
    coverage_dates = due[-policy.coverage_window:]
    coverage = sum(d in good for d in coverage_dates) / len(coverage_dates) if coverage_dates else None
    # A confirmed 50-session listing need not have a 60-session lifetime.
    full_lifetime = bool(first is not None and calendar_ok and first >= snapshot.start_session
                         and first in scheduled and due and due[0] == first)
    enough_coverage = len(coverage_dates) == policy.coverage_window or full_lifetime
    coverage_status = (S.UNDETERMINED if coverage is None or not enough_coverage else
                       S.PASS if coverage >= policy.minimum_coverage else S.FAIL)
    gate("coverage", coverage_status, "COVERAGE_UNDETERMINED" if coverage_status is S.UNDETERMINED
         else "COVERAGE_OK" if coverage_status is S.PASS else "MISSING_EXPECTED_SESSIONS")
    metrics.update(coverage_ratio=coverage, coverage_expected_sessions=len(coverage_dates))
    gate("freshness", S.UNDETERMINED if not due else S.PASS if due[-1] in good else S.FAIL,
         "NO_COMPLETED_SESSION_REFERENCE" if not due else
         "LATEST_COMPLETED_SESSION_PRESENT" if due[-1] in good else "LATEST_COMPLETED_SESSION_MISSING")

    def trailing(predicate):
        count = 0
        for d in reversed(due):
            if d not in good or not predicate(good[d]):
                break
            count += 1
        return count

    adjusted_count = trailing(lambda b: positive(b.adjusted_close))
    volume_count = trailing(lambda b: _valid_volume(b.volume))
    indicators = []
    for name, need, count in (("SMA20", 20, adjusted_count), ("SMA50", 50, adjusted_count),
                             ("RSI14", 15, adjusted_count), ("VOLATILITY20", 21, adjusted_count),
                             ("RVOL20", 21, volume_count), ("TREND", 50, adjusted_count)):
        ok = count >= need
        reason = "SUFFICIENT_ALIGNED_SESSIONS" if ok else "INSUFFICIENT_ALIGNED_SESSIONS"
        if name == "RVOL20" and ok and sum(good[d].volume for d in due[-21:-1]) <= 0:
            ok, reason = False, "ZERO_REFERENCE_VOLUME"
        indicators.append(IndicatorCapability(name, ok, need, count, reason))
    gate("technical_inputs", S.PASS if all(c.available for c in indicators) else S.UNDETERMINED,
         "STANDARD_INDICATOR_INPUTS_READY" if all(c.available for c in indicators) else "PARTIAL_INDICATOR_INPUTS")
    metrics.update(adjusted_close_trailing_sessions=adjusted_count,
                   volume_trailing_sessions=volume_count,
                   annual_window_observed_sessions=min(adjusted_count, 252),
                   recent_listing_verified=recent_proven)
    gate("history_maturity", S.PASS if len(good) >= policy.min_standard_sessions else S.UNDETERMINED,
         "STANDARD_HISTORY_LENGTH" if len(good) >= policy.min_standard_sessions else
         "VERIFIED_RECENT_LISTING" if recent_proven else "SHORT_HISTORY_UNVERIFIED")

    # Currency is bound to the Yahoo metadata, not guessed from a ticker suffix.
    unit = "GBX" if snapshot.price_unit in {"GBp", "GBX"} else snapshot.price_unit.upper()
    observed = dict(verification.identity_evidence).get("yahoo.currency")
    observed = "GBX" if observed in {"GBp", "GBX"} else observed.upper() if observed else None
    unit_ok = observed == unit
    gate("price_unit", S.UNDETERMINED if observed is None else S.PASS if unit_ok else S.FAIL,
         "PRICE_UNIT_UNKNOWN" if observed is None else "PRICE_UNIT_MATCH" if unit_ok else "PRICE_UNIT_MISMATCH")
    dates = due[-policy.liquidity_window:]
    liquid_bars = [good.get(d) for d in dates]
    complete_volume = (len(dates) == policy.liquidity_window
                       and all(b is not None and _valid_volume(b.volume) for b in liquid_bars))
    rates = {(r.session, r.currency): r for r in fx_rates if r.known_at <= as_of}
    currency, scale = ("GBP", .01) if unit == "GBX" else (unit, 1.)
    turnover = []
    if complete_volume and unit_ok:
        for d, b in zip(dates, liquid_bars):
            rate = 1. if currency == "EUR" else rates[(d, currency)].eur_per_unit if (d, currency) in rates else None
            if rate is not None:
                value = b.close * scale * b.volume * rate
                if math.isfinite(value):
                    turnover.append(value)
    positive_sessions = sum(b.volume > 0 for b in liquid_bars) if complete_volume else None
    median_turnover = median(turnover) if len(turnover) == policy.liquidity_window else None
    if not complete_volume:
        liquidity_status, liquidity_reason = S.UNDETERMINED, "INSUFFICIENT_ALIGNED_VOLUME_HISTORY"
    elif positive_sessions < policy.minimum_positive_volume_sessions:
        liquidity_status, liquidity_reason = S.FAIL, "TOO_FEW_POSITIVE_VOLUME_SESSIONS"
    elif median_turnover is None:
        liquidity_status, liquidity_reason = S.UNDETERMINED, "TURNOVER_CONVERSION_UNDETERMINED"
    elif median_turnover < policy.minimum_median_turnover_eur:
        liquidity_status, liquidity_reason = S.FAIL, "LOW_MEDIAN_TURNOVER"
    else:
        liquidity_status, liquidity_reason = S.PASS, "LIQUIDITY_OK"
    gate("liquidity", liquidity_status, liquidity_reason)
    metrics.update(liquidity_expected_sessions=len(dates), positive_volume_sessions=positive_sessions,
                   median_turnover_eur=median_turnover, turnover_method="nominal_close_times_volume")

    unknown_other = any(g.status is S.UNDETERMINED for g in gates
                        if g.name not in {"history_maturity", "liquidity", "technical_inputs"})
    if any(g.status is S.FAIL for g in gates):
        route = R.BLOCKED
    elif unknown_other:
        route = R.REVIEW_REQUIRED
    elif recent_proven:
        route = R.RECENT_LISTING
    elif all(g.status is S.PASS for g in gates) and all(c.available for c in indicators):
        route = R.STANDARD
    else:
        route = R.REVIEW_REQUIRED
    evidence = {"verification": asdict(verification), "calendar": asdict(calendar) if calendar else None,
                "listing_start": asdict(listing_start) if listing_start else None,
                "fx": [asdict(r) for r in sorted(fx_rates, key=lambda r: (r.session, r.currency))],
                "policy": asdict(policy), "as_of": as_of}
    evidence["policy"]["publication_grace"] = policy.publication_grace.total_seconds()
    sources = [("history", snapshot.source), ("adjustment_basis", snapshot.adjustment_basis),
               ("mapping_version", mapping.mapping_version),
               ("verification_version", verification.verification_version)]
    if calendar:
        sources.extend((("calendar", calendar.source), ("calendar_version", calendar.version)))
    if listing_start:
        sources.append(("listing_start", listing_start.source))
    for index, source in enumerate(sorted({r.source for r in fx_rates})):
        sources.append((f"fx_source_{index}", source))
    return HistoryQualityResult(snapshot.listing_key, snapshot.yahoo_symbol, as_of, policy,
        _digest(asdict(snapshot)), _digest(evidence), tuple(sources), route, tuple(gates), tuple(indicators),
        tuple(sorted(metrics.items())), listing_start.event_kind if listing_known else None)
