"""Stage 5 selected-opportunity dry run on the IBKR account (S5.0-7, part B).

4.0 fed the S2.2G dry run from the Fineco workbook (portfolio exposure for
marginal risk), Yahoo history, an operator-maintained Fineco instrument cache
and hand-typed market inputs. Stage 5 replaces each with IBKR data, read-only:

* marginal-risk inputs: the positions of the IBKR account state version the
  snapshot is bound to, with IB Gateway daily history cut at the snapshot time;
* the implementing instrument: the IBKR listing itself (contract details);
* market inputs: last IB Gateway close, the account's IBKR FX rate and an
  ATR-based protective stop, each overridable by the operator;
* PF-1 limits (G7): fractions of the live net liquidation value.

The 4.0 policy services (Portfolio Filter, lifecycle gate, selector, sizer,
simulator, CIO decision, ExecutionPlan) run unchanged. Nothing is ever sent
to the broker: the ExecutionPlan stays a non-authorized dry run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone

import pandas as pd

from app.analysis.portfolio import BENCHMARK_SYMBOL
from app.broker.gateway import ContractInfo, GatewaySession
from app.broker.instrument_directory import InstrumentDirectory
from app.broker.models import StateVersion
from app.cio.models import (
    CacheStatus,
    Currency,
    DataSource,
    Direction,
    ExposureRelationship,
    FinecoInstrument,
    InstrumentType,
    PortfolioSnapshot,
    RiskConstraints,
    TradingMode,
)
from app.cio.portfolio_filter_service import PortfolioMarginalRiskInputs
from app.cio.portfolio_marginal_risk_provider import MarginalRiskPortfolioPosition, _normalize_history_as_of

ATR_SESSIONS = 14
DEFAULT_STOP_ATR_MULTIPLE = 2.0


class Stage5DryRunError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# PF-1 limits as fractions of the live NLV (G7)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NlvRiskPolicy:
    """PF-1 limits expressed against the live net liquidation value, so they
    follow the account as it grows (STAGE_5_0_ARCHITECTURE §7, G7)."""

    max_trade_loss_pct: float = 1.5
    max_position_weight_pct: float = 20.0
    max_portfolio_gross_exposure_pct: float = 150.0
    max_cio_deployable_pct: float = 25.0
    max_var_95_1d_pct: float = 3.0
    min_cash_reserve_pct: float = 5.0

    def constraints(self, nlv_eur: float) -> RiskConstraints:
        if nlv_eur <= 0:
            raise Stage5DryRunError("PF-1 limits need a positive EUR net liquidation value")
        return RiskConstraints(
            max_trade_loss_eur=round(nlv_eur * self.max_trade_loss_pct / 100, 2),
            max_position_weight_pct=self.max_position_weight_pct,
            max_portfolio_gross_exposure_pct=self.max_portfolio_gross_exposure_pct,
            max_cio_deployable_pct=self.max_cio_deployable_pct,
            max_var_95_1d_eur=round(nlv_eur * self.max_var_95_1d_pct / 100, 2),
            min_cash_reserve_eur=round(nlv_eur * self.min_cash_reserve_pct / 100, 2),
        )


# --------------------------------------------------------------------------
# Marginal-risk inputs from the IBKR account state version
# --------------------------------------------------------------------------


class IbkrMarginalRiskInputProvider:
    """PF-1C / Portfolio Simulator inputs without the Fineco workbook.

    ``positions`` are the 4.0 ``PortfolioPosition`` values of the account state
    version the snapshot is bound to; ``history`` loads IB Gateway daily bars
    by ticker (an ``IbkrHistoryLoader`` over the run's instrument directory).
    Called with a TradeOpportunity (filter) or a TradeProposal (simulator):
    both carry ``ticker`` and ``snapshot_id``.
    """

    def __init__(self, positions, history, *, snapshot_reference: str, benchmark_symbol: str = BENCHMARK_SYMBOL):
        self._positions = tuple(positions)
        self._history = history
        self._reference = snapshot_reference
        self._benchmark = benchmark_symbol
        self._cache: dict[str, tuple[MarginalRiskPortfolioPosition, ...]] = {}
        self.failures: dict[str, str] = {}

    def __call__(self, item, snapshot: PortfolioSnapshot) -> PortfolioMarginalRiskInputs:
        if item.snapshot_id != snapshot.snapshot_id:
            raise ValueError("opportunity/proposal snapshot_id does not match PortfolioSnapshot")
        if snapshot.source_file != self._reference:
            raise ValueError(
                f"PortfolioSnapshot is bound to {snapshot.source_file}, not to the account state {self._reference}"
            )
        positions = self._snapshot_positions(snapshot)
        symbol = item.ticker.strip().upper()
        candidate = next((p.history for p in positions if p.yahoo_symbol == symbol), None)
        if candidate is None:
            candidate = self._cut(symbol, snapshot)
        return PortfolioMarginalRiskInputs(
            analyzed_positions=list(positions),
            candidate_symbol=symbol,
            candidate_history=candidate,
            benchmark_history=self._cut(self._benchmark, snapshot),
        )

    def _cut(self, symbol: str, snapshot: PortfolioSnapshot) -> pd.DataFrame:
        return _normalize_history_as_of(self._history(symbol), as_of=snapshot.timestamp, symbol=symbol)

    def _snapshot_positions(self, snapshot):
        cached = self._cache.get(snapshot.snapshot_id)
        if cached is not None:
            return cached
        result = []
        for position in self._positions:
            if position.direction == "FLAT":
                continue
            symbol = position.yahoo_symbol
            try:
                history = self._cut(symbol, snapshot)
            except Exception as exc:  # noqa: BLE001 - omitted as 4.0 omits positions without history
                self.failures[symbol] = f"{type(exc).__name__}: {exc}"
                continue
            result.append(MarginalRiskPortfolioPosition(position=position, yahoo_symbol=symbol, history=history))
        if not result:
            raise ValueError(f"no account position has usable IBKR history: {self.failures}")
        frozen = tuple(result)
        self._cache[snapshot.snapshot_id] = frozen
        return frozen


# --------------------------------------------------------------------------
# The implementing instrument: the IBKR listing
# --------------------------------------------------------------------------


def ibkr_instrument(ticker: str, info: ContractInfo, *, as_of: datetime,
                    short_available: bool | None = None, funding_currency: str | None = None) -> FinecoInstrument:
    """The IBKR listing as a cash instrument record for the 4.0 selector/sizer.

    Shortability (borrow) is not read from IBKR yet, so ``short_available``
    stays unknown by default and SHORT opportunities fail closed.

    ``funding_currency``: the cash the sizer funds the trade from. 4.0 (Fineco)
    funded from cash in the trading currency. On IBKR a foreign-currency trade
    can be funded from base-currency cash (IBKR converts or borrows the
    currency), so Stage 5 passes the account base currency; None keeps the 4.0
    trading-currency rule.
    """

    try:
        currency = Currency(info.currency)
    except ValueError as exc:
        raise Stage5DryRunError(f"{ticker}: currency {info.currency} not supported by the CIO contracts") from exc
    is_etf = (info.stock_type or "").upper() == "ETF"
    return FinecoInstrument(
        instrument_id=f"IBKR:{info.conid}",
        underlying=ticker.upper(),
        reference_underlying=ticker.upper(),
        exposure_relationship=ExposureRelationship.DIRECT,
        description=info.long_name or ticker,
        instrument_type=InstrumentType.ETF if is_etf else InstrumentType.ORDINARY,
        trading_mode=TradingMode.ORDINARY,
        market=info.primary_exchange or info.exchange,
        quote_currency=currency,
        settlement_currency=currency,
        margin_currency=Currency(funding_currency) if funding_currency else None,
        long_available=True,
        short_available=short_available,
        intraday_available=True,
        overnight_available=True,
        embedded_leverage=1.0,
        source=DataSource.IBKR,
        cache_status=CacheStatus.CURRENT,
        last_confirmed=as_of,
        notes=(f"IBKR contract details conid={info.conid} ({info.sec_type} {info.stock_type or ''}); "
               f"funded from {funding_currency or info.currency} cash"),
    )


# --------------------------------------------------------------------------
# Market inputs from IB Gateway
# --------------------------------------------------------------------------


def average_true_range(history: pd.DataFrame, sessions: int = ATR_SESSIONS) -> float:
    if len(history) < sessions + 1:
        raise Stage5DryRunError(f"ATR needs {sessions + 1} sessions, got {len(history)}")
    high, low, close = history["High"], history["Low"], history["Close"]
    previous = close.shift(1)
    true_range = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1)
    return float(true_range.tail(sessions).mean())


@dataclass(frozen=True)
class MarketInputs:
    reference_price: float
    fx_to_eur: float
    stop_price: float
    market_observed_at: datetime
    atr: float
    source: str


def fx_to_eur(version: StateVersion, currency: str) -> float:
    reading = version.reading
    if reading.metrics.base_currency != "EUR":
        raise Stage5DryRunError(f"account base currency is {reading.metrics.base_currency}, not EUR")
    if currency == "EUR":
        return 1.0
    for balance in reading.balances:
        if balance.currency == currency and balance.exchange_rate_to_base:
            return float(balance.exchange_rate_to_base)
    raise Stage5DryRunError(f"no IBKR exchange rate {currency}->EUR in the account state")


def market_inputs(history: pd.DataFrame, *, direction: Direction, fx: float, now: datetime,
                  stop_atr_multiple: float = DEFAULT_STOP_ATR_MULTIPLE) -> MarketInputs:
    """Reference price = last daily close; stop = close -/+ multiple x ATR(14)."""

    if history is None or history.empty:
        raise Stage5DryRunError("no IB Gateway history for the selected instrument")
    close = float(history["Close"].iloc[-1])
    atr = average_true_range(history)
    distance = stop_atr_multiple * atr
    stop = close - distance if direction is Direction.LONG else close + distance
    if stop <= 0:
        raise Stage5DryRunError(f"ATR stop {stop:.4f} is not positive")
    day = pd.Timestamp(history.index[-1]).date()
    observed = min(datetime.combine(day, time(23, 59), tzinfo=timezone.utc), now)
    return MarketInputs(
        reference_price=round(close, 4),
        fx_to_eur=fx,
        stop_price=round(stop, 4),
        market_observed_at=observed,
        atr=round(atr, 4),
        source=f"IB Gateway daily close {day.isoformat()}; stop {stop_atr_multiple:g}xATR{ATR_SESSIONS}",
    )


def default_exposure_eur(nlv_eur: float, policy: NlvRiskPolicy) -> float:
    """Requested exposure when the operator gives none: half the PF-1 position
    weight cap, so the position-weight check has headroom."""

    return round(nlv_eur * policy.max_position_weight_pct / 200, 2)


def resolve_contract(session: GatewaySession, directory: InstrumentDirectory, ticker: str) -> ContractInfo:
    ref = directory.get(ticker)
    if ref is None:
        raise Stage5DryRunError(f"{ticker} is not in the run's instrument directory")
    return session.contract_info(ref.conid)
