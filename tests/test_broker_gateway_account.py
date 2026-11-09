"""IB Gateway account read (Read-Only API) mapped to connector-shaped responses."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from app.broker.account_sync import AccountSyncService
from app.broker.gateway import RecordedGatewaySession, account_snapshot_from_values
from app.broker.ibkr_client import BrokerNoDataError
from app.broker.models import PortfolioMode, StateOrigin
from app.broker.sources import GATEWAY_FLOW_TYPE, GatewayAccountTransport, gateway_account_source
from app.broker.sync_store import SyncStore

T0 = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc)


def av(tag, value, currency="EUR"):
    return NS(tag=tag, value=str(value), currency=currency, account="U1")


def values():
    return [
        av("NetLiquidation", 5300.5), av("TotalCashValue", 2100.0), av("GrossPositionValue", 9800.0),
        av("InitMarginReq", 2500.0), av("MaintMarginReq", 2000.0), av("AvailableFunds", 2800.0),
        av("CashBalance", 1998.0), av("CashBalance", 119.0, "USD"), av("CashBalance", 2100.0, "BASE"),
        av("ExchangeRate", 0.86, "USD"), av("ExchangeRate", 1, "EUR"), av("NetLiquidation", 6100.0, "USD"),
    ]


def item(conid, symbol, qty, price, avg_cost):
    contract = NS(conId=conid, symbol=symbol, currency="USD", secType="STK", exchange="NASDAQ",
                  primaryExchange="", multiplier="")
    return NS(contract=contract, position=qty, marketPrice=price, marketValue=qty * price,
              averageCost=avg_cost, unrealizedPNL=qty * (price - avg_cost))


def portfolio():
    return [item(265598, "AAPL", 10, 350.0, 344.57), item(10291, "WDC", -6, 255.0, 262.33), item(1, "ZERO", 0, 1, 1)]


def trades():
    order = NS(permId=111, orderId=5, action="SELL", totalQuantity=10, orderType="STP", lmtPrice=0.0,
               auxPrice=325.0, tif="GTC")
    return [NS(order=order, contract=NS(conId=265598, symbol="AAPL"), orderStatus=NS(status="PreSubmitted"))]


DETAILS = {265598: {"isin": "US0378331005", "stock_type": "COMMON", "long_name": "APPLE INC",
                    "primary_exchange": "NASDAQ"}}


def snapshot():
    return account_snapshot_from_values(values(), portfolio(), trades(), DETAILS)


def test_summary_balances_positions_and_orders_are_mapped():
    s = snapshot()
    assert s["get_account_summary"]["currency"] == "EUR"
    assert s["get_account_summary"]["net_liquidation"] == 5300.5  # USD duplicate ignored
    assert s["get_account_summary"]["initial_margin"] == 2500.0
    bal = {b["currency"]: b for b in s["get_account_balances"]["balances"]}
    assert bal["USD"]["cash_balance"] == 119.0 and bal["USD"]["exchange_rate"] == 0.86
    assert bal["EUR"]["exchange_rate"] == 1.0 and bal["BASE"]["exchange_rate"] == 1.0
    pos = {p["contract_description"]: p for p in s["get_account_positions"]["positions"]}
    assert set(pos) == {"AAPL", "WDC"}  # zero quantity dropped
    assert pos["AAPL"]["isin"] == "US0378331005" and pos["AAPL"]["description"] == "APPLE INC"
    assert pos["AAPL"]["average_price"] == pytest.approx(344.57)
    assert pos["WDC"]["position"] == -6 and pos["WDC"]["isin"] is None
    (order,) = s["get_account_orders"]["orders"]
    assert order["side"] == "SELL" and order["stop_price"] == 325.0 and order["limit_price"] is None


def test_missing_net_liquidation_is_no_data():
    with pytest.raises(BrokerNoDataError):
        account_snapshot_from_values([av("CashBalance", 1)], [], [], {})


def test_recorded_session_replays_a_copy_of_the_account():
    session = RecordedGatewaySession({"instruments": {}, "account": snapshot()})
    first = session.account_snapshot()
    first["get_account_summary"]["net_liquidation"] = 0
    assert session.account_snapshot()["get_account_summary"]["net_liquidation"] == 5300.5
    with pytest.raises(BrokerNoDataError):
        RecordedGatewaySession({"instruments": {}}).account_snapshot()


def test_gateway_source_syncs_the_real_account(tmp_path):
    calls = []

    class Session:
        def account_snapshot(self):
            calls.append(1)
            return snapshot()

    result = AccountSyncService(gateway_account_source(Session(), clock=lambda: T0), SyncStore(tmp_path / "s.db")).sync()
    v = result.version
    assert calls == [1]  # one Gateway read per sync
    assert v.mode is PortfolioMode.LIVE and v.origin is StateOrigin.REAL_IBKR
    assert v.reading.source_detail == "ib-gateway:read-only-api"
    assert {p.symbol: p.quantity for p in v.reading.positions} == {"AAPL": 10, "WDC": -6}
    assert v.reading.metrics.net_liquidation == 5300.5
    assert len(v.reading.open_orders) == 1
    assert result.broker_orders_submitted == 0


def test_transport_answers_flow_type_without_the_gateway():
    transport = GatewayAccountTransport(NS(account_snapshot=lambda: pytest.fail("not read")))
    assert transport.call("get_flow_type", {}) == {"flow_type": GATEWAY_FLOW_TYPE}
