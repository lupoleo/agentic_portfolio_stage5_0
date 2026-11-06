from __future__ import annotations

from dataclasses import dataclass

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.analysis.portfolio import AnalyzedPosition


@dataclass(frozen=True)
class PositionRiskMetrics:
    """
    Portfolio-risk metrics for one analyzed position.

    gross_weight_pct:
        Absolute economic exposure divided by gross portfolio exposure.
        Always non-negative and sums to 100% across non-flat positions.

    net_weight_pct:
        Signed economic exposure divided by gross portfolio exposure.
        LONG positions are positive, SHORT positions are negative.

    volatility_load:
        Gross weight multiplied by annualized 20-day volatility.
        This is a simple risk-intensity measure, NOT covariance-aware
        portfolio risk contribution.

    technical_risk_load:
        Gross weight multiplied by the 0-100 technical risk score.
        This is an exposure-weighted technical-risk measure, not VaR.
    """

    yahoo_symbol: str
    direction: str
    exposure_eur: float
    signed_exposure_eur: float
    gross_weight_pct: float
    net_weight_pct: float
    volatility_20d_pct: float
    technical_risk_score: float
    volatility_load: float
    technical_risk_load: float


@dataclass(frozen=True)
class PortfolioRiskSummary:
    """Portfolio-level exposure and concentration metrics."""

    positions: int
    long_positions: int
    short_positions: int
    flat_positions: int

    long_exposure_eur: float
    short_exposure_eur: float
    gross_exposure_eur: float
    net_exposure_eur: float
    net_to_gross_pct: float

    largest_position_pct: float
    top3_concentration_pct: float
    top5_concentration_pct: float
    top10_concentration_pct: float
    hhi: float
    effective_positions: float

    weighted_volatility_20d_pct: float
    weighted_technical_risk_score: float

    position_metrics: tuple[PositionRiskMetrics, ...]


def _economic_exposure_eur(item: AnalyzedPosition) -> float:
    """
    Return the absolute economic exposure of a position.

    Fineco direction is determined from quantity, not from the sign of
    market_value_eur.  Using abs(market_value_eur) makes the calculation
    robust whether the broker reports SHORT market value as positive or
    negative.
    """

    return abs(float(item.position.market_value_eur))


def _direction_sign(direction: str) -> int:
    direction = direction.upper()

    if direction == "LONG":
        return 1
    if direction == "SHORT":
        return -1
    return 0


def calculate_portfolio_risk(
    analyzed_positions: list[AnalyzedPosition],
) -> PortfolioRiskSummary:
    """
    Calculate Stage 2.1 portfolio-risk metrics.

    Long/short conventions
    ----------------------
    Gross exposure:
        sum(abs(position exposure))

    Net exposure:
        LONG exposure - SHORT exposure

    Gross weight:
        abs(position exposure) / gross exposure

    Net weight:
        signed position exposure / gross exposure

    Concentration metrics (Top-N, HHI, effective positions) use gross
    weights.  This is intentional: a LONG and a SHORT do not cancel the
    capital/risk concentration merely because their signed exposures offset.

    This stage intentionally does NOT yet calculate covariance-aware
    portfolio volatility, marginal risk contribution, beta, VaR or CVaR.
    """

    if not analyzed_positions:
        return PortfolioRiskSummary(
            positions=0,
            long_positions=0,
            short_positions=0,
            flat_positions=0,
            long_exposure_eur=0.0,
            short_exposure_eur=0.0,
            gross_exposure_eur=0.0,
            net_exposure_eur=0.0,
            net_to_gross_pct=0.0,
            largest_position_pct=0.0,
            top3_concentration_pct=0.0,
            top5_concentration_pct=0.0,
            top10_concentration_pct=0.0,
            hhi=0.0,
            effective_positions=0.0,
            weighted_volatility_20d_pct=0.0,
            weighted_technical_risk_score=0.0,
            position_metrics=(),
        )

    exposures: list[tuple[AnalyzedPosition, float, int]] = []

    long_exposure = 0.0
    short_exposure = 0.0
    long_positions = 0
    short_positions = 0
    flat_positions = 0

    for item in analyzed_positions:
        exposure = _economic_exposure_eur(item)
        sign = _direction_sign(item.position.direction)

        exposures.append((item, exposure, sign))

        if sign > 0:
            long_positions += 1
            long_exposure += exposure
        elif sign < 0:
            short_positions += 1
            short_exposure += exposure
        else:
            flat_positions += 1

    gross_exposure = long_exposure + short_exposure
    net_exposure = long_exposure - short_exposure

    if gross_exposure <= 0:
        raise ValueError("Portfolio gross exposure is zero")

    metrics: list[PositionRiskMetrics] = []

    for item, exposure, sign in exposures:
        gross_weight = exposure / gross_exposure
        signed_exposure = exposure * sign
        net_weight = signed_exposure / gross_exposure

        volatility = float(item.technical.volatility_20d_pct)
        technical_risk = float(item.scores.risk_score)

        metrics.append(
            PositionRiskMetrics(
                yahoo_symbol=item.yahoo_symbol,
                direction=item.position.direction,
                exposure_eur=exposure,
                signed_exposure_eur=signed_exposure,
                gross_weight_pct=gross_weight * 100,
                net_weight_pct=net_weight * 100,
                volatility_20d_pct=volatility,
                technical_risk_score=technical_risk,
                volatility_load=gross_weight * volatility,
                technical_risk_load=gross_weight * technical_risk,
            )
        )

    gross_weights = [
        metric.gross_weight_pct / 100
        for metric in metrics
        if metric.exposure_eur > 0
    ]

    sorted_weights = sorted(gross_weights, reverse=True)

    def top_n(n: int) -> float:
        return sum(sorted_weights[:n]) * 100

    largest_position = (
        sorted_weights[0] * 100
        if sorted_weights
        else 0.0
    )

    hhi = sum(weight**2 for weight in gross_weights)
    effective_positions = 1 / hhi if hhi > 0 else 0.0

    weighted_volatility = sum(
        metric.volatility_load
        for metric in metrics
    )

    weighted_technical_risk = sum(
        metric.technical_risk_load
        for metric in metrics
    )

    net_to_gross = net_exposure / gross_exposure * 100

    return PortfolioRiskSummary(
        positions=len(analyzed_positions),
        long_positions=long_positions,
        short_positions=short_positions,
        flat_positions=flat_positions,
        long_exposure_eur=long_exposure,
        short_exposure_eur=short_exposure,
        gross_exposure_eur=gross_exposure,
        net_exposure_eur=net_exposure,
        net_to_gross_pct=net_to_gross,
        largest_position_pct=largest_position,
        top3_concentration_pct=top_n(3),
        top5_concentration_pct=top_n(5),
        top10_concentration_pct=top_n(10),
        hhi=hhi,
        effective_positions=effective_positions,
        weighted_volatility_20d_pct=weighted_volatility,
        weighted_technical_risk_score=weighted_technical_risk,
        position_metrics=tuple(metrics),
    )


@dataclass(frozen=True)
class AssetCovarianceRiskMetrics:
    """Covariance-aware market-risk metrics aggregated by Yahoo symbol."""

    yahoo_symbol: str
    direction: str
    gross_exposure_eur: float
    signed_exposure_eur: float
    signed_weight_pct: float
    annualized_volatility_pct: float
    marginal_risk: float
    component_risk_pct_points: float
    risk_contribution_pct: float


@dataclass(frozen=True)
class PortfolioCovarianceRiskSummary:
    """
    Stage 2.2 covariance-aware portfolio market risk.

    Portfolio volatility is calculated from signed LONG/SHORT weights
    normalized by gross exposure:

        sigma_p = sqrt(w.T @ Sigma @ w)

    where LONG weights are positive and SHORT weights are negative.

    Component risk contributions can be negative.  A negative component
    contribution means the position is reducing portfolio volatility under
    the covariance model (i.e. acting as a hedge over the estimation window).
    """

    observations: int
    assets: int
    annualization_factor: int
    portfolio_volatility_pct: float
    diversification_ratio: float
    weighted_standalone_volatility_pct: float
    risk_coverage_pct: float
    excluded_symbols: tuple[str, ...]
    asset_metrics: tuple[AssetCovarianceRiskMetrics, ...]
    correlation_matrix: object
    covariance_matrix: object


def _close_series(item):
    """Return a clean adjusted-close series from an analyzed position."""

    history = getattr(item, "history", None)

    if history is None or history.empty or "Close" not in history.columns:
        return None

    close = history["Close"].copy()

    # Defensive handling for an unexpected one-column DataFrame.
    if getattr(close, "ndim", 1) != 1:
        if close.shape[1] != 1:
            return None
        close = close.iloc[:, 0]

    close = close.astype(float).dropna()
    return close if len(close) >= 3 else None


def calculate_covariance_risk(
    analyzed_positions: list[AnalyzedPosition],
    annualization_factor: int = 252,
    min_observations: int = 60,
    max_observations: int | None = None,
) -> PortfolioCovarianceRiskSummary:
    """
    Calculate Stage 2.2 covariance-aware portfolio volatility and risk
    contribution with native LONG/SHORT support.

    Design choices
    --------------
    * Direction is determined from PortfolioPosition.direction.
    * Exposure uses abs(market_value_eur), so broker accounting signs do not
      determine LONG/SHORT direction.
    * Multiple Fineco positions mapped to the same Yahoo symbol are aggregated
      before covariance calculations.
    * Signed asset weights are normalized by total portfolio gross exposure.
    * Returns are daily percentage returns from adjusted Yahoo Close prices.
    * The covariance matrix is annualized with 252 trading sessions by default.
    * Component contributions sum to portfolio volatility (apart from floating
      point rounding) and may be negative for effective hedges.
    """

    import math
    import numpy as np
    import pandas as pd

    if not analyzed_positions:
        empty = pd.DataFrame()
        return PortfolioCovarianceRiskSummary(
            observations=0,
            assets=0,
            annualization_factor=annualization_factor,
            portfolio_volatility_pct=0.0,
            diversification_ratio=0.0,
            weighted_standalone_volatility_pct=0.0,
            risk_coverage_pct=0.0,
            excluded_symbols=(),
            asset_metrics=(),
            correlation_matrix=empty,
            covariance_matrix=empty,
        )

    gross_exposure = sum(
        _economic_exposure_eur(item)
        for item in analyzed_positions
        if _direction_sign(item.position.direction) != 0
    )

    if gross_exposure <= 0:
        raise ValueError("Portfolio gross exposure is zero")

    # Aggregate economic exposures by market-risk factor (Yahoo symbol).
    asset_exposure: dict[str, dict[str, float]] = {}
    close_by_symbol: dict[str, pd.Series] = {}

    for item in analyzed_positions:
        sign = _direction_sign(item.position.direction)
        if sign == 0:
            continue

        exposure = _economic_exposure_eur(item)
        symbol = item.yahoo_symbol

        bucket = asset_exposure.setdefault(
            symbol,
            {"gross": 0.0, "signed": 0.0},
        )
        bucket["gross"] += exposure
        bucket["signed"] += exposure * sign

        if symbol not in close_by_symbol:
            close = _close_series(item)
            if close is not None:
                close_by_symbol[symbol] = close.rename(symbol)

    # Exclude factors that cannot support the requested estimation window
    # instead of failing the entire portfolio report. Coverage is reported.
    eligible_symbols = sorted(
        symbol
        for symbol, close in close_by_symbol.items()
        if len(close) >= min_observations + 1
    )
    excluded_symbols = set(asset_exposure) - set(eligible_symbols)

    if not eligible_symbols:
        raise ValueError(
            "No asset has enough usable price history for covariance analysis"
        )

    def _common_returns(symbols: list[str]) -> pd.DataFrame:
        prices = pd.concat(
            [close_by_symbol[s] for s in symbols],
            axis=1,
            join="inner",
        ).dropna(how="any")

        return prices.pct_change(
            fill_method=None
        ).dropna(how="any")

    # Use a common set of trading dates. If an individually eligible factor
    # makes the coherent joint sample too short, exclude deterministically the
    # factor whose removal produces the largest common-history recovery.
    retained_symbols = list(eligible_symbols)
    returns = _common_returns(retained_symbols)

    while (
        len(returns) < min_observations
        and len(retained_symbols) > 1
    ):
        current_observations = len(returns)
        candidates = []

        for symbol in retained_symbols:
            candidate_symbols = [
                s
                for s in retained_symbols
                if s != symbol
            ]
            candidate_returns = _common_returns(candidate_symbols)

            candidates.append(
                (
                    len(candidate_returns),
                    symbol,
                    candidate_returns,
                )
            )

        best_observations = max(
            candidate[0]
            for candidate in candidates
        )

        best_candidates = [
            candidate
            for candidate in candidates
            if candidate[0] == best_observations
        ]

        # Deterministic alphabetical tie-break.
        best_observations, excluded_symbol, best_returns = min(
            best_candidates,
            key=lambda candidate: candidate[1],
        )

        # Do not sacrifice coverage unless the exclusion improves the
        # coherent common-history sample.
        if best_observations <= current_observations:
            break

        retained_symbols.remove(excluded_symbol)
        excluded_symbols.add(excluded_symbol)
        returns = best_returns

    if len(returns) < min_observations:
        raise ValueError(
            f"Insufficient common return observations: {len(returns)} "
            f"(minimum required: {min_observations})"
        )

    eligible_symbols = retained_symbols
    excluded_symbols = sorted(excluded_symbols)

    covered_gross_exposure = sum(
        asset_exposure[symbol]["gross"]
        for symbol in eligible_symbols
    )
    risk_coverage_pct = covered_gross_exposure / gross_exposure * 100

    if max_observations is not None:
        if max_observations < min_observations:
            raise ValueError(
                "max_observations cannot be smaller than min_observations"
            )
        returns = returns.tail(max_observations)

    symbols = list(returns.columns)
    weights = np.array(
        [asset_exposure[s]["signed"] / gross_exposure for s in symbols],
        dtype=float,
    )

    covariance_daily = returns.cov()
    covariance_annual = covariance_daily * annualization_factor
    correlation = returns.corr()

    cov_values = covariance_annual.loc[symbols, symbols].to_numpy(dtype=float)
    variance = float(weights @ cov_values @ weights)

    # Tiny negative values can occur from floating point round-off.
    if variance < 0 and abs(variance) < 1e-15:
        variance = 0.0
    if variance < 0:
        raise ValueError("Calculated portfolio variance is negative")

    portfolio_vol = math.sqrt(variance)
    standalone_vol = np.sqrt(np.diag(cov_values))

    weighted_standalone_vol = float(
        np.sum(np.abs(weights) * standalone_vol)
    )
    diversification_ratio = (
        weighted_standalone_vol / portfolio_vol
        if portfolio_vol > 0
        else 0.0
    )

    if portfolio_vol > 0:
        marginal_risk = cov_values @ weights / portfolio_vol
        component_risk = weights * marginal_risk
        contribution_pct = component_risk / portfolio_vol * 100
    else:
        marginal_risk = np.zeros_like(weights)
        component_risk = np.zeros_like(weights)
        contribution_pct = np.zeros_like(weights)

    metrics: list[AssetCovarianceRiskMetrics] = []

    for i, symbol in enumerate(symbols):
        signed_exposure = asset_exposure[symbol]["signed"]
        direction = (
            "LONG" if signed_exposure > 0
            else "SHORT" if signed_exposure < 0
            else "FLAT"
        )

        metrics.append(
            AssetCovarianceRiskMetrics(
                yahoo_symbol=symbol,
                direction=direction,
                gross_exposure_eur=asset_exposure[symbol]["gross"],
                signed_exposure_eur=signed_exposure,
                signed_weight_pct=weights[i] * 100,
                annualized_volatility_pct=standalone_vol[i] * 100,
                marginal_risk=float(marginal_risk[i]),
                component_risk_pct_points=float(component_risk[i] * 100),
                risk_contribution_pct=float(contribution_pct[i]),
            )
        )

    covariance_pct2 = covariance_annual * 10_000

    return PortfolioCovarianceRiskSummary(
        observations=len(returns),
        assets=len(symbols),
        annualization_factor=annualization_factor,
        portfolio_volatility_pct=portfolio_vol * 100,
        diversification_ratio=diversification_ratio,
        weighted_standalone_volatility_pct=weighted_standalone_vol * 100,
        risk_coverage_pct=risk_coverage_pct,
        excluded_symbols=tuple(excluded_symbols),
        asset_metrics=tuple(metrics),
        correlation_matrix=correlation,
        covariance_matrix=covariance_pct2,
    )


@dataclass(frozen=True)
class HistoricalTailRiskLevel:
    """Historical loss-tail statistics for one confidence level."""

    confidence_level_pct: float
    var_pct: float
    cvar_pct: float
    var_eur: float
    cvar_eur: float
    tail_observations: int


@dataclass(frozen=True)
class PortfolioHistoricalTailRiskSummary:
    """
    Stage 2.3 historical VaR / CVaR (Expected Shortfall).

    Returns are built from current signed LONG/SHORT exposures normalized by
    total portfolio gross exposure.  Therefore all percentage risk measures
    are expressed as a percentage of gross exposure, consistently with Stage
    2.2.

    VaR is reported as a positive loss threshold. CVaR / Expected Shortfall
    is the positive average loss conditional on being at or beyond VaR.
    """

    horizon_days: int
    observations: int
    assets: int
    gross_exposure_eur: float
    risk_coverage_pct: float
    excluded_symbols: tuple[str, ...]
    mean_return_pct: float
    best_return_pct: float
    worst_return_pct: float
    positive_periods_pct: float
    levels: tuple[HistoricalTailRiskLevel, ...]


def calculate_historical_tail_risk(
    analyzed_positions: list[AnalyzedPosition],
    confidence_levels: tuple[float, ...] = (0.95, 0.99),
    horizon_days: int = 1,
    min_observations: int = 60,
    max_observations: int | None = None,
) -> PortfolioHistoricalTailRiskSummary:
    """
    Calculate Stage 2.3 non-parametric Historical VaR and CVaR/Expected Shortfall.

    LONG/SHORT handling
    -------------------
    For each market factor, Fineco positions are first aggregated into a
    signed EUR exposure. Portfolio historical returns are then:

        r_p,t = sum_i(w_i * r_i,t)

    with w_i = signed_exposure_i / total_gross_exposure.

    For horizons above one day, daily portfolio returns are compounded over
    rolling windows; no square-root-of-time approximation is used.

    Notes
    -----
    * VaR/CVaR are loss measures and are returned as positive numbers.
    * If the empirical quantile is positive (i.e. even the selected lower-tail
      return is a gain), VaR is floored at zero rather than reporting a
      negative "loss".
    * Percentage and EUR figures are relative to total gross exposure.
    """

    import numpy as np
    import pandas as pd

    if horizon_days < 1:
        raise ValueError("horizon_days must be at least 1")
    if min_observations < 2:
        raise ValueError("min_observations must be at least 2")
    if max_observations is not None and max_observations < min_observations:
        raise ValueError(
            "max_observations cannot be smaller than min_observations"
        )
    if not confidence_levels:
        raise ValueError("At least one confidence level is required")
    for confidence in confidence_levels:
        if not 0 < confidence < 1:
            raise ValueError("Confidence levels must be between 0 and 1")

    if not analyzed_positions:
        return PortfolioHistoricalTailRiskSummary(
            horizon_days=horizon_days,
            observations=0,
            assets=0,
            gross_exposure_eur=0.0,
            risk_coverage_pct=0.0,
            excluded_symbols=(),
            mean_return_pct=0.0,
            best_return_pct=0.0,
            worst_return_pct=0.0,
            positive_periods_pct=0.0,
            levels=(),
        )

    gross_exposure = sum(
        _economic_exposure_eur(item)
        for item in analyzed_positions
        if _direction_sign(item.position.direction) != 0
    )
    if gross_exposure <= 0:
        raise ValueError("Portfolio gross exposure is zero")

    asset_exposure: dict[str, dict[str, float]] = {}
    close_by_symbol: dict[str, pd.Series] = {}

    for item in analyzed_positions:
        sign = _direction_sign(item.position.direction)
        if sign == 0:
            continue

        exposure = _economic_exposure_eur(item)
        symbol = item.yahoo_symbol
        bucket = asset_exposure.setdefault(symbol, {"gross": 0.0, "signed": 0.0})
        bucket["gross"] += exposure
        bucket["signed"] += exposure * sign

        if symbol not in close_by_symbol:
            close = _close_series(item)
            if close is not None:
                close_by_symbol[symbol] = close.rename(symbol)

    required_prices = min_observations + horizon_days
    eligible_symbols = sorted(
        symbol
        for symbol, close in close_by_symbol.items()
        if len(close) >= required_prices
    )
    excluded_symbols = sorted(set(asset_exposure) - set(eligible_symbols))

    if not eligible_symbols:
        raise ValueError(
            "No asset has enough usable price history for historical tail-risk analysis"
        )

    def _common_daily_returns(
        symbols: list[str],
    ) -> pd.DataFrame:
        prices = pd.concat(
            [close_by_symbol[s] for s in symbols],
            axis=1,
            join="inner",
        ).dropna(how="any")

        daily_returns = (
            prices
            .pct_change(fill_method=None)
            .dropna(how="any")
        )

        if max_observations is not None:
            # Keep enough daily observations to produce max_observations
            # rolling horizon returns after compounding.
            daily_limit = max_observations + horizon_days - 1
            daily_returns = daily_returns.tail(daily_limit)

        return daily_returns

    # Common-history robustness:
    # an asset may pass the individual-history gate but still make the
    # joint daily return sample too short. Exclude deterministically the
    # portfolio asset whose removal yields the largest common-history gain.
    required_common_returns = min_observations + horizon_days - 1

    active_symbols = list(eligible_symbols)
    daily_returns = _common_daily_returns(active_symbols)

    while (
        len(daily_returns) < required_common_returns
        and len(active_symbols) > 1
    ):
        current_observations = len(daily_returns)
        candidates = []

        for symbol in active_symbols:
            trial_symbols = [
                s for s in active_symbols if s != symbol
            ]
            trial_returns = _common_daily_returns(trial_symbols)

            candidates.append(
                (
                    len(trial_returns),
                    asset_exposure[symbol]["gross"],
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

        excluded_symbols.append(removed_symbol)
        active_symbols = best_symbols
        daily_returns = best_returns

    if len(daily_returns) < required_common_returns:
        raise ValueError(
            "Insufficient common return observations "
            f"after deterministic asset exclusion: {len(daily_returns)} "
            f"(minimum required: {required_common_returns})"
        )

    symbols = list(daily_returns.columns)
    excluded_symbols = sorted(set(excluded_symbols))

    covered_gross = sum(
        asset_exposure[s]["gross"]
        for s in symbols
    )
    risk_coverage_pct = covered_gross / gross_exposure * 100
    weights = np.array(
        [asset_exposure[s]["signed"] / gross_exposure for s in symbols],
        dtype=float,
    )

    portfolio_daily = pd.Series(
        daily_returns.to_numpy(dtype=float) @ weights,
        index=daily_returns.index,
        name="portfolio_return",
    )

    if horizon_days == 1:
        portfolio_returns = portfolio_daily
    else:
        portfolio_returns = (
            (1.0 + portfolio_daily)
            .rolling(horizon_days)
            .apply(np.prod, raw=True)
            - 1.0
        ).dropna()

    if len(portfolio_returns) < min_observations:
        raise ValueError(
            f"Insufficient portfolio return observations: {len(portfolio_returns)} "
            f"(minimum required: {min_observations})"
        )

    levels: list[HistoricalTailRiskLevel] = []
    values = portfolio_returns.to_numpy(dtype=float)

    for confidence in sorted(set(confidence_levels)):
        lower_quantile = float(np.quantile(values, 1.0 - confidence))
        var_return = min(lower_quantile, 0.0)
        var_loss = -var_return

        tail = values[values <= lower_quantile]
        if len(tail):
            cvar_return = float(np.mean(tail))
            cvar_loss = max(-cvar_return, 0.0)
        else:
            cvar_loss = var_loss

        levels.append(
            HistoricalTailRiskLevel(
                confidence_level_pct=confidence * 100,
                var_pct=var_loss * 100,
                cvar_pct=cvar_loss * 100,
                var_eur=var_loss * gross_exposure,
                cvar_eur=cvar_loss * gross_exposure,
                tail_observations=int(len(tail)),
            )
        )

    return PortfolioHistoricalTailRiskSummary(
        horizon_days=horizon_days,
        observations=len(portfolio_returns),
        assets=len(symbols),
        gross_exposure_eur=gross_exposure,
        risk_coverage_pct=risk_coverage_pct,
        excluded_symbols=tuple(excluded_symbols),
        mean_return_pct=float(portfolio_returns.mean() * 100),
        best_return_pct=float(portfolio_returns.max() * 100),
        worst_return_pct=float(portfolio_returns.min() * 100),
        positive_periods_pct=float((portfolio_returns > 0).mean() * 100),
        levels=tuple(levels),
    )
