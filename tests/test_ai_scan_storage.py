from datetime import datetime, timedelta, timezone

import pytest

from app.ai.scan_models import (
    CandidateAction,
    CandidateOrigin,
    CatalystType,
    MarketScan,
    MarketScanStatus,
    ScannerType,
    ScanCandidate,
    ScanUniverseType,
    SignalType,
)
from app.cio.storage import Stage3Store


BASE = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)


def make_scan(
    scan_id: str = "SCAN-001",
    *,
    created_at: datetime = BASE,
    scanner_type: ScannerType = ScannerType.EVENT,
    universe_type: ScanUniverseType = ScanUniverseType.MARKET,
    status: MarketScanStatus = MarketScanStatus.COMPLETED,
    snapshot_id: str | None = None,
    risk_state_id: str | None = None,
) -> MarketScan:
    return MarketScan(
        scan_id=scan_id,
        created_at=created_at,
        scanner_type=scanner_type,
        scanner_version="1.0",
        universe_type=universe_type,
        universe_name="TEST",
        portfolio_snapshot_id=snapshot_id,
        risk_state_id=risk_state_id,
        symbols_requested=["NVDA", "MRVL", "PATH"],
        symbols_scanned=["NVDA", "MRVL", "PATH"],
        candidate_ids=[],
        status=status,
        started_at=created_at,
        completed_at=(
            created_at + timedelta(seconds=2)
            if status != MarketScanStatus.RUNNING
            else None
        ),
    )


def make_candidate(
    candidate_id: str,
    scan_id: str,
    ticker: str,
    *,
    created_at: datetime = BASE,
    origin: CandidateOrigin = CandidateOrigin.EXTERNAL,
    action: CandidateAction = CandidateAction.NEW_LONG,
    signal_type: SignalType = SignalType.EVENT,
    snapshot_id: str | None = None,
    risk_state_id: str | None = None,
    requires_research: bool = True,
) -> ScanCandidate:
    return ScanCandidate(
        candidate_id=candidate_id,
        scan_id=scan_id,
        created_at=created_at,
        ticker=ticker,
        origin=origin,
        action=action,
        signal_type=signal_type,
        raw_score=81.0,
        scanner_confidence=0.77,
        thesis_summary="Synthetic scanner candidate",
        catalyst_type=CatalystType.EARNINGS,
        evidence_ids=["NEWS-001"],
        inference_ids=["AI-001"],
        portfolio_snapshot_id=snapshot_id,
        risk_state_id=risk_state_id,
        requires_research=requires_research,
    )


def test_market_scan_save_get_roundtrip(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    scan = make_scan()

    store.save_market_scan(scan)

    assert store.get_market_scan(scan.scan_id) == scan


def test_market_scan_upsert_and_filters(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    first = make_scan(
        "SCAN-001",
        scanner_type=ScannerType.EVENT,
        snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
    )
    store.save_market_scan(first)

    updated = first.model_copy(
        update={"scanner_version": "1.1"}
    )
    store.save_market_scan(updated)

    rows = store.list_market_scans(
        scanner_type=ScannerType.EVENT.value,
        universe_type=ScanUniverseType.MARKET.value,
        status=MarketScanStatus.COMPLETED.value,
        portfolio_snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
    )

    assert len(rows) == 1
    assert rows[0].scanner_version == "1.1"


def test_latest_market_scan_is_most_recent(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    store.save_market_scan(make_scan("SCAN-001", created_at=BASE))
    store.save_market_scan(
        make_scan("SCAN-002", created_at=BASE + timedelta(minutes=1))
    )

    latest = store.get_latest_market_scan()

    assert latest is not None
    assert latest.scan_id == "SCAN-002"


def test_scan_candidate_requires_existing_parent_scan(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    candidate = make_candidate("CAND-001", "SCAN-MISSING", "PATH")

    with pytest.raises(ValueError, match="MarketScan not found"):
        store.save_scan_candidate(candidate)


def test_scan_candidate_save_get_roundtrip(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    scan = make_scan()
    store.save_market_scan(scan)

    candidate = make_candidate("CAND-001", scan.scan_id, "path")
    store.save_scan_candidate(candidate)

    loaded = store.get_scan_candidate(candidate.candidate_id)

    assert loaded == candidate
    assert loaded.ticker == "PATH"


def test_scan_candidate_upsert_does_not_duplicate(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    scan = make_scan()
    store.save_market_scan(scan)

    first = make_candidate("CAND-001", scan.scan_id, "PATH")
    store.save_scan_candidate(first)

    updated = first.model_copy(
        update={
            "raw_score": 92.0,
            "scanner_confidence": 0.91,
        }
    )
    store.save_scan_candidate(updated)

    rows = store.list_scan_candidates(scan_id=scan.scan_id)

    assert len(rows) == 1
    assert rows[0].raw_score == 92.0
    assert rows[0].scanner_confidence == 0.91


def test_list_candidates_for_scan_and_order(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    scan = make_scan()
    store.save_market_scan(scan)

    store.save_scan_candidate(
        make_candidate("CAND-001", scan.scan_id, "PATH", created_at=BASE)
    )
    store.save_scan_candidate(
        make_candidate(
            "CAND-002",
            scan.scan_id,
            "MRVL",
            created_at=BASE + timedelta(minutes=1),
            action=CandidateAction.NEW_SHORT,
        )
    )

    rows = store.list_scan_candidates_for_scan(scan.scan_id)

    assert [row.candidate_id for row in rows] == [
        "CAND-002",
        "CAND-001",
    ]


def test_scan_candidate_filters(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    scan = make_scan(
        snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
    )
    store.save_market_scan(scan)

    external = make_candidate(
        "CAND-001",
        scan.scan_id,
        "PATH",
        action=CandidateAction.NEW_LONG,
        snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
        requires_research=True,
    )
    portfolio = make_candidate(
        "CAND-002",
        scan.scan_id,
        "NVDA",
        origin=CandidateOrigin.PORTFOLIO,
        action=CandidateAction.REDUCE,
        signal_type=SignalType.RISK,
        snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
        requires_research=False,
    )

    store.save_scan_candidate(external)
    store.save_scan_candidate(portfolio)

    rows = store.list_scan_candidates(
        ticker="nvda",
        origin=CandidateOrigin.PORTFOLIO.value,
        action=CandidateAction.REDUCE.value,
        signal_type=SignalType.RISK.value,
        portfolio_snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
        requires_research=False,
    )

    assert [row.candidate_id for row in rows] == ["CAND-002"]


def test_latest_scan_candidate_filter(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    scan = make_scan()
    store.save_market_scan(scan)

    store.save_scan_candidate(
        make_candidate("CAND-001", scan.scan_id, "PATH", created_at=BASE)
    )
    store.save_scan_candidate(
        make_candidate(
            "CAND-002",
            scan.scan_id,
            "PATH",
            created_at=BASE + timedelta(minutes=1),
        )
    )

    latest = store.get_latest_scan_candidate(
        ticker="path",
        action=CandidateAction.NEW_LONG.value,
    )

    assert latest is not None
    assert latest.candidate_id == "CAND-002"


def test_missing_scan_and_candidate_return_none(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")

    assert store.get_market_scan("SCAN-MISSING") is None
    assert store.get_scan_candidate("CAND-MISSING") is None
