from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from app.broker.account_sync import AccountSyncService
from app.broker.cio_adapter import account_state_from_version, snapshot_source_reference
from app.broker.ibkr_client import (
    KNOWN_WRITE_TOOLS,
    READ_ONLY_TOOLS,
    ForbiddenBrokerToolError,
    ReadOnlyIbkrClient,
    RecordedTransport,
)
from app.broker.models import ChangeType, PortfolioMode, StateOrigin, SyncOutcome
from app.broker.sources import (
    LiveIbkrAccountSource,
)
from app.broker.sync_store import SyncStore
from app.cio.models import DataSource

T0 = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)


def summary(nlv=10_000.0, cash=8_000.0, gross=2_000.0, margin=500.0):
    return {
        "currency": "EUR",
        "net_liquidation": nlv,
        "equity_with_loan_value": nlv,
        "buying_power": nlv * 4,
        "gross_position_value": gross,
        "total_cash_value": cash,
        "available_funds": nlv - margin,
        "initial_margin": margin,
        "maintenance_margin": margin * 0.9,
        "excess_liquidity": nlv - margin * 0.9,
        "leverage": f"{gross / nlv:.2f}",
    }


def balances(eur=5_000.0, usd=3_000.0, usd_rate=0.9):
    return {
        "balances": [
            {"currency": "BASE", "cash_balance": eur + usd * usd_rate, "exchange_rate": 1},
            {"currency": "EUR", "cash_balance": eur, "exchange_rate": 1},
            {"currency": "USD", "cash_balance": usd, "exchange_rate": usd_rate},
        ]
    }


def position(conid=1001, symbol="AAA", qty=10, price=200.0, avg=180.0):
    return {
        "contract_id": conid,
        "contract_description": symbol,
        "position": qty,
        "market_price": price,
        "market_value": qty * price,
        "currency": "USD",
        "average_price": avg,
        "unrealized_pnl": (price - avg) * qty,
        "asset_class": "STK",
    }


def reading(positions=None, summ=None, bal=None, flow="REVIEW_AND_APPROVE", orders=None):
    return {
        "get_account_summary": summ or summary(),
        "get_account_balances": bal or balances(),
        "get_account_positions": {"positions": [position()] if positions is None else positions},
        "get_flow_type": {"flow_type": flow},
        "get_account_orders": {"orders": orders or []},
    }


def transport_for(*readings):
    tools = readings[0].keys()
    return RecordedTransport({tool: [r[tool] for r in readings] for tool in tools})


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        value = self.now
        self.now += timedelta(minutes=15)
        return value


def live_service(tmp_path, *readings):
    client = ReadOnlyIbkrClient(transport_for(*readings))
    store = SyncStore(tmp_path / "sync.db")
    return AccountSyncService(LiveIbkrAccountSource(client, clock=Clock()), store), store, client


# --- read-only allowlist ---------------------------------------------------------------


def test_allowlist_contains_no_write_tool():
    assert READ_ONLY_TOOLS.isdisjoint(KNOWN_WRITE_TOOLS)
    assert not any(t.startswith(("create_", "delete_", "update_", "edit_", "submit_", "cancel_", "modify_", "set_")) for t in READ_ONLY_TOOLS)


@pytest.mark.parametrize("tool", sorted(KNOWN_WRITE_TOOLS))
def test_write_tools_are_refused_before_the_transport(tool):
    class Exploding:
        def call(self, tool, arguments):
            raise AssertionError("transport must not be reached")

    client = ReadOnlyIbkrClient(Exploding())
    with pytest.raises(ForbiddenBrokerToolError):
        client.call(tool, {})
    assert sum(client.calls.values()) == 0


# --- live sync ---------------------------------------------------------------------------


def test_first_sync_creates_initial_version(tmp_path):
    service, store, client = live_service(tmp_path, reading())
    result = service.sync()
    assert result.outcome is SyncOutcome.INITIAL
    assert result.version.parent_version_id is None
    assert result.version.origin is StateOrigin.REAL_IBKR
    assert [p.conid for p in result.version.reading.positions] == [1001]
    assert (result.broker_orders_submitted, result.portfolio_mutations, result.automatic_executions) == (0, 0, 0)
    assert store.latest_version(PortfolioMode.LIVE) == result.version
    assert set(client.calls) <= READ_ONLY_TOOLS


def test_identical_reading_records_no_change_without_new_version(tmp_path):
    service, store, _ = live_service(tmp_path, reading(), reading())
    first = service.sync()
    second = service.sync()
    assert second.outcome is SyncOutcome.NO_CHANGE
    assert second.version.version_id == first.version.version_id
    runs = store.sync_runs(PortfolioMode.LIVE)
    assert [r["outcome"] for r in runs] == ["INITIAL", "NO_CHANGE"]


def test_sale_is_detected_as_position_closed_and_cash_changed(tmp_path):
    after = reading(positions=[], summ=summary(nlv=9_900, cash=9_900, gross=0, margin=0), bal=balances(usd=5_000))
    service, store, _ = live_service(tmp_path, reading(), after)
    first = service.sync()
    second = service.sync()
    kinds = {c.change_type for c in second.changes}
    assert second.outcome is SyncOutcome.CHANGED
    assert {ChangeType.POSITION_CLOSED, ChangeType.CASH_CHANGED, ChangeType.ACCOUNT_RISK_CHANGED} <= kinds
    assert ChangeType.VALUATION_DRIFT not in kinds
    assert second.version.parent_version_id == first.version.version_id
    assert store.get_version(first.version.version_id).reading.positions  # old version intact


def test_buy_and_partial_sale(tmp_path):
    two = reading(positions=[position(), position(conid=2002, symbol="BBB", qty=5)])
    partial = reading(positions=[position(qty=4), position(conid=2002, symbol="BBB", qty=5)])
    service, _, _ = live_service(tmp_path, reading(), two, partial)
    service.sync()
    opened = service.sync()
    assert [(c.change_type, c.subject) for c in opened.changes] == [(ChangeType.POSITION_OPENED, "2002")]
    reduced = service.sync()
    assert [(c.change_type, c.subject) for c in reduced.changes] == [(ChangeType.QUANTITY_CHANGED, "1001")]


def test_small_price_moves_are_not_changes_large_ones_are_valuation_drift(tmp_path):
    small = reading(positions=[position(price=201.0)])
    large = reading(positions=[position(price=260.0)])
    service, _, _ = live_service(tmp_path, reading(), small, large)
    service.sync()
    assert service.sync().outcome is SyncOutcome.NO_CHANGE
    drift = service.sync()
    assert drift.outcome is SyncOutcome.VALUATION_ONLY
    assert [c.change_type for c in drift.changes] == [ChangeType.VALUATION_DRIFT]


def test_flow_type_change_is_an_account_profile_change(tmp_path):
    service, _, _ = live_service(tmp_path, reading(), reading(flow="AUTONOMOUS"))
    service.sync()
    result = service.sync()
    assert [(c.change_type, c.subject) for c in result.changes] == [(ChangeType.ACCOUNT_PROFILE_CHANGED, "flow_type")]


def test_open_order_placed_in_tws_is_detected(tmp_path):
    service, _, _ = live_service(tmp_path, reading(), reading(orders=[{"order_id": 1, "symbol": "AAA", "side": "BUY"}]))
    service.sync()
    assert [c.change_type for c in service.sync().changes] == [ChangeType.OPEN_ORDERS_CHANGED]


def test_store_is_append_only(tmp_path):
    service, _, _ = live_service(tmp_path, reading())
    service.sync()
    con = sqlite3.connect(tmp_path / "sync.db")
    for sql in ("UPDATE state_version SET mode = 'X'", "DELETE FROM state_version", "DELETE FROM sync_run"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute(sql)


# --- CIO contract bridge -------------------------------------------------------------------


def test_cio_adapter_marks_real_state(tmp_path):
    service, _, _ = live_service(tmp_path, reading())
    assert account_state_from_version(service.sync().version).source is DataSource.IBKR


def test_replaying_the_same_recorded_reading_is_a_new_sync_run(tmp_path):
    from app.broker.account_sync import AccountSyncService
    from app.broker.ibkr_client import ReadOnlyIbkrClient
    from app.broker.recordings import RecordedTransport
    from app.broker.sources import LiveIbkrAccountSource
    from app.broker.sync_store import SyncStore

    store = SyncStore(tmp_path / "s.db")
    results = []
    for _ in range(2):  # the same recording replayed by two tool runs
        transport = RecordedTransport({k: [v] for k, v in reading(positions=[position()]).items()})
        source = LiveIbkrAccountSource(ReadOnlyIbkrClient(transport), clock=lambda: T0)
        results.append(AccountSyncService(source, store).sync())
    assert results[0].sync_id != results[1].sync_id
    assert results[1].version.version_id == results[0].version.version_id
