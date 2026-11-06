"""Bridges a broker ``StateVersion`` to the frozen Stage 3/4 CIO contracts.

The 4.0 ``PortfolioSnapshot`` pointed at a Fineco file and its SHA-256. In 5.0
there is no file: ``source_file`` becomes a stable ``ibkr://`` reference to the
state version and ``source_file_hash`` its structural fingerprint, so every
downstream record stays bound to an exact, immutable account state.
"""

from __future__ import annotations

from app.cio.models import AccountState, Currency, CurrencyCash, DataSource

from .models import StateOrigin, StateVersion


def snapshot_source_reference(version: StateVersion) -> tuple[str, str]:
    return f"ibkr://{version.mode.value}/{version.version_id}", version.structural_fingerprint


def account_state_from_version(version: StateVersion) -> AccountState:
    reading = version.reading
    supported = {c.value for c in Currency}
    cash: list[CurrencyCash] = []
    notes: list[str] = [reading.source_detail]
    for balance in reading.balances:
        if balance.currency == "BASE":
            continue
        if balance.currency not in supported:
            notes.append(f"cash in unsupported currency {balance.currency} not represented")
            continue
        if balance.cash_balance < 0:
            notes.append(f"negative {balance.currency} cash {balance.cash_balance:.2f} shown as 0 available")
        cash.append(CurrencyCash(currency=Currency(balance.currency), available=max(balance.cash_balance, 0.0)))

    metrics = reading.metrics
    equity_eur = metrics.net_liquidation if metrics.base_currency == "EUR" and metrics.net_liquidation > 0 else None
    if equity_eur is None:
        notes.append(f"account equity not in EUR (base {metrics.base_currency}); left unset")

    return AccountState(
        account_state_id=f"AS-{version.version_id}",
        timestamp=version.created_at,
        cash=cash,
        account_equity_eur=equity_eur,
        source=DataSource.IBKR if version.origin is StateOrigin.REAL_IBKR else DataSource.IBKR_SIMULATED,
        notes="; ".join(notes),
    )
