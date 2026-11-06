"""Pure Candidate Set and Portfolio Watch Set assembly; no network calls."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math

from app.portfolio.models import PortfolioPosition
from app.portfolio.ticker_resolver import resolve_yahoo_symbol
from app.scanner.history_quality_contracts import GateStatus, HistoryRoute
from app.scanner.instrument_eligibility import InstrumentEligibilityStatus
from app.scanner.market_data_contracts import SymbolMappingStatus
from app.scanner.watch_universe_contracts import (
    CandidateAssemblyDecision,
    CandidateAssemblyStatus,
    CandidateEvidenceBundle,
    CandidateExclusionReason,
    PortfolioPositionSnapshot,
    PortfolioWatchDecision,
    PortfolioWatchReason,
    PortfolioWatchStatus,
    ResearchReadiness,
    ResearchSubjectKey,
    ResearchWatchMember,
    ResearchWatchUniverse,
    ScannerCandidate,
    WatchProvenance,
    WatchUniversePolicy,
)


def _canonical(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_float": repr(value)}
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (set, frozenset)):
        normalized = [_canonical(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item, sort_keys=True, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ),
        )
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _digest(value) -> str:
    payload = json.dumps(
        _canonical(value), sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def watch_universe_to_dict(value):
    """Return a deterministic JSON-safe representation."""
    return _canonical(value)


def _candidate_decision(bundle, *, as_of, policy):
    eligibility = bundle.eligibility
    if eligibility.status is InstrumentEligibilityStatus.REVIEW_REQUIRED:
        return CandidateAssemblyDecision(
            bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
            CandidateExclusionReason.ELIGIBILITY_REVIEW_REQUIRED,
            "Instrument eligibility requires classification review",
        ), None
    if eligibility.status is not InstrumentEligibilityStatus.ELIGIBLE:
        return CandidateAssemblyDecision(
            bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
            CandidateExclusionReason.ELIGIBILITY_NOT_ELIGIBLE,
            "Instrument is not eligible for new Scanner admission",
        ), None

    if bundle.mapping is None:
        return CandidateAssemblyDecision(
            bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
            CandidateExclusionReason.UPSTREAM_RESULT_MISSING,
            "Market-data mapping result is missing",
        ), None
    if bundle.mapping.listing_key != bundle.listing.key:
        raise ValueError("mapping belongs to another listing")
    if bundle.mapping.provider_id != policy.market_data_provider_id:
        raise ValueError("mapping provider differs from assembly policy")
    if bundle.mapping.status is not SymbolMappingStatus.RESOLVED:
        return CandidateAssemblyDecision(
            bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
            CandidateExclusionReason.MAPPING_NOT_RESOLVED,
            "Market-data symbol is not deterministically resolved",
        ), None

    if bundle.verification is None:
        return CandidateAssemblyDecision(
            bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
            CandidateExclusionReason.UPSTREAM_RESULT_MISSING,
            "Market-data verification result is missing",
        ), None
    if bundle.verification.mapping != bundle.mapping:
        raise ValueError("verification and mapping differ")
    if not bundle.verification.ready_for_market_data(as_of=as_of):
        return CandidateAssemblyDecision(
            bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
            CandidateExclusionReason.VERIFICATION_NOT_READY,
            "Market-data identity or availability is not ready as of assembly",
        ), None

    if bundle.history is None:
        return CandidateAssemblyDecision(
            bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
            CandidateExclusionReason.UPSTREAM_RESULT_MISSING,
            "History-quality result is missing",
        ), None
    history = bundle.history
    if history.listing_key != bundle.listing.key:
        raise ValueError("history belongs to another listing")
    if history.yahoo_symbol != bundle.mapping.resolved_symbol:
        raise ValueError("history and mapping symbols differ")
    if history.as_of > as_of:
        raise ValueError("history result was not known as of assembly")

    if history.route is HistoryRoute.BLOCKED:
        return CandidateAssemblyDecision(
            bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
            CandidateExclusionReason.HISTORY_BLOCKED,
            "History quality contains a blocking failure",
        ), None
    if history.route is HistoryRoute.REVIEW_REQUIRED:
        return CandidateAssemblyDecision(
            bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
            CandidateExclusionReason.HISTORY_REVIEW_REQUIRED,
            "History quality requires review",
        ), None

    if len({gate.name for gate in history.gates}) != len(history.gates):
        raise ValueError("history result contains duplicate gates")
    if len({item.name for item in history.indicators}) != len(history.indicators):
        raise ValueError("history result contains duplicate indicator capabilities")
    gates = {gate.name: gate for gate in history.gates}
    if any(gate.status is GateStatus.FAIL for gate in history.gates):
        raise ValueError("admissible history route contains a failed gate")
    liquidity = gates.get("liquidity")
    if liquidity is None:
        raise ValueError("history result has no liquidity gate")

    if history.route is HistoryRoute.STANDARD:
        if any(gate.status is not GateStatus.PASS for gate in history.gates):
            raise ValueError("STANDARD history must contain only passing gates")
        if any(not indicator.available for indicator in history.indicators):
            raise ValueError("STANDARD history must expose all indicator capabilities")
        if liquidity.status is not GateStatus.PASS:
            return CandidateAssemblyDecision(
                bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
                CandidateExclusionReason.LIQUIDITY_NOT_PASSING,
                "Standard history requires a passing liquidity gate",
            ), None
    elif history.route is HistoryRoute.RECENT_LISTING:
        metrics = dict(history.metrics)
        permitted_undetermined = {
            "history_maturity", "technical_inputs", "liquidity",
        }
        unexpected_undetermined = any(
            gate.status is GateStatus.UNDETERMINED
            and gate.name not in permitted_undetermined
            for gate in history.gates
        )
        recent_identity_ready = (
            not unexpected_undetermined
            and metrics.get("recent_listing_verified") is True
            and history.listing_event in {"IPO", "LISTING_START"}
        )
        recent_exception = (
            recent_identity_ready
            and policy.allow_recent_listing_short_liquidity
            and liquidity.status is GateStatus.UNDETERMINED
            and liquidity.reason == "INSUFFICIENT_ALIGNED_VOLUME_HISTORY"
        )
        if not recent_identity_ready or (
            liquidity.status is not GateStatus.PASS and not recent_exception
        ):
            return CandidateAssemblyDecision(
                bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
                CandidateExclusionReason.RECENT_LISTING_POLICY_NOT_MET,
                "Recent-listing liquidity exception is not fully evidenced",
            ), None
    else:
        return CandidateAssemblyDecision(
            bundle.listing.key, CandidateAssemblyStatus.EXCLUDED,
            CandidateExclusionReason.HISTORY_REVIEW_REQUIRED,
            "History route is not admissible for a new candidate",
        ), None

    candidate = ScannerCandidate(
        bundle.listing, eligibility, bundle.mapping, bundle.verification, history,
    )
    return CandidateAssemblyDecision(
        bundle.listing.key, CandidateAssemblyStatus.INCLUDED,
        CandidateExclusionReason.INCLUDED,
        "All required new-candidate gates are satisfied",
    ), candidate


def _clean_optional(value):
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or normalized.upper() in {"NAN", "NONE", "NULL", "N/A"}:
        return None
    return normalized


def _position_payload(position):
    return {field.name: getattr(position, field.name) for field in fields(position)}


def _portfolio_snapshots(positions, snapshot_id):
    raw = []
    for position in positions:
        if not isinstance(position, PortfolioPosition):
            raise ValueError("portfolio positions must be PortfolioPosition values")
        fingerprint = _digest(_position_payload(position))
        symbol = _clean_optional(resolve_yahoo_symbol(position))
        raw.append((
            fingerprint,
            str(position.broker_symbol).strip().upper(),
            str(position.market).strip().upper(),
            str(position.name).strip(),
            position,
            symbol.upper() if symbol else None,
        ))
    raw.sort(key=lambda item: item[:4])
    occurrences = defaultdict(int)
    snapshots = []
    for fingerprint, _, _, _, position, yahoo_symbol in raw:
        occurrences[fingerprint] += 1
        position_ref = f"{fingerprint}:{occurrences[fingerprint]:04d}"
        snapshots.append(PortfolioPositionSnapshot(
            position_ref=position_ref,
            snapshot_id=snapshot_id,
            broker_id="FINECO",
            name=str(position.name).strip(),
            isin=_clean_optional(position.isin),
            broker_symbol=str(position.broker_symbol).strip().upper(),
            market=str(position.market).strip().upper(),
            instrument_type=str(position.instrument_type).strip().upper(),
            currency=str(position.currency).strip().upper(),
            quantity=position.quantity,
            direction=position.direction,
            yahoo_symbol=yahoo_symbol,
            position_fingerprint=fingerprint,
        ))
    return tuple(snapshots)


class _DSU:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, value):
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[max(left, right)] = min(left, right)


def _research_members(candidates, positions):
    records = []
    for candidate in candidates:
        records.append({
            "provenance": WatchProvenance.NEW_CANDIDATE,
            "candidate_key": candidate.listing.key,
            "position_ref": None,
            "yahoo": candidate.mapping.resolved_symbol,
            "isin": _clean_optional(candidate.listing.isin),
            "listing": f"{candidate.listing.exchange}:{candidate.listing.symbol}",
            "broker": None,
            "route": candidate.history.route.value,
        })
    for position in positions:
        records.append({
            "provenance": WatchProvenance.CURRENT_POSITION,
            "candidate_key": None,
            "position_ref": position.position_ref,
            "yahoo": position.yahoo_symbol,
            "isin": position.isin,
            "listing": None,
            "broker": f"FINECO:{position.market}:{position.broker_symbol}",
            "route": None,
        })

    dsu = _DSU(len(records))
    token_owner = {}
    for index, record in enumerate(records):
        tokens = []
        for namespace, field in (
            ("YAHOO", "yahoo"), ("ISIN", "isin"),
            ("LISTING", "listing"), ("BROKER", "broker"),
        ):
            if record[field]:
                tokens.append((namespace, str(record[field]).upper()))
        for token in tokens:
            if token in token_owner:
                dsu.union(index, token_owner[token])
            else:
                token_owner[token] = index

    grouped = defaultdict(list)
    for index, record in enumerate(records):
        grouped[dsu.find(index)].append(record)

    members = []
    priority = ("YAHOO", "ISIN", "LISTING", "BROKER")
    for component in grouped.values():
        values = {namespace: set() for namespace in priority}
        for record in component:
            for namespace, field in (
                ("YAHOO", "yahoo"), ("ISIN", "isin"),
                ("LISTING", "listing"), ("BROKER", "broker"),
            ):
                if record[field]:
                    values[namespace].add(str(record[field]).upper())
        namespace = next(item for item in priority if values[item])
        subject = ResearchSubjectKey(namespace, sorted(values[namespace])[0])
        diagnostics = []
        readiness = ResearchReadiness.READY
        if len(values["YAHOO"]) == 0:
            diagnostics.append("MARKET_DATA_SYMBOL_UNRESOLVED")
            readiness = ResearchReadiness.DEGRADED
        if len(values["YAHOO"]) > 1:
            diagnostics.append("MULTIPLE_MARKET_DATA_LISTINGS")
        if len(values["ISIN"]) > 1 and len(values["YAHOO"]) == 1:
            diagnostics.append("IDENTITY_CONFLICT")
            readiness = ResearchReadiness.REVIEW_REQUIRED
        members.append(ResearchWatchMember(
            subject_key=subject,
            provenances=tuple(record["provenance"] for record in component),
            candidate_keys=tuple(
                record["candidate_key"] for record in component
                if record["candidate_key"] is not None
            ),
            position_refs=tuple(
                record["position_ref"] for record in component
                if record["position_ref"] is not None
            ),
            yahoo_symbols=tuple(values["YAHOO"]),
            isins=tuple(values["ISIN"]),
            history_routes=tuple(
                record["route"] for record in component if record["route"]
            ),
            readiness=readiness,
            diagnostics=tuple(diagnostics),
        ))
    return tuple(sorted(members, key=lambda item: item.subject_key))


def assemble_research_watch_universe(
    *,
    candidate_inputs,
    portfolio_positions,
    portfolio_snapshot_id,
    as_of,
    assembled_at=None,
    source_fingerprints=(),
    policy=None,
):
    """Assemble one immutable universe from supplied, already-acquired evidence."""
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    assembled_at = assembled_at or datetime.now(timezone.utc)
    if not isinstance(assembled_at, datetime) or assembled_at.utcoffset() is None:
        raise ValueError("assembled_at must be timezone-aware")
    if assembled_at < as_of:
        raise ValueError("assembled_at cannot precede as_of")
    if not isinstance(portfolio_snapshot_id, str) or not portfolio_snapshot_id.strip():
        raise ValueError("portfolio_snapshot_id must be nonblank")
    policy = policy or WatchUniversePolicy()

    bundles = tuple(candidate_inputs)
    if not all(isinstance(bundle, CandidateEvidenceBundle) for bundle in bundles):
        raise ValueError("candidate_inputs must contain CandidateEvidenceBundle values")
    keys = [bundle.listing.key for bundle in bundles]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate candidate listing key")

    decisions = []
    candidates = []
    for bundle in sorted(bundles, key=lambda item: item.listing.key):
        decision, candidate = _candidate_decision(bundle, as_of=as_of, policy=policy)
        decisions.append(decision)
        if candidate is not None:
            candidates.append(candidate)

    snapshots = _portfolio_snapshots(tuple(portfolio_positions), portfolio_snapshot_id.strip())
    portfolio_decisions = []
    watch_set = []
    for snapshot in snapshots:
        if snapshot.direction == "FLAT":
            portfolio_decisions.append(PortfolioWatchDecision(
                snapshot.position_ref, PortfolioWatchStatus.EXCLUDED,
                PortfolioWatchReason.FLAT_POSITION,
                "Flat broker line is not a current portfolio position",
            ))
        else:
            portfolio_decisions.append(PortfolioWatchDecision(
                snapshot.position_ref, PortfolioWatchStatus.INCLUDED,
                PortfolioWatchReason.CURRENT_POSITION,
                "Current non-flat Fineco position is always watched",
            ))
            watch_set.append(snapshot)

    members = _research_members(tuple(candidates), tuple(watch_set))
    fingerprint_core = {
        "as_of": as_of,
        "portfolio_snapshot_id": portfolio_snapshot_id.strip(),
        "policy": policy,
        "source_fingerprints": tuple(sorted(source_fingerprints)),
        "candidate_decisions": tuple(decisions),
        "candidate_set": tuple(candidates),
        "portfolio_decisions": tuple(portfolio_decisions),
        "portfolio_watch_set": tuple(watch_set),
        "members": members,
    }
    fingerprint = _digest(fingerprint_core)
    run_id = f"watch-{fingerprint[:20]}"
    return ResearchWatchUniverse(
        run_id=run_id,
        fingerprint=fingerprint,
        assembled_at=assembled_at,
        **fingerprint_core,
    )
