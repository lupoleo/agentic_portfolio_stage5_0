from datetime import datetime, timedelta, timezone

import pytest

from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceItem,
    EvidenceKind,
    EvidenceSource,
)
from app.ai.research_service import ResearchEvidence
from app.e2e.stage4_contracts import Stage4Mode
from app.e2e.stage4_frontier_priority import (
    FrontierRankingMode,
    NewsSensitiveFrontierRanker,
    seeded_venue_order,
)
from app.e2e.stage4_replenishment import (
    build_candidate_frontier,
    create_replenishment_session,
)
from app.e2e.stage4_replenishment_contracts import (
    CandidateReplenishmentPolicy,
    FrontierRankingPolicy,
)
from app.e2e.stage4_replenishment_store import (
    Stage4CandidateReplenishmentStore,
)


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def reports(count=16):
    venues = ("BIT", "NASDAQ", "NYSE", "XETRA")
    eligibility = {"all_decisions": []}
    mapping = {"mappings": []}
    for index in range(count):
        exchange = venues[index % len(venues)]
        symbol = f"SYM{index:02d}"
        eligibility["all_decisions"].append({
            "exchange": exchange,
            "symbol": symbol,
            "name": f"Company {index:02d}",
            "status": "ELIGIBLE",
        })
        mapping["mappings"].append({
            "exchange": exchange,
            "symbol": symbol,
            "mapping_status": "RESOLVED",
            "resolved_symbol": f"{symbol}.Y",
        })
    return eligibility, mapping


def session(*, mode=Stage4Mode.LIVE, iterations=3):
    policy = CandidateReplenishmentPolicy(
        frontier_ranking=FrontierRankingPolicy(
            target_size=4,
            max_iterations=iterations,
            max_workers=1,
            max_provider_retries=1,
        )
    )
    return create_replenishment_session(
        root_run_id="s4a-root",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        as_of=NOW,
        mode=mode,
        policy=policy,
    )


def news_result(ticker, *, qualified):
    items = []
    if qualified:
        published = NOW - timedelta(hours=1)
        items.append(EvidenceItem(
            evidence=ResearchEvidence(
                evidence_id="news-" + ticker,
                source_type="NEWS",
                text=(
                    f"Headline: {ticker} reports quarterly earnings "
                    "and raises guidance"
                ),
                published_at=published,
            ),
            source=EvidenceSource(
                source_id="source-" + ticker,
                provider="YAHOO_NEWS",
                source_type="NEWS",
                source_name="Reuters",
                retrieved_at=NOW,
                published_at=published,
            ),
            kind=EvidenceKind.NEWS,
            ticker=ticker,
        ))
    return EvidenceFetchResult(
        provider="YAHOO_NEWS",
        ticker=ticker,
        status=(
            EvidenceFetchStatus.SUCCESS
            if items else EvidenceFetchStatus.NO_DATA
        ),
        items=items,
        fetched_at=NOW,
    )


class RecordingProvider:
    def __init__(self, calls, qualified, *, fail=False):
        self.calls = calls
        self.qualified = qualified
        self.fail = fail

    def fetch(self, request):
        self.calls.append(request.ticker)
        if self.fail:
            raise RuntimeError("provider unavailable")
        return news_result(
            request.ticker,
            qualified=request.ticker in self.qualified,
        )


def test_keep_and_refill_reaches_target_without_rescreening(tmp_path):
    eligibility, mapping = reports()
    value = session()
    frontier = build_candidate_frontier(
        eligibility, mapping, policy=value.policy,
    )
    seeded = seeded_venue_order(frontier, seed=value.fingerprint)
    qualified = {
        seeded[index].yahoo_symbol for index in (0, 1, 4, 6)
    }
    calls = []
    store = Stage4CandidateReplenishmentStore(tmp_path / "state.db")
    store.save_session(value)
    ranker = NewsSensitiveFrontierRanker(
        store=store,
        provider_factory=lambda: RecordingProvider(calls, qualified),
        clock=lambda: NOW,
    )

    snapshot = ranker.rank(
        session=value,
        frontier=frontier,
        eligibility=eligibility,
    )

    assert snapshot.mode is FrontierRankingMode.NEWS_SENSITIVE
    assert snapshot.iteration_count == 3
    assert snapshot.screened_listing_count == 7
    assert snapshot.qualified_listing_count == 4
    assert len(calls) == len(set(calls)) == 7
    assert not snapshot.fallback_listing_keys
    assert set(snapshot.ordered_listing_keys) == {
        item.listing_key for item in seeded
        if item.yahoo_symbol in qualified
    }

    replay = ranker.rank(
        session=value,
        frontier=frontier,
        eligibility=eligibility,
    )
    assert replay == snapshot
    assert len(calls) == 7


def test_cache_only_uses_seeded_fallback_without_network(tmp_path):
    eligibility, mapping = reports()
    value = session(mode=Stage4Mode.CACHE_ONLY)
    frontier = build_candidate_frontier(
        eligibility, mapping, policy=value.policy,
    )
    store = Stage4CandidateReplenishmentStore(tmp_path / "state.db")
    store.save_session(value)
    calls = []
    ranker = NewsSensitiveFrontierRanker(
        store=store,
        provider_factory=lambda: RecordingProvider(calls, set()),
        clock=lambda: NOW,
    )
    snapshot = ranker.rank(
        session=value,
        frontier=frontier,
        eligibility=eligibility,
    )
    expected = seeded_venue_order(frontier, seed=value.fingerprint)[:4]
    assert snapshot.mode is FrontierRankingMode.SEEDED_FALLBACK
    assert snapshot.network_calls == 0
    assert snapshot.screened_listing_count == 0
    assert snapshot.ordered_listing_keys == tuple(
        item.listing_key for item in expected
    )
    assert not calls


def test_provider_wide_failure_opens_circuit_and_falls_back(tmp_path):
    eligibility, mapping = reports()
    value = session(iterations=10)
    frontier = build_candidate_frontier(
        eligibility, mapping, policy=value.policy,
    )
    store = Stage4CandidateReplenishmentStore(tmp_path / "state.db")
    store.save_session(value)
    calls = []
    snapshot = NewsSensitiveFrontierRanker(
        store=store,
        provider_factory=lambda: RecordingProvider(
            calls, set(), fail=True,
        ),
        clock=lambda: NOW,
    ).rank(
        session=value,
        frontier=frontier,
        eligibility=eligibility,
    )
    assert snapshot.provider_circuit_open
    assert snapshot.iteration_count == 1
    assert snapshot.screened_listing_count == 4
    assert snapshot.network_calls == 8
    assert len(snapshot.fallback_listing_keys) == 4
    assert snapshot.mode is FrontierRankingMode.SEEDED_FALLBACK


def test_ranking_snapshot_is_append_only(tmp_path):
    eligibility, mapping = reports()
    value = session(mode=Stage4Mode.CACHE_ONLY)
    frontier = build_candidate_frontier(
        eligibility, mapping, policy=value.policy,
    )
    store = Stage4CandidateReplenishmentStore(tmp_path / "state.db")
    store.save_session(value)
    snapshot = NewsSensitiveFrontierRanker(
        store=store, clock=lambda: NOW,
    ).rank(
        session=value,
        frontier=frontier,
        eligibility=eligibility,
    )
    store.save_frontier_ranking(snapshot)
    assert store.get_frontier_ranking(value.session_id) == snapshot

    changed = snapshot.model_copy(update={"network_calls": 1})
    with pytest.raises(ValueError, match="immutable"):
        store.save_frontier_ranking(changed)
