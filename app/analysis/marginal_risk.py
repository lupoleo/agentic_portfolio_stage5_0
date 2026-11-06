from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Iterable

import numpy as np
import pandas as pd

from app.analysis.risk import (
    calculate_covariance_risk,
    calculate_historical_tail_risk,
)
from app.analysis.benchmark import calculate_benchmark_risk


@dataclass(frozen=True)
class MarginalMetricDelta:
    before: float
    after: float
    delta: float


@dataclass(frozen=True)
class PortfolioMarginalRiskResult:
    candidate_symbol: str
    direction: str
    candidate_exposure_eur: float

    volatility_pct: MarginalMetricDelta
    beta: MarginalMetricDelta
    var_95_1d_eur: MarginalMetricDelta
    cvar_95_1d_eur: MarginalMetricDelta

    candidate_correlation_to_portfolio: float | None
    candidate_component_risk_pct_points: float | None
    candidate_risk_contribution_pct: float | None

    covariance_coverage_before_pct: float
    covariance_coverage_after_pct: float
    benchmark_coverage_before_pct: float
    benchmark_coverage_after_pct: float
    tail_coverage_before_pct: float
    tail_coverage_after_pct: float
    analytical_coverage_before_pct: float
    analytical_coverage_after_pct: float

    excluded_symbols_before: tuple[str, ...]
    excluded_symbols_after: tuple[str, ...]
    observations_before: int
    observations_after: int

    top5_concentration_before_pct: float | None = None
    top5_concentration_after_pct: float | None = None
    effective_positions_before: float | None = None
    effective_positions_after: float | None = None


def _gross_weight_concentration(analyzed_positions) -> tuple[float, float]:
    """
    Canonical Stage 2.1 concentration semantics:
    absolute economic exposure / gross exposure. LONG and SHORT do not net.
    """
    exposures = [
        abs(float(item.position.market_value_eur))
        for item in analyzed_positions
        if abs(float(item.position.market_value_eur)) > 0
    ]
    gross = sum(exposures)
    if gross <= 0:
        return 0.0, 0.0
    weights = [exposure / gross for exposure in exposures]
    top5 = sum(sorted(weights, reverse=True)[:5]) * 100.0
    hhi = sum(weight * weight for weight in weights)
    return float(top5), float(1.0 / hhi if hhi > 0 else 0.0)


def _delta(before: float, after: float) -> MarginalMetricDelta:
    return MarginalMetricDelta(
        before=float(before),
        after=float(after),
        delta=float(after - before),
    )


def _direction_sign(direction: str) -> int:
    value = str(direction).upper()
    if value == "LONG":
        return 1
    if value == "SHORT":
        return -1
    raise ValueError("Candidate direction must be LONG or SHORT")


def _normalize_close(history: pd.DataFrame, name: str) -> pd.Series:
    if history is None or history.empty or "Close" not in history.columns:
        raise ValueError(f"Candidate history for {name} is empty or has no Close column")
    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if len(close) < 3:
        raise ValueError(f"Candidate history for {name} is insufficient")
    try:
        close.index = close.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    return close.rename(name)


def _candidate_position(
    symbol: str,
    direction: str,
    exposure_eur: float,
    history: pd.DataFrame,
):
    sign = _direction_sign(direction)
    return SimpleNamespace(
        yahoo_symbol=symbol,
        position=SimpleNamespace(
            quantity=float(sign),
            market_value_eur=float(exposure_eur),
            direction=direction.upper(),
            name=symbol,
        ),
        history=history,
    )


def _candidate_portfolio_correlation_from_covariance(
    analyzed_positions,
    candidate_symbol: str,
    covariance_summary,
) -> float | None:
    """
    Correlation of the candidate risk factor with the CURRENT signed
    portfolio, evaluated on the exact coherent sample retained by the
    canonical Stage 2 covariance engine.

    This derives the diagnostic from covariance_summary rather than
    re-aligning histories or inventing a second common-history pruning
    policy.
    """
    covariance = getattr(covariance_summary, "covariance_matrix", None)
    if covariance is None or getattr(covariance, "empty", True):
        return None
    if (
        candidate_symbol not in covariance.index
        or candidate_symbol not in covariance.columns
    ):
        return None

    signed_exposure: dict[str, float] = {}
    gross_before = 0.0

    for item in analyzed_positions:
        direction = str(item.position.direction).upper()
        if direction not in {"LONG", "SHORT"}:
            continue

        exposure = abs(float(item.position.market_value_eur))
        sign = 1.0 if direction == "LONG" else -1.0
        gross_before += exposure
        signed_exposure[item.yahoo_symbol] = (
            signed_exposure.get(item.yahoo_symbol, 0.0)
            + exposure * sign
        )

    if gross_before <= 0:
        return None

    retained_current_symbols = [
        symbol
        for symbol in covariance.columns
        if symbol in signed_exposure
    ]
    if not retained_current_symbols:
        return None

    weights = np.array(
        [
            signed_exposure[symbol] / gross_before
            for symbol in retained_current_symbols
        ],
        dtype=float,
    )

    sigma_pp = covariance.loc[
        retained_current_symbols, retained_current_symbols
    ].to_numpy(dtype=float)
    sigma_cp = covariance.loc[
        candidate_symbol, retained_current_symbols
    ].to_numpy(dtype=float)
    sigma_cc = float(covariance.loc[candidate_symbol, candidate_symbol])

    portfolio_variance = float(weights @ sigma_pp @ weights)
    candidate_portfolio_covariance = float(sigma_cp @ weights)

    if portfolio_variance <= 0 or sigma_cc <= 0:
        return None

    denominator = float(np.sqrt(portfolio_variance * sigma_cc))
    if denominator <= 0:
        return None

    value = candidate_portfolio_covariance / denominator
    return float(max(-1.0, min(1.0, value)))


class PortfolioMarginalRiskEngine:
    """
    Shared PF/Simulator quantitative engine.

    BEFORE and AFTER are both calculated by the canonical Stage 2.x
    covariance, benchmark and historical-tail engines. Candidate exposure is
    represented as one synthetic analyzed position carrying only the fields
    those engines consume. No covariance/beta/VaR formula is duplicated here.
    """

    def __init__(
        self,
        *,
        benchmark_symbol: str = "SPY",
        min_observations: int = 504,
        max_observations: int | None = 756,
        annualization_factor: int = 252,
    ):
        self.benchmark_symbol = benchmark_symbol
        self.min_observations = min_observations
        self.max_observations = max_observations
        self.annualization_factor = annualization_factor

    def assess(
        self,
        *,
        analyzed_positions,
        candidate_symbol: str,
        direction: str,
        candidate_exposure_eur: float,
        candidate_history: pd.DataFrame,
        benchmark_history: pd.DataFrame,
    ) -> PortfolioMarginalRiskResult:
        if candidate_exposure_eur <= 0:
            raise ValueError("candidate_exposure_eur must be positive")
        _direction_sign(direction)

        before_positions = list(analyzed_positions)
        after_positions = before_positions + [
            _candidate_position(
                candidate_symbol,
                direction,
                candidate_exposure_eur,
                candidate_history,
            )
        ]

        top5_before, effective_positions_before = (
            _gross_weight_concentration(before_positions)
        )
        top5_after, effective_positions_after = (
            _gross_weight_concentration(after_positions)
        )

        kwargs = dict(
            min_observations=self.min_observations,
            max_observations=self.max_observations,
        )
        cov_before = calculate_covariance_risk(
            before_positions,
            annualization_factor=self.annualization_factor,
            **kwargs,
        )
        cov_after = calculate_covariance_risk(
            after_positions,
            annualization_factor=self.annualization_factor,
            **kwargs,
        )

        bench_before = calculate_benchmark_risk(
            before_positions,
            self.benchmark_symbol,
            benchmark_history,
            annualization_factor=self.annualization_factor,
            **kwargs,
        )
        bench_after = calculate_benchmark_risk(
            after_positions,
            self.benchmark_symbol,
            benchmark_history,
            annualization_factor=self.annualization_factor,
            **kwargs,
        )

        tail_before = calculate_historical_tail_risk(
            before_positions,
            confidence_levels=(0.95,),
            horizon_days=1,
            **kwargs,
        )
        tail_after = calculate_historical_tail_risk(
            after_positions,
            confidence_levels=(0.95,),
            horizon_days=1,
            **kwargs,
        )
        before_95 = tail_before.levels[0]
        after_95 = tail_after.levels[0]

        candidate_metric = next(
            (m for m in cov_after.asset_metrics if m.yahoo_symbol == candidate_symbol),
            None,
        )

        corr = _candidate_portfolio_correlation_from_covariance(
            before_positions,
            candidate_symbol,
            cov_after,
        )

        excluded_before = tuple(sorted(set(
            cov_before.excluded_symbols
            + bench_before.excluded_symbols
            + tail_before.excluded_symbols
        )))
        excluded_after = tuple(sorted(set(
            cov_after.excluded_symbols
            + bench_after.excluded_symbols
            + tail_after.excluded_symbols
        )))

        coverage_before = min(
            cov_before.risk_coverage_pct,
            bench_before.risk_coverage_pct,
            tail_before.risk_coverage_pct,
        )
        coverage_after = min(
            cov_after.risk_coverage_pct,
            bench_after.risk_coverage_pct,
            tail_after.risk_coverage_pct,
        )

        return PortfolioMarginalRiskResult(
            candidate_symbol=candidate_symbol,
            direction=direction.upper(),
            candidate_exposure_eur=float(candidate_exposure_eur),
            volatility_pct=_delta(
                cov_before.portfolio_volatility_pct,
                cov_after.portfolio_volatility_pct,
            ),
            beta=_delta(
                bench_before.portfolio_beta,
                bench_after.portfolio_beta,
            ),
            var_95_1d_eur=_delta(before_95.var_eur, after_95.var_eur),
            cvar_95_1d_eur=_delta(before_95.cvar_eur, after_95.cvar_eur),
            candidate_correlation_to_portfolio=corr,
            candidate_component_risk_pct_points=(
                None if candidate_metric is None
                else float(candidate_metric.component_risk_pct_points)
            ),
            candidate_risk_contribution_pct=(
                None if candidate_metric is None
                else float(candidate_metric.risk_contribution_pct)
            ),
            covariance_coverage_before_pct=float(cov_before.risk_coverage_pct),
            covariance_coverage_after_pct=float(cov_after.risk_coverage_pct),
            benchmark_coverage_before_pct=float(bench_before.risk_coverage_pct),
            benchmark_coverage_after_pct=float(bench_after.risk_coverage_pct),
            tail_coverage_before_pct=float(tail_before.risk_coverage_pct),
            tail_coverage_after_pct=float(tail_after.risk_coverage_pct),
            analytical_coverage_before_pct=float(coverage_before),
            analytical_coverage_after_pct=float(coverage_after),
            excluded_symbols_before=excluded_before,
            excluded_symbols_after=excluded_after,
            observations_before=min(
                cov_before.observations,
                bench_before.observations,
                tail_before.observations,
            ),
            observations_after=min(
                cov_after.observations,
                bench_after.observations,
                tail_after.observations,
            ),
            top5_concentration_before_pct=top5_before,
            top5_concentration_after_pct=top5_after,
            effective_positions_before=effective_positions_before,
            effective_positions_after=effective_positions_after,
        )
