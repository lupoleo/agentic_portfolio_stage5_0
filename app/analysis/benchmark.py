from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from app.analysis.portfolio import AnalyzedPosition


@dataclass(frozen=True)
class AssetBenchmarkMetrics:
    yahoo_symbol: str
    direction: str
    signed_weight_pct: float
    beta: float
    alpha_annualized_pct: float
    correlation: float
    r_squared: float
    beta_contribution: float


@dataclass(frozen=True)
class PortfolioBenchmarkRiskSummary:
    benchmark_symbol: str
    assets: int
    observations: int
    gross_exposure_eur: float
    risk_coverage_pct: float
    excluded_symbols: tuple[str, ...]
    portfolio_beta: float
    beta_from_components: float
    alpha_annualized_pct: float
    correlation: float
    r_squared: float
    benchmark_volatility_pct: float
    portfolio_volatility_pct: float
    systematic_volatility_pct: float
    idiosyncratic_volatility_pct: float
    asset_metrics: tuple[AssetBenchmarkMetrics, ...]


def _direction_sign(direction: str) -> int:
    direction = direction.upper()
    if direction == "LONG":
        return 1
    if direction == "SHORT":
        return -1
    return 0


def _economic_exposure_eur(item: AnalyzedPosition) -> float:
    return abs(float(item.position.market_value_eur))


def _close_series(item: AnalyzedPosition) -> pd.Series | None:
    history = item.history
    if history is None or history.empty or "Close" not in history.columns:
        return None

    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if close.empty:
        return None

    # Normalize timezone differences before joining portfolio and benchmark.
    try:
        close.index = close.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass

    return close


def _benchmark_close_series(history: pd.DataFrame) -> pd.Series:
    if history is None or history.empty or "Close" not in history.columns:
        raise ValueError("Benchmark price history is empty or has no Close column")

    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if close.empty:
        raise ValueError("Benchmark Close series is empty")

    try:
        close.index = close.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass

    return close.rename("BENCHMARK")


def calculate_benchmark_risk(
    analyzed_positions: list[AnalyzedPosition],
    benchmark_symbol: str,
    benchmark_history: pd.DataFrame,
    min_observations: int = 504,
    max_observations: int | None = 756,
    annualization_factor: int = 252,
) -> PortfolioBenchmarkRiskSummary:
    """
    Stage 2.4 benchmark-relative risk analysis.

    Current portfolio exposures are applied retrospectively to historical
    returns. Signed weights are normalized by total gross exposure:

        LONG  -> positive weight
        SHORT -> negative weight

    Portfolio beta is estimated by OLS against benchmark daily returns:

        beta = Cov(Rp, Rm) / Var(Rm)

    Alpha is the daily OLS intercept annualized linearly by 252.

    Systematic volatility is |beta| * benchmark volatility.
    Idiosyncratic volatility is the residual standard deviation from the
    one-factor regression, annualized by sqrt(252).
    """

    if min_observations < 2:
        raise ValueError("min_observations must be at least 2")
    if max_observations is not None and max_observations < min_observations:
        raise ValueError("max_observations cannot be smaller than min_observations")
    if not analyzed_positions:
        raise ValueError("No analyzed portfolio positions")

    gross_exposure = sum(
        _economic_exposure_eur(item)
        for item in analyzed_positions
        if _direction_sign(item.position.direction) != 0
    )
    if gross_exposure <= 0:
        raise ValueError("Portfolio gross exposure is zero")

    # Aggregate Fineco positions that map to the same market factor.
    exposure_by_symbol: dict[str, dict[str, float]] = {}
    close_by_symbol: dict[str, pd.Series] = {}

    for item in analyzed_positions:
        sign = _direction_sign(item.position.direction)
        if sign == 0:
            continue

        exposure = _economic_exposure_eur(item)
        symbol = item.yahoo_symbol

        bucket = exposure_by_symbol.setdefault(
            symbol, {"gross": 0.0, "signed": 0.0}
        )
        bucket["gross"] += exposure
        bucket["signed"] += exposure * sign

        if symbol not in close_by_symbol:
            close = _close_series(item)
            if close is not None:
                close_by_symbol[symbol] = close.rename(symbol)

    # Need enough prices to create min_observations daily returns.
    eligible = sorted(
        symbol
        for symbol, close in close_by_symbol.items()
        if len(close) >= min_observations + 1
    )
    excluded = sorted(set(exposure_by_symbol) - set(eligible))

    if not eligible:
        raise ValueError("No asset has enough history for benchmark analysis")

    benchmark_close = _benchmark_close_series(benchmark_history)

    def _common_returns(
        symbols: list[str],
    ) -> pd.DataFrame:
        common_prices = pd.concat(
            [close_by_symbol[s] for s in symbols] + [benchmark_close],
            axis=1,
            join="inner",
        ).dropna(how="any")

        common_returns = (
            common_prices
            .pct_change(fill_method=None)
            .dropna(how="any")
        )

        if max_observations is not None:
            common_returns = common_returns.tail(max_observations)

        return common_returns

    active_symbols = list(eligible)
    returns = _common_returns(active_symbols)

    while (
        len(returns) < min_observations
        and len(active_symbols) > 1
    ):
        current_observations = len(returns)
        candidates = []

        for symbol in active_symbols:
            trial_symbols = [
                s for s in active_symbols if s != symbol
            ]
            trial_returns = _common_returns(trial_symbols)

            candidates.append(
                (
                    len(trial_returns),
                    exposure_by_symbol[symbol]["gross"],
                    symbol,
                    trial_symbols,
                    trial_returns,
                )
            )

        best_observations = max(
            candidate[0] for candidate in candidates
        )

        best_candidates = [
            candidate
            for candidate in candidates
            if candidate[0] == best_observations
        ]

        _, _, removed_symbol, best_symbols, best_returns = min(
            best_candidates,
            key=lambda candidate: (
                candidate[1],
                candidate[2],
            ),
        )

        if best_observations <= current_observations:
            break

        excluded.append(removed_symbol)
        active_symbols = best_symbols
        returns = best_returns

    if len(returns) < min_observations:
        raise ValueError(
            "Insufficient common benchmark observations "
            f"after deterministic asset exclusion: {len(returns)} "
            f"(minimum required: {min_observations})"
        )

    symbols = active_symbols
    excluded = sorted(set(excluded))

    covered_gross = sum(
        exposure_by_symbol[s]["gross"]
        for s in symbols
    )
    coverage_pct = covered_gross / gross_exposure * 100.0
    benchmark_returns = returns["BENCHMARK"].to_numpy(dtype=float)
    asset_returns = returns[symbols]

    weights = np.array(
        [exposure_by_symbol[s]["signed"] / gross_exposure for s in symbols],
        dtype=float,
    )
    portfolio_returns = asset_returns.to_numpy(dtype=float) @ weights

    benchmark_variance = float(np.var(benchmark_returns, ddof=1))
    if benchmark_variance <= 0:
        raise ValueError("Benchmark return variance is zero")

    portfolio_covariance = float(
        np.cov(portfolio_returns, benchmark_returns, ddof=1)[0, 1]
    )
    portfolio_beta = portfolio_covariance / benchmark_variance

    portfolio_mean = float(np.mean(portfolio_returns))
    benchmark_mean = float(np.mean(benchmark_returns))
    daily_alpha = portfolio_mean - portfolio_beta * benchmark_mean
    alpha_annualized_pct = daily_alpha * annualization_factor * 100.0

    correlation = float(np.corrcoef(portfolio_returns, benchmark_returns)[0, 1])
    r_squared = correlation * correlation

    benchmark_vol = float(
        np.std(benchmark_returns, ddof=1) * np.sqrt(annualization_factor) * 100.0
    )
    portfolio_vol = float(
        np.std(portfolio_returns, ddof=1) * np.sqrt(annualization_factor) * 100.0
    )

    fitted = daily_alpha + portfolio_beta * benchmark_returns
    residuals = portfolio_returns - fitted
    idio_vol = float(
        np.std(residuals, ddof=1) * np.sqrt(annualization_factor) * 100.0
    )
    systematic_vol = abs(portfolio_beta) * benchmark_vol

    metrics: list[AssetBenchmarkMetrics] = []

    for symbol, weight in zip(symbols, weights):
        series = asset_returns[symbol].to_numpy(dtype=float)
        covariance = float(np.cov(series, benchmark_returns, ddof=1)[0, 1])
        beta = covariance / benchmark_variance
        mean_return = float(np.mean(series))
        daily_asset_alpha = mean_return - beta * benchmark_mean
        asset_alpha = daily_asset_alpha * annualization_factor * 100.0
        asset_corr = float(np.corrcoef(series, benchmark_returns)[0, 1])
        beta_contribution = float(weight * beta)

        signed_exposure = exposure_by_symbol[symbol]["signed"]
        if signed_exposure > 0:
            direction = "LONG"
        elif signed_exposure < 0:
            direction = "SHORT"
        else:
            direction = "FLAT"

        metrics.append(
            AssetBenchmarkMetrics(
                yahoo_symbol=symbol,
                direction=direction,
                signed_weight_pct=float(weight * 100.0),
                beta=float(beta),
                alpha_annualized_pct=float(asset_alpha),
                correlation=asset_corr,
                r_squared=asset_corr * asset_corr,
                beta_contribution=beta_contribution,
            )
        )

    beta_from_components = float(sum(m.beta_contribution for m in metrics))

    return PortfolioBenchmarkRiskSummary(
        benchmark_symbol=benchmark_symbol,
        assets=len(symbols),
        observations=len(returns),
        gross_exposure_eur=gross_exposure,
        risk_coverage_pct=coverage_pct,
        excluded_symbols=tuple(excluded),
        portfolio_beta=float(portfolio_beta),
        beta_from_components=beta_from_components,
        alpha_annualized_pct=float(alpha_annualized_pct),
        correlation=correlation,
        r_squared=r_squared,
        benchmark_volatility_pct=benchmark_vol,
        portfolio_volatility_pct=portfolio_vol,
        systematic_volatility_pct=float(systematic_vol),
        idiosyncratic_volatility_pct=idio_vol,
        asset_metrics=tuple(metrics),
    )
