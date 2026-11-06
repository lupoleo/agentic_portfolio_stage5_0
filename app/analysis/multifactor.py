from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from app.analysis.portfolio import AnalyzedPosition


@dataclass(frozen=True)
class FactorMetrics:
    name: str
    symbol: str
    beta: float
    beta_from_assets: float
    vif: float


@dataclass(frozen=True)
class AssetFactorMetrics:
    yahoo_symbol: str
    direction: str
    signed_weight_pct: float
    factor_betas: tuple[float, ...]
    factor_beta_contributions: tuple[float, ...]


@dataclass(frozen=True)
class PortfolioMultiFactorSummary:
    factor_names: tuple[str, ...]
    factor_symbols: tuple[str, ...]
    assets: int
    observations: int
    gross_exposure_eur: float
    risk_coverage_pct: float
    excluded_symbols: tuple[str, ...]
    alpha_annualized_pct: float
    r_squared: float
    adjusted_r_squared: float
    portfolio_volatility_pct: float
    systematic_volatility_pct: float
    idiosyncratic_volatility_pct: float
    condition_number: float
    factor_metrics: tuple[FactorMetrics, ...]
    asset_metrics: tuple[AssetFactorMetrics, ...]
    factor_correlation_matrix: pd.DataFrame


def _direction_sign(direction: str) -> int:
    direction = direction.upper()
    if direction == "LONG":
        return 1
    if direction == "SHORT":
        return -1
    return 0


def _economic_exposure_eur(item: AnalyzedPosition) -> float:
    return abs(float(item.position.market_value_eur))


def _close_series(history: pd.DataFrame, name: str) -> pd.Series | None:
    if history is None or history.empty or "Close" not in history.columns:
        return None

    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if close.empty:
        return None

    try:
        close.index = close.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass

    return close.rename(name)


def _calculate_vif(x: np.ndarray, column_index: int) -> float:
    """
    Variance Inflation Factor for one factor.

    VIF = 1 / (1 - R²_j), where factor j is regressed on all
    remaining factors. Values around 1 imply little collinearity;
    >5 is commonly treated as elevated and >10 as severe.
    """

    n, k = x.shape
    if k <= 1:
        return 1.0

    y = x[:, column_index]
    others = np.delete(x, column_index, axis=1)
    design = np.column_stack([np.ones(n), others])

    coeffs, *_ = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ coeffs

    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))

    if ss_tot <= 0:
        return float("inf")

    r_squared = 1.0 - ss_res / ss_tot
    denominator = 1.0 - r_squared

    if denominator <= 1e-12:
        return float("inf")

    return float(1.0 / denominator)


def calculate_multifactor_risk(
    analyzed_positions: list[AnalyzedPosition],
    factor_definitions: dict[str, str],
    factor_histories: dict[str, pd.DataFrame],
    min_observations: int = 504,
    max_observations: int | None = 756,
    annualization_factor: int = 252,
) -> PortfolioMultiFactorSummary:
    """
    Stage 2.5 multi-factor OLS risk model.

    Model:
        Rp,t = alpha + beta_1 F_1,t + ... + beta_k F_k,t + epsilon_t

    Current portfolio weights are applied retrospectively and normalized
    by total gross exposure. LONG weights are positive; SHORT weights are
    negative.

    All portfolio and per-asset factor betas use exactly the same aligned
    return sample. By OLS linearity, the signed-weighted sum of asset beta
    contributions equals the corresponding portfolio factor beta.
    """

    if not factor_definitions:
        raise ValueError("At least one factor is required")
    if min_observations < 2:
        raise ValueError("min_observations must be at least 2")
    if max_observations is not None and max_observations < min_observations:
        raise ValueError("max_observations cannot be smaller than min_observations")
    if not analyzed_positions:
        raise ValueError("No analyzed portfolio positions")

    factor_names = tuple(factor_definitions.keys())
    factor_symbols = tuple(factor_definitions.values())

    missing = [
        symbol
        for symbol in factor_symbols
        if symbol not in factor_histories
        or factor_histories[symbol] is None
        or factor_histories[symbol].empty
    ]
    if missing:
        raise ValueError(
            "Missing market history for factor(s): " + ", ".join(missing)
        )

    gross_exposure = sum(
        _economic_exposure_eur(item)
        for item in analyzed_positions
        if _direction_sign(item.position.direction) != 0
    )
    if gross_exposure <= 0:
        raise ValueError("Portfolio gross exposure is zero")

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
            close = _close_series(item.history, symbol)
            if close is not None:
                close_by_symbol[symbol] = close

    eligible = sorted(
        symbol
        for symbol, close in close_by_symbol.items()
        if len(close) >= min_observations + 1
    )
    excluded = sorted(set(exposure_by_symbol) - set(eligible))

    if not eligible:
        raise ValueError("No asset has enough history for multi-factor analysis")

    factor_closes: list[pd.Series] = []
    factor_column_names: list[str] = []

    for factor_name, factor_symbol in factor_definitions.items():
        close = _close_series(
            factor_histories[factor_symbol],
            f"FACTOR::{factor_name}",
        )
        if close is None:
            raise ValueError(f"Invalid history for factor {factor_name}")
        factor_closes.append(close)
        factor_column_names.append(f"FACTOR::{factor_name}")

    def _common_returns(
        symbols: list[str],
    ) -> pd.DataFrame:
        common_prices = pd.concat(
            [close_by_symbol[s] for s in symbols] + factor_closes,
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

    # Common-history robustness.
    # Factor proxies are structural inputs and must never be removed here.
    # Recovery acts only on portfolio assets.
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

        # Prefer lower gross exposure when history recovery ties,
        # then lexical ticker order for deterministic reproducibility.
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
            "Insufficient common multi-factor observations "
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
    asset_returns = returns[symbols]
    factor_returns_df = returns[factor_column_names].copy()
    factor_returns_df.columns = list(factor_names)

    x = factor_returns_df.to_numpy(dtype=float)
    n = len(x)
    k = x.shape[1]

    # Centering/scaling only for the numerical collinearity diagnostic.
    x_std = np.std(x, axis=0, ddof=1)
    if np.any(x_std <= 0):
        raise ValueError("One or more factors have zero return variance")
    x_standardized = (x - np.mean(x, axis=0)) / x_std
    condition_number = float(np.linalg.cond(x_standardized))

    design = np.column_stack([np.ones(n), x])

    weights = np.array(
        [exposure_by_symbol[s]["signed"] / gross_exposure for s in symbols],
        dtype=float,
    )

    portfolio_returns = asset_returns.to_numpy(dtype=float) @ weights

    portfolio_coeffs, *_ = np.linalg.lstsq(
        design, portfolio_returns, rcond=None
    )
    daily_alpha = float(portfolio_coeffs[0])
    portfolio_betas = np.asarray(portfolio_coeffs[1:], dtype=float)

    fitted = design @ portfolio_coeffs
    residuals = portfolio_returns - fitted

    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((portfolio_returns - np.mean(portfolio_returns)) ** 2))

    if ss_tot <= 0:
        r_squared = 0.0
    else:
        r_squared = 1.0 - ss_res / ss_tot

    if n <= k + 1:
        adjusted_r_squared = float("nan")
    else:
        adjusted_r_squared = 1.0 - (
            (1.0 - r_squared) * (n - 1) / (n - k - 1)
        )

    portfolio_vol = float(
        np.std(portfolio_returns, ddof=1)
        * np.sqrt(annualization_factor)
        * 100.0
    )
    systematic_vol = float(
        np.std(fitted, ddof=1)
        * np.sqrt(annualization_factor)
        * 100.0
    )
    idiosyncratic_vol = float(
        np.std(residuals, ddof=1)
        * np.sqrt(annualization_factor)
        * 100.0
    )

    # Per-asset multivariate betas, all on the same factor/sample matrix.
    asset_beta_matrix = np.zeros((len(symbols), k), dtype=float)

    for i, symbol in enumerate(symbols):
        y = asset_returns[symbol].to_numpy(dtype=float)
        coeffs, *_ = np.linalg.lstsq(design, y, rcond=None)
        asset_beta_matrix[i, :] = coeffs[1:]

    beta_contribution_matrix = weights[:, None] * asset_beta_matrix
    beta_from_assets = np.sum(beta_contribution_matrix, axis=0)

    vifs = tuple(
        _calculate_vif(x, j)
        for j in range(k)
    )

    factor_metrics = tuple(
        FactorMetrics(
            name=factor_names[j],
            symbol=factor_symbols[j],
            beta=float(portfolio_betas[j]),
            beta_from_assets=float(beta_from_assets[j]),
            vif=float(vifs[j]),
        )
        for j in range(k)
    )

    asset_metrics: list[AssetFactorMetrics] = []

    for i, symbol in enumerate(symbols):
        signed_exposure = exposure_by_symbol[symbol]["signed"]

        if signed_exposure > 0:
            direction = "LONG"
        elif signed_exposure < 0:
            direction = "SHORT"
        else:
            direction = "FLAT"

        asset_metrics.append(
            AssetFactorMetrics(
                yahoo_symbol=symbol,
                direction=direction,
                signed_weight_pct=float(weights[i] * 100.0),
                factor_betas=tuple(float(v) for v in asset_beta_matrix[i, :]),
                factor_beta_contributions=tuple(
                    float(v) for v in beta_contribution_matrix[i, :]
                ),
            )
        )

    factor_corr = factor_returns_df.corr()

    return PortfolioMultiFactorSummary(
        factor_names=factor_names,
        factor_symbols=factor_symbols,
        assets=len(symbols),
        observations=len(returns),
        gross_exposure_eur=gross_exposure,
        risk_coverage_pct=coverage_pct,
        excluded_symbols=tuple(excluded),
        alpha_annualized_pct=float(
            daily_alpha * annualization_factor * 100.0
        ),
        r_squared=float(r_squared),
        adjusted_r_squared=float(adjusted_r_squared),
        portfolio_volatility_pct=portfolio_vol,
        systematic_volatility_pct=systematic_vol,
        idiosyncratic_volatility_pct=idiosyncratic_vol,
        condition_number=condition_number,
        factor_metrics=factor_metrics,
        asset_metrics=tuple(asset_metrics),
        factor_correlation_matrix=factor_corr,
    )
