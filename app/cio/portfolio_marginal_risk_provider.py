from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pandas as pd

from app.analysis.marginal_risk import PortfolioMarginalRiskEngine
from app.analysis.portfolio import (
    BENCHMARK_SYMBOL,
    MARKET_DATA_DOWNLOAD_PERIOD,
    RISK_MAX_SESSIONS,
    RISK_MIN_COMMON_OBSERVATIONS,
)
from app.cio.models import PortfolioSnapshot, TradeOpportunity
from app.cio.portfolio_filter_service import (
    PortfolioFilterService,
    PortfolioMarginalRiskInputs,
)
from app.cio.storage import Stage3Store
from app.cio.portfolio_fit_scoring import PortfolioFitScoringEngine
from app.market_data.yahoo_provider import (
    download_price_history,
    get_price_history,
)
from app.portfolio.fineco_importer import load_fineco_positions
from app.portfolio.ticker_resolver import resolve_yahoo_symbol


@dataclass(frozen=True)
class MarginalRiskPortfolioPosition:
    """
    Minimal analyzed-position adapter consumed by Stage 2.x risk engines.

    The marginal-risk engines require only:
      - yahoo_symbol
      - position.market_value_eur
      - position.direction
      - history

    Technical indicators and scoring are intentionally not recomputed here.
    """

    position: object
    yahoo_symbol: str
    history: pd.DataFrame


def _normalize_history_as_of(
    history: pd.DataFrame,
    *,
    as_of,
    symbol: str,
) -> pd.DataFrame:
    if history is None or history.empty:
        raise ValueError(f"No market history available for {symbol}")
    if "Close" not in history.columns:
        raise ValueError(f"Market history for {symbol} has no Close column")

    result = history.copy()

    index = pd.to_datetime(result.index)
    try:
        index = index.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    result.index = index

    cutoff = pd.Timestamp(as_of)
    try:
        cutoff = cutoff.tz_localize(None)
    except (TypeError, AttributeError):
        pass

    result = result.loc[result.index <= cutoff].copy()
    result = result.dropna(subset=["Close"], how="all")

    if result.empty:
        raise ValueError(
            f"No market history for {symbol} at or before snapshot timestamp"
        )

    return result


class CanonicalPortfolioMarginalRiskInputProvider:
    """
    Build real PF-1C marginal-risk inputs from the exact PortfolioSnapshot.

    Portfolio composition
    ---------------------
    The snapshot's source_file is re-read through the canonical Fineco
    importer and canonical ticker resolver. This preserves current Stage 2.x
    portfolio exposure semantics.

    Market data
    -----------
    Yahoo Finance remains the canonical Stage 2.x market-data provider.
    Histories are downloaded using the same 5y window as Portfolio Analysis
    and are then cut at PortfolioSnapshot.timestamp. This prevents look-ahead
    when an older snapshot is assessed later.

    Snapshot file hash
    ------------------
    This provider deliberately does not require the current workbook hash to
    equal PortfolioSnapshot.source_file_hash. Portfolio Analysis hashes the
    workbook before report sheets are rewritten; the report export can
    therefore legitimately change the workbook bytes after the snapshot was
    persisted without changing the underlying Fineco position sheet.

    Candidate symbol
    ----------------
    TradeOpportunity.ticker is treated as the canonical Yahoo risk-factor
    symbol by default. A resolver callback may be injected when a discovery
    source uses a different ticker namespace.
    """

    def __init__(
        self,
        *,
        candidate_symbol_resolver: Callable[[TradeOpportunity], str] | None = None,
        benchmark_symbol: str = BENCHMARK_SYMBOL,
        market_data_period: str = MARKET_DATA_DOWNLOAD_PERIOD,
    ) -> None:
        self.candidate_symbol_resolver = (
            candidate_symbol_resolver or self._default_candidate_symbol
        )
        self.benchmark_symbol = benchmark_symbol
        self.market_data_period = market_data_period

        # In-process cache is safe because entries are keyed by immutable
        # snapshot identity and sliced to the snapshot timestamp.
        self._portfolio_cache: dict[str, tuple[MarginalRiskPortfolioPosition, ...]] = {}
        self._benchmark_cache: dict[tuple[str, str], pd.DataFrame] = {}

    def __call__(
        self,
        opportunity: TradeOpportunity,
        snapshot: PortfolioSnapshot,
    ) -> PortfolioMarginalRiskInputs:
        if opportunity.snapshot_id != snapshot.snapshot_id:
            raise ValueError(
                "TradeOpportunity snapshot_id does not match PortfolioSnapshot"
            )

        candidate_symbol = self.candidate_symbol_resolver(opportunity).strip()
        if not candidate_symbol:
            raise ValueError("Candidate Yahoo symbol is empty")

        analyzed_positions = list(
            self._load_snapshot_positions(snapshot)
        )

        # Reuse an already downloaded portfolio history when the candidate is
        # itself an existing portfolio risk factor.
        candidate_history = next(
            (
                item.history
                for item in analyzed_positions
                if item.yahoo_symbol == candidate_symbol
            ),
            None,
        )

        if candidate_history is None:
            raw_candidate = get_price_history(
                candidate_symbol,
                period=self.market_data_period,
            )
            candidate_history = _normalize_history_as_of(
                raw_candidate,
                as_of=snapshot.timestamp,
                symbol=candidate_symbol,
            )

        benchmark_history = self._load_benchmark_history(snapshot)

        return PortfolioMarginalRiskInputs(
            analyzed_positions=analyzed_positions,
            candidate_symbol=candidate_symbol,
            candidate_history=candidate_history,
            benchmark_history=benchmark_history,
        )

    def _load_snapshot_positions(
        self,
        snapshot: PortfolioSnapshot,
    ) -> tuple[MarginalRiskPortfolioPosition, ...]:
        cached = self._portfolio_cache.get(snapshot.snapshot_id)
        if cached is not None:
            return cached

        source_path = Path(snapshot.source_file)
        if not source_path.exists():
            raise FileNotFoundError(
                f"PortfolioSnapshot source file does not exist: {source_path}"
            )

        positions = load_fineco_positions(source_path)
        if not positions:
            raise ValueError(
                f"No Fineco positions found in snapshot source file: {source_path}"
            )

        resolved: list[tuple[object, str]] = []
        for position in positions:
            if position.direction == "FLAT":
                continue

            yahoo_symbol = resolve_yahoo_symbol(position)
            if yahoo_symbol is None:
                continue

            resolved.append((position, yahoo_symbol))

        if not resolved:
            raise ValueError(
                "No non-flat Fineco position could be resolved to Yahoo"
            )

        histories = download_price_history(
            [symbol for _, symbol in resolved],
            period=self.market_data_period,
        )

        result: list[MarginalRiskPortfolioPosition] = []

        for position, symbol in resolved:
            raw_history = histories.get(symbol)
            if raw_history is None or raw_history.empty:
                # Preserve Stage 2.x analytical-coverage semantics:
                # positions without usable history remain economically present
                # in the source portfolio, but cannot be represented by the
                # marginal engine adapter. The risk engines will reflect
                # insufficient history for included factors; fully missing
                # Yahoo factors are omitted here exactly as Portfolio Analysis
                # omits positions without market data.
                continue

            history = _normalize_history_as_of(
                raw_history,
                as_of=snapshot.timestamp,
                symbol=symbol,
            )

            result.append(
                MarginalRiskPortfolioPosition(
                    position=position,
                    yahoo_symbol=symbol,
                    history=history,
                )
            )

        if not result:
            raise ValueError(
                "No snapshot position has usable canonical market history"
            )

        frozen = tuple(result)
        self._portfolio_cache[snapshot.snapshot_id] = frozen
        return frozen

    def _load_benchmark_history(
        self,
        snapshot: PortfolioSnapshot,
    ) -> pd.DataFrame:
        key = (
            self.benchmark_symbol,
            snapshot.timestamp.isoformat(),
        )
        cached = self._benchmark_cache.get(key)
        if cached is not None:
            return cached.copy()

        raw = get_price_history(
            self.benchmark_symbol,
            period=self.market_data_period,
        )
        history = _normalize_history_as_of(
            raw,
            as_of=snapshot.timestamp,
            symbol=self.benchmark_symbol,
        )

        self._benchmark_cache[key] = history.copy()
        return history

    @staticmethod
    def _default_candidate_symbol(
        opportunity: TradeOpportunity,
    ) -> str:
        return opportunity.ticker.upper()


def build_canonical_portfolio_filter_service(
    store: Stage3Store,
    *,
    candidate_symbol_resolver: Callable[[TradeOpportunity], str] | None = None,
) -> PortfolioFilterService:
    """
    Production composition root for PF-1C.

    Existing tests and callers may still instantiate PortfolioFilterService
    directly and retain fail-closed UNKNOWN marginal-risk semantics.

    Live/operational callers should use this factory so the shared marginal
    risk engine is wired to the canonical Fineco/Yahoo input provider.
    """

    provider = CanonicalPortfolioMarginalRiskInputProvider(
        candidate_symbol_resolver=candidate_symbol_resolver,
    )

    engine = PortfolioMarginalRiskEngine(
        benchmark_symbol=BENCHMARK_SYMBOL,
        min_observations=RISK_MIN_COMMON_OBSERVATIONS,
        max_observations=RISK_MAX_SESSIONS,
    )

    scoring_engine = PortfolioFitScoringEngine()

    return PortfolioFilterService(
        store,
        marginal_risk_engine=engine,
        marginal_risk_input_provider=provider,
        scoring_engine=scoring_engine,
    )
