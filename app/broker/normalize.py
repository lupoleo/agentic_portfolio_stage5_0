"""Turns raw IBKR connector responses into canonical broker records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .ibkr_client import BrokerResponseError
from .models import AccountMetrics, AccountProfile, BrokerPosition, CashBalance, StateOrigin


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise BrokerResponseError(f"not a number: {value!r}") from exc


def position_from_ibkr(raw: Mapping[str, Any]) -> BrokerPosition:
    try:
        conid = int(raw["contract_id"])
        symbol = str(raw.get("contract_description") or raw.get("symbol") or "").strip()
        quantity = _float(raw["position"])
    except KeyError as exc:
        raise BrokerResponseError(f"position without {exc.args[0]}") from exc
    if not symbol:
        raise BrokerResponseError(f"position {conid} has no description")
    return BrokerPosition(
        conid=conid,
        symbol=symbol,
        asset_class=str(raw.get("asset_class") or "UNKNOWN"),
        currency=str(raw.get("currency") or ""),
        quantity=quantity or 0.0,
        average_price=_float(raw.get("average_price")),
        market_price=_float(raw.get("market_price")),
        market_value=_float(raw.get("market_value")),
        unrealized_pnl=_float(raw.get("unrealized_pnl")),
        origin=StateOrigin.REAL_IBKR,
    )


def balances_from_ibkr(rows: list[Mapping[str, Any]]) -> tuple[CashBalance, ...]:
    out = []
    for row in rows:
        currency = str(row.get("currency") or "").upper()
        if not currency:
            raise BrokerResponseError("balance row without currency")
        out.append(
            CashBalance(
                currency=currency,
                cash_balance=_float(row.get("cash_balance")) or 0.0,
                settled_cash=_float(row.get("settled_cash")),
                exchange_rate_to_base=_float(row.get("exchange_rate")),
            )
        )
    return tuple(sorted(out, key=lambda b: b.currency))


def metrics_from_ibkr(summary: Mapping[str, Any]) -> AccountMetrics:
    base = str(summary.get("currency") or "").upper()
    nlv = _float(summary.get("net_liquidation"))
    if len(base) != 3 or nlv is None:
        raise BrokerResponseError("account summary without base currency or net liquidation")
    return AccountMetrics(
        base_currency=base,
        net_liquidation=nlv,
        equity_with_loan_value=_float(summary.get("equity_with_loan_value")),
        total_cash_value=_float(summary.get("total_cash_value")),
        gross_position_value=_float(summary.get("gross_position_value")),
        available_funds=_float(summary.get("available_funds")),
        buying_power=_float(summary.get("buying_power")),
        initial_margin=_float(summary.get("initial_margin")),
        maintenance_margin=_float(summary.get("maintenance_margin")),
        excess_liquidity=_float(summary.get("excess_liquidity")),
        leverage=_float(summary.get("leverage")),
    )


def profile_from(metrics: AccountMetrics, balances: tuple[CashBalance, ...], flow_type: str) -> AccountProfile:
    currencies = tuple(sorted(b.currency for b in balances if b.currency != "BASE"))
    return AccountProfile(base_currency=metrics.base_currency, flow_type=flow_type, cash_currencies=currencies)
