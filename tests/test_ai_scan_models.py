from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

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


NOW = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)


def test_completed_market_scan_is_valid():
    scan = MarketScan(
        scan_id="SCAN-001",
        created_at=NOW,
        scanner_type=ScannerType.EVENT,
        scanner_version="1.0",
        universe_type=ScanUniverseType.INDEX,
        universe_name="NASDAQ100",
        symbols_requested=["NVDA", "MRVL"],
        symbols_scanned=["NVDA", "MRVL"],
        candidate_ids=["CAND-001"],
        status=MarketScanStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=5),
    )
    assert scan.status == MarketScanStatus.COMPLETED
    assert scan.candidate_ids == ["CAND-001"]


def test_running_market_scan_cannot_be_completed():
    with pytest.raises(ValidationError):
        MarketScan(
            scan_id="SCAN-001",
            created_at=NOW,
            scanner_type=ScannerType.MARKET,
            scanner_version="1.0",
            universe_type=ScanUniverseType.MARKET,
            status=MarketScanStatus.RUNNING,
            started_at=NOW,
            completed_at=NOW,
        )


def test_terminal_market_scan_requires_completed_at():
    with pytest.raises(ValidationError):
        MarketScan(
            scan_id="SCAN-001",
            created_at=NOW,
            scanner_type=ScannerType.MARKET,
            scanner_version="1.0",
            universe_type=ScanUniverseType.MARKET,
            status=MarketScanStatus.COMPLETED,
            started_at=NOW,
        )


def test_market_scan_rejects_completion_before_start():
    with pytest.raises(ValidationError):
        MarketScan(
            scan_id="SCAN-001",
            created_at=NOW,
            scanner_type=ScannerType.MARKET,
            scanner_version="1.0",
            universe_type=ScanUniverseType.MARKET,
            status=MarketScanStatus.FAILED,
            started_at=NOW,
            completed_at=NOW - timedelta(seconds=1),
        )


def test_portfolio_candidate_accepts_existing_position_action():
    candidate = ScanCandidate(
        candidate_id="CAND-001",
        scan_id="SCAN-001",
        created_at=NOW,
        ticker=" nvda ",
        origin=CandidateOrigin.PORTFOLIO,
        action=CandidateAction.REDUCE,
        signal_type=SignalType.RISK,
        raw_score=88.0,
        scanner_confidence=0.82,
        portfolio_snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
    )
    assert candidate.ticker == "NVDA"
    assert candidate.action == CandidateAction.REDUCE


@pytest.mark.parametrize(
    "action",
    [CandidateAction.NEW_LONG, CandidateAction.NEW_SHORT],
)
def test_portfolio_candidate_rejects_new_exposure_actions(action):
    with pytest.raises(ValidationError):
        ScanCandidate(
            candidate_id="CAND-001",
            scan_id="SCAN-001",
            created_at=NOW,
            ticker="NVDA",
            origin=CandidateOrigin.PORTFOLIO,
            action=action,
            signal_type=SignalType.RISK,
        )


@pytest.mark.parametrize(
    "action",
    [CandidateAction.NEW_LONG, CandidateAction.NEW_SHORT],
)
def test_external_candidate_accepts_new_exposure_actions(action):
    candidate = ScanCandidate(
        candidate_id="CAND-002",
        scan_id="SCAN-001",
        created_at=NOW,
        ticker="path",
        origin=CandidateOrigin.EXTERNAL,
        action=action,
        signal_type=SignalType.EVENT,
        catalyst_type=CatalystType.EARNINGS,
    )
    assert candidate.ticker == "PATH"


@pytest.mark.parametrize(
    "action",
    [
        CandidateAction.ADD,
        CandidateAction.REDUCE,
        CandidateAction.EXIT,
        CandidateAction.HEDGE,
        CandidateAction.REVERSE,
        CandidateAction.NO_ACTION,
    ],
)
def test_external_candidate_rejects_portfolio_actions(action):
    with pytest.raises(ValidationError):
        ScanCandidate(
            candidate_id="CAND-002",
            scan_id="SCAN-001",
            created_at=NOW,
            ticker="PATH",
            origin=CandidateOrigin.EXTERNAL,
            action=action,
            signal_type=SignalType.EVENT,
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_candidate_confidence_is_bounded(confidence):
    with pytest.raises(ValidationError):
        ScanCandidate(
            candidate_id="CAND-003",
            scan_id="SCAN-001",
            created_at=NOW,
            ticker="MRVL",
            origin=CandidateOrigin.EXTERNAL,
            action=CandidateAction.NEW_SHORT,
            signal_type=SignalType.EVENT,
            scanner_confidence=confidence,
        )


def test_candidate_keeps_provenance_without_becoming_trade_opportunity():
    candidate = ScanCandidate(
        candidate_id="CAND-004",
        scan_id="SCAN-002",
        created_at=NOW,
        ticker="MRVL",
        origin=CandidateOrigin.EXTERNAL,
        action=CandidateAction.NEW_SHORT,
        signal_type=SignalType.EVENT,
        evidence_ids=["NEWS-001"],
        inference_ids=["AI-001"],
        requires_research=True,
    )
    dumped = candidate.model_dump(mode="json")
    assert dumped["evidence_ids"] == ["NEWS-001"]
    assert dumped["inference_ids"] == ["AI-001"]
    assert dumped["requires_research"] is True
    assert "direction" not in dumped
    assert "opportunity_id" not in dumped
