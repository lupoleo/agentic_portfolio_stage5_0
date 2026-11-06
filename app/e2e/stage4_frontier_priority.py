"""Deterministic news-sensitive frontier ranking for E2E-S4.0A.2."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping

from pydantic import Field, field_validator, model_validator

from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceKind,
    EvidenceProviderResponseError,
    EvidenceRequest,
)
from app.ai.news_evidence_provider import (
    YahooNewsEvidenceProvider,
    normalize_company_name,
)
from app.e2e.stage4_contracts import (
    Stage4Mode,
    Stage4Model,
    canonical_fingerprint,
)
from app.e2e.stage4_replenishment_contracts import (
    CandidateListingRef,
    CandidateReplenishmentSession,
    FrontierRankingPolicy,
    normalize_listing_key,
)


class FrontierNewsStatus(str, Enum):
    NEWS_QUALIFIED = "NEWS_QUALIFIED"
    NO_PRICE_SENSITIVE_EVENT = "NO_PRICE_SENSITIVE_EVENT"
    NEWS_PROVIDER_UNAVAILABLE = "NEWS_PROVIDER_UNAVAILABLE"
    NEWS_RESPONSE_INVALID = "NEWS_RESPONSE_INVALID"
    NOT_SCREENED = "NOT_SCREENED"


class FrontierRankingMode(str, Enum):
    NEWS_SENSITIVE = "NEWS_SENSITIVE"
    MIXED = "MIXED"
    SEEDED_FALLBACK = "SEEDED_FALLBACK"


class FrontierNewsAssessment(Stage4Model):
    listing_key: str
    yahoo_symbol: str
    status: FrontierNewsStatus
    event_severity: int = Field(default=0, ge=0, le=5)
    latest_published_at: datetime | None = None
    direct_item_count: int = Field(default=0, ge=0)
    independent_source_count: int = Field(default=0, ge=0)
    matched_event_classes: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    seeded_order_key: str

    @field_validator("listing_key", mode="before")
    @classmethod
    def listing_identity(cls, value):
        return normalize_listing_key(value)

    @field_validator("yahoo_symbol", "seeded_order_key", mode="before")
    @classmethod
    def nonblank(cls, value):
        result = str(value).strip()
        if not result:
            raise ValueError("frontier assessment identity must be nonblank")
        return result

    @model_validator(mode="after")
    def validate_assessment(self):
        if self.latest_published_at is not None:
            if self.latest_published_at.utcoffset() is None:
                raise ValueError("news publication time must be timezone-aware")
        event_classes = tuple(sorted({
            str(value).strip().upper()
            for value in self.matched_event_classes
            if str(value).strip()
        }))
        evidence_ids = tuple(sorted({
            str(value).strip()
            for value in self.evidence_ids
            if str(value).strip()
        }))
        object.__setattr__(self, "matched_event_classes", event_classes)
        object.__setattr__(self, "evidence_ids", evidence_ids)
        if self.status is FrontierNewsStatus.NEWS_QUALIFIED:
            if not self.event_severity or not event_classes or not evidence_ids:
                raise ValueError("qualified news requires scored evidence")
        elif any((
            self.event_severity,
            self.direct_item_count,
            self.independent_source_count,
            bool(event_classes),
            bool(evidence_ids),
        )):
            raise ValueError("unqualified assessment cannot carry scored evidence")
        return self


class FrontierRankingSnapshot(Stage4Model):
    snapshot_id: str
    session_id: str
    frontier_input_fingerprint: str
    as_of: datetime
    created_at: datetime
    policy: FrontierRankingPolicy
    mode: FrontierRankingMode
    iteration_count: int = Field(ge=0)
    screened_listing_count: int = Field(ge=0)
    qualified_listing_count: int = Field(ge=0)
    network_calls: int = Field(ge=0)
    provider_circuit_open: bool = False
    ordered_listing_keys: tuple[str, ...]
    qualified_listing_keys: tuple[str, ...] = ()
    fallback_listing_keys: tuple[str, ...] = ()
    screened_no_event_listing_keys: tuple[str, ...] = ()
    provider_unavailable_listing_keys: tuple[str, ...] = ()
    invalid_response_listing_keys: tuple[str, ...] = ()
    assessments: tuple[FrontierNewsAssessment, ...] = ()
    fingerprint: str

    @field_validator(
        "snapshot_id",
        "session_id",
        "frontier_input_fingerprint",
        "fingerprint",
        mode="before",
    )
    @classmethod
    def identity_fields(cls, value):
        result = str(value).strip()
        if not result:
            raise ValueError("frontier ranking identity must be nonblank")
        return result

    @model_validator(mode="after")
    def validate_snapshot(self):
        for value in (self.as_of, self.created_at):
            if value.utcoffset() is None:
                raise ValueError("frontier ranking timestamps must be aware")
        if self.created_at < self.as_of:
            raise ValueError("ranking cannot be created before session as_of")
        ordered = tuple(normalize_listing_key(v) for v in self.ordered_listing_keys)
        if not ordered or len(set(ordered)) != len(ordered):
            raise ValueError("ordered frontier must be nonempty and unique")
        if len(ordered) > self.policy.target_size:
            raise ValueError("ordered frontier exceeds ranking target")
        qualified = tuple(normalize_listing_key(v) for v in self.qualified_listing_keys)
        fallback = tuple(normalize_listing_key(v) for v in self.fallback_listing_keys)
        if ordered != qualified + fallback:
            raise ValueError("ordered frontier must be qualified then fallback")
        if self.qualified_listing_count != len(qualified):
            raise ValueError("qualified count differs from qualified keys")
        if self.screened_listing_count != len(self.assessments):
            raise ValueError("screened count differs from assessments")
        assessment_keys = tuple(value.listing_key for value in self.assessments)
        if len(set(assessment_keys)) != len(assessment_keys):
            raise ValueError("frontier assessment identities must be unique")
        expected = frontier_ranking_snapshot_fingerprint(
            self, exclude_fingerprint=True
        )
        if self.fingerprint != expected:
            raise ValueError("frontier ranking fingerprint differs")
        if self.snapshot_id != "frontier-" + expected[:24]:
            raise ValueError("frontier ranking snapshot_id differs")
        return self


EVENT_PATTERNS: tuple[tuple[int, str, re.Pattern[str]], ...] = (
    (5, "DISTRESS", re.compile(
        r"\b(bankrupt(?:cy)?|insolven(?:t|cy)|default(?:ed)?|restructur(?:e|ing))\b",
        re.IGNORECASE,
    )),
    (5, "M_AND_A", re.compile(
        r"\b(takeover|acqui(?:re|res|red|sition)|merger|tender offer|buyout)\b",
        re.IGNORECASE,
    )),
    (5, "PROFIT_WARNING", re.compile(
        r"\b(profit warning|cuts? guidance|lowers? guidance|withdraws? guidance)\b",
        re.IGNORECASE,
    )),
    (5, "REGULATORY_DECISION", re.compile(
        r"\b(fda|regulator|regulatory).{0,30}\b(approv(?:e|al)|reject(?:s|ed|ion)|ban(?:s|ned)?)\b",
        re.IGNORECASE,
    )),
    (4, "EARNINGS_GUIDANCE", re.compile(
        r"\b(earnings|quarterly results?|full[- ]year results?|guidance|forecast|outlook)\b",
        re.IGNORECASE,
    )),
    (4, "CAPITAL_ACTION", re.compile(
        r"\b(capital raise|rights issue|share offering|buyback|special dividend|dividend cut)\b",
        re.IGNORECASE,
    )),
    (4, "MATERIAL_CONTRACT", re.compile(
        r"\b(major contract|contract award|wins? contract|purchase order|strategic deal)\b",
        re.IGNORECASE,
    )),
    (4, "LEGAL", re.compile(
        r"\b(lawsuit|litigation|settlement|antitrust|criminal investigation)\b",
        re.IGNORECASE,
    )),
    (4, "MANAGEMENT", re.compile(
        r"\b(ceo|chief executive|cfo|chief financial officer).{0,30}\b(resigns?|steps? down|dismissed|appointed)\b",
        re.IGNORECASE,
    )),
    (3, "ANALYST_ACTION", re.compile(
        r"\b(upgrade[sd]?|downgrade[sd]?|price target|rating raised|rating cut)\b",
        re.IGNORECASE,
    )),
    (3, "PRODUCT_EVENT", re.compile(
        r"\b(product launch|launches|approval|clinical trial|trial results?)\b",
        re.IGNORECASE,
    )),
)


def _stable_key(seed: str, value: str) -> str:
    return hashlib.sha256(f"{seed}|{value}".encode("utf-8")).hexdigest()


def seeded_venue_order(
    frontier: Iterable[CandidateListingRef], *, seed: str,
) -> tuple[CandidateListingRef, ...]:
    """Seed-shuffle each venue, then interleave venues deterministically."""
    groups: dict[str, list[CandidateListingRef]] = {}
    for value in frontier:
        groups.setdefault(value.exchange, []).append(value)
    for venue, values in groups.items():
        values.sort(key=lambda item: _stable_key(seed, item.listing_key))
    venues = sorted(groups, key=lambda venue: _stable_key(seed, "VENUE:" + venue))
    output: list[CandidateListingRef] = []
    position = 0
    while True:
        added = False
        for venue in venues:
            values = groups[venue]
            if position < len(values):
                output.append(values[position])
                added = True
        if not added:
            break
        position += 1
    return tuple(output)


def aliases_from_eligibility(
    eligibility: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for row in eligibility.get("all_decisions", ()):
        exchange = str(row.get("exchange") or "").strip().upper()
        symbol = str(row.get("symbol") or "").strip().upper()
        if not exchange or not symbol:
            continue
        aliases = []
        # AI-8C.2-R4: headlines name companies without their legal form
        # ("UniCredit", not "UniCredit S.p.A."), so the normalized names are
        # aliases too.
        for value in (
            symbol,
            row.get("name"),
            row.get("instrument_name"),
            normalize_company_name(row.get("name")),
            normalize_company_name(row.get("instrument_name")),
        ):
            text = str(value or "").strip()
            if text and text.casefold() not in {v.casefold() for v in aliases}:
                aliases.append(text)
        result[f"{exchange}:{symbol}"] = tuple(aliases)
    return result


def _direct_match(text: str, aliases: Iterable[str]) -> bool:
    for alias in aliases:
        normalized = str(alias).strip()
        if not normalized:
            continue
        pattern = rf"(?<![A-Za-z0-9]){re.escape(normalized)}(?![A-Za-z0-9])"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def assess_news_result(
    listing: CandidateListingRef,
    result: EvidenceFetchResult,
    *, as_of: datetime,
    lookback_days: int,
    aliases: Iterable[str],
    seed: str,
) -> FrontierNewsAssessment:
    if result.ticker != listing.yahoo_symbol:
        raise ValueError("news result belongs to another Yahoo symbol")
    cutoff = as_of - timedelta(days=lookback_days)
    matched_classes: set[str] = set()
    evidence_ids: set[str] = set()
    publishers: set[str] = set()
    direct_count = 0
    severity = 0
    latest: datetime | None = None
    target_aliases = tuple(aliases) + (
        listing.symbol,
        listing.yahoo_symbol,
        listing.yahoo_symbol.split(".", 1)[0],
    )

    for item in result.items:
        published = item.source.published_at
        if published is None or not cutoff <= published <= as_of:
            continue
        text = item.evidence.text
        if not _direct_match(text, target_aliases):
            continue
        item_matches = tuple(
            (weight, event_class)
            for weight, event_class, pattern in EVENT_PATTERNS
            if pattern.search(text)
        )
        if not item_matches:
            continue
        direct_count += 1
        severity = max(severity, *(weight for weight, _ in item_matches))
        matched_classes.update(value for _, value in item_matches)
        evidence_ids.add(item.evidence.evidence_id)
        publishers.add(item.source.source_name.strip().casefold())
        latest = published if latest is None else max(latest, published)

    status = (
        FrontierNewsStatus.NEWS_QUALIFIED
        if evidence_ids
        else FrontierNewsStatus.NO_PRICE_SENSITIVE_EVENT
    )
    return FrontierNewsAssessment(
        listing_key=listing.listing_key,
        yahoo_symbol=listing.yahoo_symbol,
        status=status,
        event_severity=severity,
        latest_published_at=latest,
        direct_item_count=direct_count,
        independent_source_count=len(publishers),
        matched_event_classes=tuple(matched_classes),
        evidence_ids=tuple(evidence_ids),
        diagnostics=tuple(result.warnings),
        seeded_order_key=_stable_key(seed, listing.listing_key),
    )


def frontier_ranking_snapshot_fingerprint(
    value: FrontierRankingSnapshot | Mapping[str, Any],
    *, exclude_fingerprint: bool = False,
) -> str:
    payload = _json_safe(value)
    payload.pop("snapshot_id", None)
    if exclude_fingerprint:
        payload.pop("fingerprint", None)
    return canonical_fingerprint(payload)


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return value


class NewsSensitiveFrontierRanker:
    """Build and persist one immutable keep-and-refill ranking snapshot."""

    def __init__(
        self,
        *,
        store,
        provider_factory: Callable[[], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.provider_factory = provider_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def rank(
        self,
        *,
        session: CandidateReplenishmentSession,
        frontier: Iterable[CandidateListingRef],
        eligibility: Mapping[str, Any],
    ) -> FrontierRankingSnapshot:
        frontier = tuple(frontier)
        policy = session.policy.frontier_ranking
        aliases = aliases_from_eligibility(eligibility)
        frontier_input_fingerprint = canonical_fingerprint({
            "frontier": [
                value.model_dump(mode="json") for value in frontier
            ],
            "aliases": {
                value.listing_key: list(
                    aliases.get(value.listing_key, ())
                )
                for value in frontier
            },
        })
        existing = self.store.get_frontier_ranking(session.session_id)
        if existing is not None:
            if existing.policy != policy:
                raise ValueError("persisted frontier ranking policy differs")
            if (
                existing.frontier_input_fingerprint
                != frontier_input_fingerprint
            ):
                raise ValueError(
                    "persisted frontier ranking input differs"
                )
            return existing

        seeded = seeded_venue_order(frontier, seed=session.fingerprint)
        if not seeded:
            raise ValueError("cannot rank an empty candidate frontier")
        target = min(policy.target_size, len(seeded))
        evaluations: dict[str, FrontierNewsAssessment] = {}
        qualified: dict[str, FrontierNewsAssessment] = {}
        provider_circuit_open = False
        iteration_count = 0
        network_calls = 0
        cursor = 0

        if session.mode is Stage4Mode.LIVE:
            while (
                iteration_count < policy.max_iterations
                and len(qualified) < target
                and cursor < len(seeded)
            ):
                needed = target - len(qualified)
                batch = seeded[cursor:cursor + needed]
                cursor += len(batch)
                if not batch:
                    break
                iteration_count += 1
                with ThreadPoolExecutor(max_workers=policy.max_workers) as executor:
                    futures = [
                        executor.submit(
                            self._screen_one,
                            value,
                            session=session,
                            aliases=aliases.get(value.listing_key, ()),
                        )
                        for value in batch
                    ]
                    batch_results = tuple(future.result() for future in futures)
                network_calls += sum(value[1] for value in batch_results)
                assessments = tuple(value[0] for value in batch_results)
                for assessment in assessments:
                    evaluations[assessment.listing_key] = assessment
                    if assessment.status is FrontierNewsStatus.NEWS_QUALIFIED:
                        qualified[assessment.listing_key] = assessment
                if assessments and all(
                    value.status in {
                        FrontierNewsStatus.NEWS_PROVIDER_UNAVAILABLE,
                        FrontierNewsStatus.NEWS_RESPONSE_INVALID,
                    }
                    for value in assessments
                ):
                    provider_circuit_open = True
                    break

        qualified_order = tuple(sorted(
            qualified,
            key=lambda key: self._qualified_sort_key(qualified[key]),
        ))[:target]
        remaining = target - len(qualified_order)
        unseen = tuple(
            value.listing_key for value in seeded
            if value.listing_key not in evaluations
            and value.listing_key not in qualified
        )
        fallback = list(unseen[:remaining])
        if len(fallback) < remaining:
            screened_unqualified = tuple(
                value.listing_key for value in seeded
                if value.listing_key in evaluations
                and value.listing_key not in qualified
            )
            fallback.extend(
                screened_unqualified[:remaining - len(fallback)]
            )
        fallback_order = tuple(fallback)
        ordered = qualified_order + fallback_order
        if qualified_order and fallback_order:
            mode = FrontierRankingMode.MIXED
        elif qualified_order:
            mode = FrontierRankingMode.NEWS_SENSITIVE
        else:
            mode = FrontierRankingMode.SEEDED_FALLBACK

        created_at = self.clock()
        if created_at.utcoffset() is None:
            raise ValueError("frontier ranker clock must be timezone-aware")
        if created_at < session.as_of:
            created_at = session.as_of
        assessments = tuple(sorted(
            evaluations.values(), key=lambda value: value.listing_key,
        ))
        payload = {
            "session_id": session.session_id,
            "frontier_input_fingerprint": frontier_input_fingerprint,
            "as_of": session.as_of,
            "created_at": created_at,
            "policy": policy,
            "mode": mode,
            "iteration_count": iteration_count,
            "screened_listing_count": len(assessments),
            "qualified_listing_count": len(qualified_order),
            "network_calls": network_calls,
            "provider_circuit_open": provider_circuit_open,
            "ordered_listing_keys": ordered,
            "qualified_listing_keys": qualified_order,
            "fallback_listing_keys": fallback_order,
            "screened_no_event_listing_keys": tuple(sorted(
                value.listing_key for value in assessments
                if value.status is FrontierNewsStatus.NO_PRICE_SENSITIVE_EVENT
            )),
            "provider_unavailable_listing_keys": tuple(sorted(
                value.listing_key for value in assessments
                if value.status is FrontierNewsStatus.NEWS_PROVIDER_UNAVAILABLE
            )),
            "invalid_response_listing_keys": tuple(sorted(
                value.listing_key for value in assessments
                if value.status is FrontierNewsStatus.NEWS_RESPONSE_INVALID
            )),
            "assessments": assessments,
        }
        fingerprint = frontier_ranking_snapshot_fingerprint(payload)
        snapshot = FrontierRankingSnapshot(
            snapshot_id="frontier-" + fingerprint[:24],
            **payload,
            fingerprint=fingerprint,
        )
        self.store.save_frontier_ranking(snapshot)
        return snapshot

    def _screen_one(self, listing, *, session, aliases):
        policy = session.policy.frontier_ranking
        calls = 0
        last_diagnostic = "NEWS_PROVIDER_UNAVAILABLE"
        last_status = FrontierNewsStatus.NEWS_PROVIDER_UNAVAILABLE
        for _ in range(policy.max_provider_retries + 1):
            calls += 1
            try:
                provider = (
                    self.provider_factory()
                    if self.provider_factory is not None
                    else YahooNewsEvidenceProvider(
                        default_lookback_days=policy.news_lookback_days
                    )
                )
                result = provider.fetch(EvidenceRequest(
                    ticker=listing.yahoo_symbol,
                    as_of=session.as_of,
                    kinds=[EvidenceKind.NEWS],
                    max_items=policy.max_news_items,
                    metadata={"company_names": [
                        alias for alias in aliases
                        if alias.strip().upper() != listing.symbol.strip().upper()
                    ]},
                ))
                if not isinstance(result, EvidenceFetchResult):
                    last_diagnostic = "INVALID_NEWS_PROVIDER_RESPONSE"
                    last_status = FrontierNewsStatus.NEWS_RESPONSE_INVALID
                    continue
                return (
                    assess_news_result(
                        listing,
                        result,
                        as_of=session.as_of,
                        lookback_days=policy.news_lookback_days,
                        aliases=aliases,
                        seed=session.fingerprint,
                    ),
                    calls,
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except EvidenceProviderResponseError as exc:
                last_diagnostic = type(exc).__name__ + ":" + str(exc)
                if any(
                    marker in str(exc).casefold()
                    for marker in (
                        "unsupported",
                        "invalid",
                        "malformed",
                        "unusable",
                    )
                ):
                    last_status = FrontierNewsStatus.NEWS_RESPONSE_INVALID
                else:
                    last_status = (
                        FrontierNewsStatus.NEWS_PROVIDER_UNAVAILABLE
                    )
            except Exception as exc:
                last_diagnostic = type(exc).__name__
        return (
            FrontierNewsAssessment(
                listing_key=listing.listing_key,
                yahoo_symbol=listing.yahoo_symbol,
                status=last_status,
                diagnostics=(last_diagnostic,),
                seeded_order_key=_stable_key(
                    session.fingerprint, listing.listing_key
                ),
            ),
            calls,
        )

    @staticmethod
    def _qualified_sort_key(value: FrontierNewsAssessment):
        published = (
            value.latest_published_at.timestamp()
            if value.latest_published_at is not None
            else 0.0
        )
        return (
            -value.event_severity,
            -published,
            -value.direct_item_count,
            -value.independent_source_count,
            value.seeded_order_key,
        )
