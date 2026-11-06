"""Acceptance on real recorded IBKR readings and the TEMPORARY Fineco-USD
simulation. Both inputs are personal data kept under git-ignored ``data/``;
the test is skipped where they are absent (CI, fresh clones)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.broker.account_sync import AccountSyncService
from app.broker.ibkr_client import READ_ONLY_TOOLS, ReadOnlyIbkrClient
from app.broker.models import ChangeType, PortfolioMode, StateOrigin, SyncOutcome
from app.broker.recordings import load_account_recording
from app.broker.sources import LiveIbkrAccountSource, SimulatedFinecoUsdSource, SimulationFixture
from app.broker.sync_store import SyncStore

ROOT = Path(__file__).resolve().parents[1]
RECORDING = ROOT / "data/recordings/ibkr_account_2026-11.json"
FIXTURE = ROOT / "data/simulation/fineco_usd_2026-11-06.json"

pytestmark = pytest.mark.skipif(
    not (RECORDING.exists() and FIXTURE.exists()), reason="personal recordings/simulation not present"
)


def run(tmp_path, simulated: bool):
    transport, times = load_account_recording(RECORDING)
    clock = iter(times)
    client = ReadOnlyIbkrClient(transport)
    if simulated:
        source = SimulatedFinecoUsdSource(client, SimulationFixture.load(FIXTURE), clock=lambda: next(clock))
    else:
        source = LiveIbkrAccountSource(client, clock=lambda: next(clock))
    service = AccountSyncService(source, SyncStore(tmp_path / "sync.db"))
    return [service.sync() for _ in times], client


def test_real_account_sale_of_dhr_is_detected(tmp_path):
    results, client = run(tmp_path, simulated=False)
    assert [r.outcome for r in results] == [SyncOutcome.INITIAL, SyncOutcome.CHANGED]
    changes = {(c.change_type, c.subject) for c in results[1].changes}
    assert (ChangeType.POSITION_CLOSED, "6442") in changes
    assert (ChangeType.CASH_CHANGED, "USD") in changes
    assert results[1].version.reading.positions == ()
    assert set(client.calls) <= READ_ONLY_TOOLS


def test_simulated_fineco_usd_portfolio(tmp_path):
    results, _ = run(tmp_path, simulated=True)
    v = results[-1].version
    fixture = SimulationFixture.load(FIXTURE)
    assert v.mode is PortfolioMode.SIMULATED_FINECO_USD
    assert {p.symbol for p in v.reading.positions} == {
        "TSLA", "AMZN", "MSFT", "NVDA", "SPCX", "NBIS", "META", "GOOGL", "PLTR", "MSTR", "PFE", "DELL"
    }
    assert all(p.origin is StateOrigin.SIMULATED_TEMPORARY for p in v.reading.positions)
    usd_rate = {b.currency: b.exchange_rate_to_base for b in v.reading.balances}["USD"]
    simulated = sum(h.quantity * h.market_price_at_export for h in fixture.holdings) * usd_rate
    assert v.reading.metrics.net_liquidation == pytest.approx(5456.91 + simulated)
    assert all(r.broker_orders_submitted == 0 for r in results)
