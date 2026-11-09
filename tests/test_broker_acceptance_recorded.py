"""Acceptance on real recorded IBKR readings. The recording is personal data
kept under git-ignored ``data/``; the test is skipped where it is absent (CI,
fresh clones)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.broker.account_sync import AccountSyncService
from app.broker.ibkr_client import READ_ONLY_TOOLS, ReadOnlyIbkrClient
from app.broker.models import ChangeType, SyncOutcome
from app.broker.recordings import load_account_recording
from app.broker.sources import LiveIbkrAccountSource
from app.broker.sync_store import SyncStore

ROOT = Path(__file__).resolve().parents[1]
RECORDING = ROOT / "data/recordings/ibkr_account_2026-11.json"

pytestmark = pytest.mark.skipif(
    not RECORDING.exists(), reason="personal recording not present"
)


def run(tmp_path):
    transport, times = load_account_recording(RECORDING)
    clock = iter(times)
    client = ReadOnlyIbkrClient(transport)
    source = LiveIbkrAccountSource(client, clock=lambda: next(clock))
    service = AccountSyncService(source, SyncStore(tmp_path / "sync.db"))
    return [service.sync() for _ in times], client


def test_real_account_sale_of_dhr_is_detected(tmp_path):
    results, client = run(tmp_path)
    assert [r.outcome for r in results] == [SyncOutcome.INITIAL, SyncOutcome.CHANGED]
    changes = {(c.change_type, c.subject) for c in results[1].changes}
    assert (ChangeType.POSITION_CLOSED, "6442") in changes
    assert (ChangeType.CASH_CHANGED, "USD") in changes
    assert results[1].version.reading.positions == ()
    assert set(client.calls) <= READ_ONLY_TOOLS
    assert all(r.broker_orders_submitted == 0 for r in results)
