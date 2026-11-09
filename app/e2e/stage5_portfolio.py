"""Stage 5 portfolio analysis from an IBKR account state version (S5.0-7).

4.0 read a Fineco workbook and downloaded Yahoo history. Here the positions
come from a versioned IBKR ``StateVersion`` (live, or the TEMPORARY
simulation) and the history from IB Gateway daily bars; the 4.0 quant
engines then run unchanged and the canonical ``AccountState``,
``PortfolioSnapshot`` and ``PortfolioRiskState`` are persisted, bound to the
``ibkr://`` reference and structural fingerprint of the state version.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from app.ai.ibkr_market import IbkrHistoryLoader
from app.analysis.benchmark import calculate_benchmark_risk
from app.analysis.portfolio import (
    BENCHMARK_SYMBOL,
    RISK_MAX_SESSIONS,
    RISK_MIN_COMMON_OBSERVATIONS,
    AnalyzedPosition,
    analyze_positions,
    persist_stage3_analysis_state,
)
from app.analysis.risk import calculate_covariance_risk, calculate_historical_tail_risk, calculate_portfolio_risk
from app.broker.cio_adapter import account_state_from_version, snapshot_source_reference
from app.broker.gateway import GatewaySession
from app.broker.instrument_directory import InstrumentDirectory, InstrumentRef
from app.broker.models import StateVersion
from app.cio.models import PortfolioRiskStateRecord, PortfolioSnapshot, RiskConstraints
from app.cio.storage import Stage3Store
from app.portfolio.models import PortfolioPosition

# IBKR listings of the 4.0 benchmark/factor ETFs (checked on the connector 2026-11-06).
REFERENCE_ETFS = (
    InstrumentRef(ticker="SPY", conid=756733, isin="US78462F1030", exchange="ARCA", asset_kind="ETF", currency="USD"),
    InstrumentRef(ticker="QQQ", conid=320227571, isin="US46090E1038", exchange="NASDAQ", asset_kind="ETF", currency="USD"),
    InstrumentRef(ticker="GLD", conid=51529211, isin="US78463V1070", exchange="ARCA", asset_kind="ETF", currency="USD"),
    InstrumentRef(ticker="VGK", conid=27684070, isin="US9220428745", exchange="ARCA", asset_kind="ETF", currency="USD"),
)
RISK_HISTORY_DURATION = "4 Y"  # the 4.0 risk sample needs 504..756 common sessions
BROKER_MARKET = "IBKR"
SUPPORTED_ASSET_CLASSES = frozenset({"STK", "ETF"})


class Stage5PortfolioError(RuntimeError):
    pass


def positions_from_version(version: StateVersion) -> list[PortfolioPosition]:
    """4.0 ``PortfolioPosition`` values (EUR valued with IBKR FX) for the non-flat
    stock/ETF positions of an account state version."""

    reading = version.reading
    base = reading.metrics.base_currency
    if base != "EUR":
        raise Stage5PortfolioError(f"account base currency is {base}; the PF-1 contracts are EUR-denominated")
    rates = {b.currency: b.exchange_rate_to_base for b in reading.balances}
    positions = []
    for p in reading.positions:
        if p.quantity == 0 or p.asset_class not in SUPPORTED_ASSET_CLASSES:
            continue
        rate = 1.0 if p.currency == base else rates.get(p.currency)
        if rate is None:
            raise Stage5PortfolioError(f"no IBKR exchange rate {p.currency}->{base} for {p.symbol}")
        if p.market_price is None:
            raise Stage5PortfolioError(f"no market price for {p.symbol} ({p.conid})")
        average = p.average_price if p.average_price is not None else p.market_price
        value = p.market_value if p.market_value is not None else p.market_price * p.quantity
        cost = average * p.quantity
        pnl = value - cost
        positions.append(
            PortfolioPosition(
                name=p.description or p.symbol,
                isin=p.isin or "",
                broker_symbol=p.symbol,
                market=BROKER_MARKET,
                instrument_type=p.asset_class,
                currency=p.currency,
                quantity=p.quantity,
                average_price=average,
                load_exchange_rate=rate,
                cost_value_eur=cost * rate,
                market_price=p.market_price,
                market_exchange_rate=rate,
                market_value_eur=value * rate,
                pnl_percent=(pnl / abs(cost) * 100) if cost else 0.0,
                pnl_eur=pnl * rate,
                pnl_currency=pnl,
                yahoo_symbol=p.symbol,
                market_data_symbol=p.symbol,
                instrument_reference_id=f"IBKR:{p.conid}",
            )
        )
    return positions


def directory_from_version(version: StateVersion) -> InstrumentDirectory:
    directory = InstrumentDirectory.from_reading(version.reading)
    for ref in REFERENCE_ETFS:
        if directory.get(ref.ticker) is None:
            directory.add(ref)
    return directory


class IbkrMarketData:
    """``analyze_positions`` market-data loader over IB Gateway history; a symbol
    without permitted history is left out (4.0 reports and skips it)."""

    def __init__(self, loader: IbkrHistoryLoader) -> None:
        self._loader = loader
        self.failures: dict[str, str] = {}

    def __call__(self, symbols) -> dict[str, pd.DataFrame]:
        out = {}
        for symbol in sorted(set(symbols)):
            try:
                out[symbol] = self._loader(symbol)
            except Exception as exc:  # noqa: BLE001 - recorded, position excluded as in 4.0
                self.failures[symbol] = f"{type(exc).__name__}: {exc}"
        return out


@dataclass(frozen=True)
class Stage5PortfolioAnalysis:
    snapshot: PortfolioSnapshot
    risk_state: PortfolioRiskStateRecord
    account_state_id: str
    analyzed_positions: tuple[AnalyzedPosition, ...]
    positions: tuple[PortfolioPosition, ...]
    history_failures: dict[str, str]
    history_notes: dict[str, tuple[str, ...]]


def analyze_account_version(
    version: StateVersion,
    session: GatewaySession,
    *,
    db_path: str,
    now: datetime | None = None,
    constraints: RiskConstraints | None = None,
) -> Stage5PortfolioAnalysis:
    """``constraints``: PF-1 limits for the AccountState (Stage 5: fractions of
    the live NLV, see ``app.e2e.stage5_dry_run.NlvRiskPolicy``)."""
    now = now or datetime.now(timezone.utc)
    positions = positions_from_version(version)
    if not positions:
        raise Stage5PortfolioError("the account state has no stock/ETF position to analyze")

    store = Stage3Store(db_path)
    account_state = account_state_from_version(version, constraints)
    if store.get_account_state(account_state.account_state_id) is None:
        store.save_account_state(account_state)

    loader = IbkrHistoryLoader(session, directory_from_version(version), duration=RISK_HISTORY_DURATION)
    market_data = IbkrMarketData(loader)
    analyzed = analyze_positions(positions, market_data_loader=market_data)
    if not analyzed:
        raise Stage5PortfolioError(f"no position has usable IBKR history: {market_data.failures}")

    portfolio_risk = calculate_portfolio_risk(analyzed)
    covariance = calculate_covariance_risk(
        analyzed, min_observations=RISK_MIN_COMMON_OBSERVATIONS, max_observations=RISK_MAX_SESSIONS
    )
    benchmark = calculate_benchmark_risk(
        analyzed,
        benchmark_symbol=BENCHMARK_SYMBOL,
        benchmark_history=loader(BENCHMARK_SYMBOL),
        min_observations=RISK_MIN_COMMON_OBSERVATIONS,
        max_observations=RISK_MAX_SESSIONS,
    )
    tail_1d = calculate_historical_tail_risk(
        analyzed, confidence_levels=(0.95, 0.99), horizon_days=1,
        min_observations=RISK_MIN_COMMON_OBSERVATIONS, max_observations=RISK_MAX_SESSIONS,
    )
    snapshot, risk_state = persist_stage3_analysis_state(
        portfolio_file="",
        analyzed_positions=analyzed,
        portfolio_risk=portfolio_risk,
        covariance_risk=covariance,
        benchmark_risk=benchmark,
        historical_risk_1d=tail_1d,
        db_path=db_path,
        source_reference=snapshot_source_reference(version),
        account_state_id=account_state.account_state_id,
        now=now,
    )
    return Stage5PortfolioAnalysis(
        snapshot=snapshot,
        risk_state=risk_state,
        account_state_id=account_state.account_state_id,
        analyzed_positions=tuple(analyzed),
        positions=tuple(positions),
        history_failures=dict(market_data.failures),
        history_notes=dict(loader.notes),
    )
