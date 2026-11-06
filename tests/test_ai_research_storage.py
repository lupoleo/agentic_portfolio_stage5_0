from datetime import datetime, timedelta, timezone

import pytest

from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    OpportunityResearch,
    ResearchStatus,
)
from app.ai.scan_models import (
    CandidateAction,
    CandidateOrigin,
    MarketScan,
    MarketScanStatus,
    ScanCandidate,
    ScannerType,
    ScanUniverseType,
    SignalType,
)
from app.cio.storage import Stage3Store

NOW = datetime(2026, 8, 30, 21, 45, tzinfo=timezone.utc)

def make_scan(scan_id="SCAN-001"):
    return MarketScan(
        scan_id=scan_id,
        created_at=NOW,
        scanner_type=ScannerType.MARKET,
        scanner_version="1.0",
        universe_type=ScanUniverseType.CUSTOM,
        universe_name="AI6B TEST",
        portfolio_snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
        symbols_requested=["PATH"],
        symbols_scanned=["PATH"],
        candidate_ids=["CAND-001"],
        status=MarketScanStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=2),
        metadata={},
    )

def make_candidate(**overrides):
    data = dict(
        candidate_id="CAND-001",
        scan_id="SCAN-001",
        created_at=NOW,
        ticker="PATH",
        origin=CandidateOrigin.EXTERNAL,
        action=CandidateAction.NEW_LONG,
        signal_type=SignalType.FUNDAMENTAL,
        raw_score=72.0,
        scanner_confidence=0.76,
        thesis_summary="Candidate for research.",
        portfolio_snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
        requires_research=True,
        metadata={},
    )
    data.update(overrides)
    return ScanCandidate(**data)

def make_research(**overrides):
    data = dict(
        research_id="RES-001",
        candidate_id="CAND-001",
        scan_id="SCAN-001",
        created_at=NOW + timedelta(seconds=10),
        ticker="PATH",
        portfolio_snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
        research_status=ResearchStatus.COMPLETE,
        market_context="Software market constructive.",
        fundamental_context="Growth remains positive.",
        technical_context="Momentum improving.",
        event_context="No immediate event required.",
        catalyst_assessment="Execution and guidance matter.",
        expectations_assessment=ExpectationsAssessment.PARTIALLY_PRICED_IN,
        bull_case="Upside on stronger execution.",
        bear_case="Multiple compression risk.",
        key_risks=["Valuation"],
        contradictory_evidence=["Recent rerating"],
        unknowns=["Near-term revisions"],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=0.81,
        evidence_ids=["EVID-001"],
        inference_ids=["AI-001"],
        requires_additional_research=False,
        metadata={},
    )
    data.update(overrides)
    return OpportunityResearch(**data)

def seeded_store(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    store.save_market_scan(make_scan())
    store.save_scan_candidate(make_candidate())
    return store

def test_save_get_roundtrip(tmp_path):
    store = seeded_store(tmp_path)
    research = make_research()
    store.save_opportunity_research(research)
    assert store.get_opportunity_research("RES-001") == research

def test_candidate_must_exist(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    with pytest.raises(ValueError, match="ScanCandidate not found"):
        store.save_opportunity_research(make_research())

@pytest.mark.parametrize(
    "field_name,bad_value,message",
    [
        ("scan_id", "SCAN-X", "scan_id"),
        ("ticker", "NVDA", "ticker"),
        ("portfolio_snapshot_id", "SNAP-X", "portfolio_snapshot_id"),
        ("risk_state_id", "RISK-X", "risk_state_id"),
    ],
)
def test_candidate_provenance_must_match(
    tmp_path, field_name, bad_value, message
):
    store = seeded_store(tmp_path)
    with pytest.raises(ValueError, match=message):
        store.save_opportunity_research(
            make_research(**{field_name: bad_value})
        )

def test_upsert_does_not_duplicate(tmp_path):
    store = seeded_store(tmp_path)
    store.save_opportunity_research(make_research())
    store.save_opportunity_research(
        make_research(research_confidence=0.91)
    )
    records = store.list_opportunity_research()
    assert len(records) == 1
    assert records[0].research_confidence == 0.91

def test_list_for_candidate_newest_first(tmp_path):
    store = seeded_store(tmp_path)
    store.save_opportunity_research(
        make_research(research_id="RES-OLD")
    )
    store.save_opportunity_research(
        make_research(
            research_id="RES-NEW",
            created_at=NOW + timedelta(minutes=1),
        )
    )
    records = store.list_research_for_candidate("CAND-001")
    assert [r.research_id for r in records] == ["RES-NEW", "RES-OLD"]

def test_filters(tmp_path):
    store = seeded_store(tmp_path)
    store.save_opportunity_research(make_research())
    records = store.list_opportunity_research(
        candidate_id="CAND-001",
        scan_id="SCAN-001",
        ticker="path",
        research_status=ResearchStatus.COMPLETE,
        evidence_quality=EvidenceQuality.HIGH,
        portfolio_snapshot_id="SNAP-001",
        risk_state_id="RISK-001",
        requires_additional_research=False,
    )
    assert len(records) == 1

def test_latest_research(tmp_path):
    store = seeded_store(tmp_path)
    store.save_opportunity_research(
        make_research(research_id="RES-OLD")
    )
    store.save_opportunity_research(
        make_research(
            research_id="RES-NEW",
            created_at=NOW + timedelta(minutes=2),
        )
    )
    latest = store.get_latest_opportunity_research(ticker="PATH")
    assert latest is not None
    assert latest.research_id == "RES-NEW"

def test_missing_returns_none(tmp_path):
    store = seeded_store(tmp_path)
    assert store.get_opportunity_research("RES-X") is None
    assert store.get_latest_opportunity_research() is None
