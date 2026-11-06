"""Deterministic diff between two account readings (architecture §5A.3)."""

from __future__ import annotations

from dataclasses import dataclass

from .models import AccountReading, ChangeType, StateChange


@dataclass(frozen=True)
class DiffTolerances:
    """Thresholds below which a numeric move is not a change.

    ``account_metric_rel``: relative move of margin, buying power, available
    funds or excess liquidity. ``valuation_drift_rel``: move of total position
    market value relative to net liquidation.
    """

    cash_abs: float = 0.01
    account_metric_rel: float = 0.05
    leverage_abs: float = 0.05
    valuation_drift_rel: float = 0.02


def _fmt(value: float | None) -> str | None:
    return None if value is None else f"{value:.6g}"


def _rel_move(before: float | None, after: float | None) -> float:
    if before is None and after is None:
        return 0.0
    if before is None or after is None:
        return float("inf")
    scale = max(abs(before), abs(after), 1.0)
    return abs(after - before) / scale


def position_value_base(reading: AccountReading) -> float | None:
    """Total position market value in the base currency, or None if any
    position lacks a price or an exchange rate (never assumed)."""

    rates = {b.currency: b.exchange_rate_to_base for b in reading.balances}
    total = 0.0
    for p in reading.positions:
        if p.market_value is None:
            return None
        rate = 1.0 if p.currency == reading.metrics.base_currency else rates.get(p.currency)
        if rate is None:
            return None
        total += p.market_value * rate
    return total


def diff_readings(
    before: AccountReading,
    after: AccountReading,
    tolerances: DiffTolerances = DiffTolerances(),
) -> tuple[StateChange, ...]:
    if before.mode is not after.mode:
        raise ValueError("readings of different portfolio modes cannot be diffed")

    changes: list[StateChange] = []

    old_pos = {p.conid: p for p in before.positions}
    new_pos = {p.conid: p for p in after.positions}
    for conid in sorted(new_pos.keys() - old_pos.keys()):
        p = new_pos[conid]
        changes.append(StateChange(change_type=ChangeType.POSITION_OPENED, subject=str(conid), after=f"{p.symbol} {p.quantity:g}"))
    for conid in sorted(old_pos.keys() - new_pos.keys()):
        p = old_pos[conid]
        changes.append(StateChange(change_type=ChangeType.POSITION_CLOSED, subject=str(conid), before=f"{p.symbol} {p.quantity:g}"))
    for conid in sorted(old_pos.keys() & new_pos.keys()):
        a, b = old_pos[conid], new_pos[conid]
        if round(a.quantity, 6) != round(b.quantity, 6) or _rel_move(a.average_price, b.average_price) > 1e-6:
            changes.append(
                StateChange(
                    change_type=ChangeType.QUANTITY_CHANGED,
                    subject=str(conid),
                    before=f"{a.quantity:g} @ {_fmt(a.average_price)}",
                    after=f"{b.quantity:g} @ {_fmt(b.average_price)}",
                )
            )

    old_cash = {c.currency: c.cash_balance for c in before.balances if c.currency != "BASE"}
    new_cash = {c.currency: c.cash_balance for c in after.balances if c.currency != "BASE"}
    for currency in sorted(old_cash.keys() | new_cash.keys()):
        a, b = old_cash.get(currency), new_cash.get(currency)
        if a is None or b is None or abs(a - b) >= tolerances.cash_abs:
            changes.append(StateChange(change_type=ChangeType.CASH_CHANGED, subject=currency, before=_fmt(a), after=_fmt(b)))

    ma, mb = before.metrics, after.metrics
    for name in ("available_funds", "buying_power", "initial_margin", "maintenance_margin", "excess_liquidity"):
        a, b = getattr(ma, name), getattr(mb, name)
        if _rel_move(a, b) > tolerances.account_metric_rel:
            changes.append(StateChange(change_type=ChangeType.ACCOUNT_RISK_CHANGED, subject=name, before=_fmt(a), after=_fmt(b)))
    if (ma.leverage is None) != (mb.leverage is None) or (
        ma.leverage is not None and mb.leverage is not None and abs(ma.leverage - mb.leverage) > tolerances.leverage_abs
    ):
        changes.append(StateChange(change_type=ChangeType.ACCOUNT_RISK_CHANGED, subject="leverage", before=_fmt(ma.leverage), after=_fmt(mb.leverage)))

    if sorted(map(repr, before.open_orders)) != sorted(map(repr, after.open_orders)):
        changes.append(
            StateChange(
                change_type=ChangeType.OPEN_ORDERS_CHANGED,
                subject="ACCOUNT",
                before=str(len(before.open_orders)),
                after=str(len(after.open_orders)),
            )
        )

    if before.profile != after.profile:
        for field in type(before.profile).model_fields:
            a, b = getattr(before.profile, field), getattr(after.profile, field)
            if a != b:
                changes.append(StateChange(change_type=ChangeType.ACCOUNT_PROFILE_CHANGED, subject=field, before=str(a), after=str(b)))

    value_a = position_value_base(before)
    value_b = position_value_base(after)
    structural_kinds = {ChangeType.POSITION_OPENED, ChangeType.POSITION_CLOSED, ChangeType.QUANTITY_CHANGED}
    if value_a is not None and value_b is not None and not any(c.change_type in structural_kinds for c in changes):
        nlv = max(abs(ma.net_liquidation), abs(mb.net_liquidation), 1.0)
        if abs(value_b - value_a) / nlv > tolerances.valuation_drift_rel:
            changes.append(StateChange(change_type=ChangeType.VALUATION_DRIFT, subject="ACCOUNT", before=_fmt(value_a), after=_fmt(value_b)))

    return tuple(changes)
