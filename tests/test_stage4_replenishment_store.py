from datetime import datetime, timezone

import pytest

from app.e2e.stage4_contracts import Stage4Mode
from app.e2e.stage4_replenishment import create_replenishment_session
from app.e2e.stage4_replenishment_contracts import (
    CandidateWaveRecord,
    CandidateWaveStatus,
    candidate_wave_record_fingerprint,
)
from app.e2e.stage4_replenishment_store import Stage4CandidateReplenishmentStore


NOW = datetime(2026, 9, 27, 14, 13, 22, tzinfo=timezone.utc)


def record(session_id, child_run_id="s4a-child"):
    payload = {
        "session_id": session_id,
        "wave_index": 1,
        "status": CandidateWaveStatus.PARTIAL,
        "started_at": NOW,
        "completed_at": NOW,
        "plan_fingerprint": "plan-fingerprint",
        "listing_keys": ("BIT:A2A", "XETRA:SAP"),
        "child_run_id": child_run_id,
        "terminal_reason": "UPSTREAM_PARTIAL",
    }
    fingerprint = candidate_wave_record_fingerprint(payload)
    return CandidateWaveRecord(
        record_id="wave-" + fingerprint[:24],
        fingerprint=fingerprint,
        **payload,
    )


def test_store_is_append_only_and_idempotent(tmp_path):
    store = Stage4CandidateReplenishmentStore(tmp_path / "state.db")
    session = create_replenishment_session(
        root_run_id="s4a-root",
        portfolio_snapshot_id="snapshot-1",
        portfolio_risk_state_id="risk-1",
        as_of=NOW,
        mode=Stage4Mode.LIVE,
    )
    store.save_session(session)
    store.save_session(session)
    value = record(session.session_id)
    store.save_wave(value)
    store.save_wave(value)
    assert store.get_session(session.session_id) == session
    assert store.get_wave(value.record_id) == value
    assert store.list_waves(session.session_id) == (value,)

    changed = record(session.session_id, child_run_id="s4a-other")
    object.__setattr__(changed, "record_id", value.record_id)
    with pytest.raises(ValueError, match="immutable"):
        store.save_wave(changed)


def test_wave_requires_parent_session(tmp_path):
    store = Stage4CandidateReplenishmentStore(tmp_path / "state.db")
    with pytest.raises(ValueError, match="before its session"):
        store.save_wave(record("missing-session"))
