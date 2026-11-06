from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.analysis.marginal_risk import PortfolioMarginalRiskEngine


def history_from_returns(returns, start="2023-01-02"):
    returns = np.asarray(returns, dtype=float)
    prices = 100.0 * np.cumprod(1.0 + returns)
    index = pd.bdate_range(start=start, periods=len(prices))
    return pd.DataFrame({"Close": prices}, index=index)


def make_position(symbol, direction, exposure, history):
    return SimpleNamespace(
        yahoo_symbol=symbol,
        position=SimpleNamespace(
            quantity=1 if direction == "LONG" else -1,
            market_value_eur=exposure,
            direction=direction,
            name=symbol,
        ),
        history=history,
    )


def test_long_market_candidate_increases_beta_and_reports_component_risk():
    rng = np.random.default_rng(310)
    market = rng.normal(0.0002, 0.01, 800)
    base = 0.50 * market + rng.normal(0, 0.004, 800)
    candidate = 1.60 * market + rng.normal(0, 0.002, 800)

    engine = PortfolioMarginalRiskEngine(min_observations=500, max_observations=756)
    result = engine.assess(
        analyzed_positions=[
            make_position("BASE", "LONG", 100_000, history_from_returns(base))
        ],
        candidate_symbol="CAND",
        direction="LONG",
        candidate_exposure_eur=50_000,
        candidate_history=history_from_returns(candidate),
        benchmark_history=history_from_returns(market),
    )

    assert result.beta.after > result.beta.before
    assert result.beta.delta > 0
    assert result.candidate_component_risk_pct_points is not None
    assert result.candidate_risk_contribution_pct is not None
    assert result.analytical_coverage_after_pct == pytest.approx(100.0)


def test_short_positive_beta_candidate_reduces_beta():
    rng = np.random.default_rng(311)
    market = rng.normal(0.0002, 0.01, 800)
    base = 1.20 * market + rng.normal(0, 0.002, 800)
    hedge = 1.00 * market + rng.normal(0, 0.002, 800)

    engine = PortfolioMarginalRiskEngine(min_observations=500, max_observations=756)
    result = engine.assess(
        analyzed_positions=[
            make_position("BASE", "LONG", 100_000, history_from_returns(base))
        ],
        candidate_symbol="HEDGE",
        direction="SHORT",
        candidate_exposure_eur=30_000,
        candidate_history=history_from_returns(hedge),
        benchmark_history=history_from_returns(market),
    )

    assert result.beta.after < result.beta.before
    assert result.beta.delta < 0


def test_candidate_correlation_to_current_portfolio_is_reported():
    rng = np.random.default_rng(312)
    factor = rng.normal(0, 0.01, 800)
    base = factor + rng.normal(0, 0.001, 800)
    candidate = factor + rng.normal(0, 0.001, 800)

    result = PortfolioMarginalRiskEngine(
        min_observations=500, max_observations=756
    ).assess(
        analyzed_positions=[
            make_position("BASE", "LONG", 100_000, history_from_returns(base))
        ],
        candidate_symbol="CAND",
        direction="LONG",
        candidate_exposure_eur=20_000,
        candidate_history=history_from_returns(candidate),
        benchmark_history=history_from_returns(factor),
    )

    assert result.candidate_correlation_to_portfolio is not None
    assert result.candidate_correlation_to_portfolio > 0.9


def test_short_history_candidate_is_reflected_in_after_coverage():
    rng = np.random.default_rng(313)
    market = rng.normal(0, 0.01, 800)
    base = market + rng.normal(0, 0.002, 800)
    young = rng.normal(0, 0.02, 200)

    result = PortfolioMarginalRiskEngine(
        min_observations=500, max_observations=756
    ).assess(
        analyzed_positions=[
            make_position("BASE", "LONG", 80_000, history_from_returns(base))
        ],
        candidate_symbol="YOUNG",
        direction="LONG",
        candidate_exposure_eur=20_000,
        candidate_history=history_from_returns(young),
        benchmark_history=history_from_returns(market),
    )

    assert "YOUNG" in result.excluded_symbols_after
    assert result.analytical_coverage_after_pct == pytest.approx(80.0)
    assert result.candidate_component_risk_pct_points is None


def test_nonpositive_candidate_exposure_is_rejected():
    rng = np.random.default_rng(314)
    x = rng.normal(0, 0.01, 800)
    engine = PortfolioMarginalRiskEngine(min_observations=500)
    with pytest.raises(ValueError, match="must be positive"):
        engine.assess(
            analyzed_positions=[
                make_position("BASE", "LONG", 100_000, history_from_returns(x))
            ],
            candidate_symbol="CAND",
            direction="LONG",
            candidate_exposure_eur=0,
            candidate_history=history_from_returns(x),
            benchmark_history=history_from_returns(x),
        )


def test_candidate_correlation_uses_canonical_covariance_pruned_sample():
    rng = np.random.default_rng(315)
    market = rng.normal(0.0, 0.01, 120)
    a = 0.9 * market + rng.normal(0, 0.002, 120)
    b = 0.6 * market + rng.normal(0, 0.003, 120)
    bad = rng.normal(0, 0.02, 120)
    candidate = 1.1 * market + rng.normal(0, 0.002, 120)

    history_a = history_from_returns(a, start="2025-01-01")
    history_b = history_from_returns(b, start="2025-01-01")
    history_bad = history_from_returns(bad, start="2025-01-01")
    candidate_history = history_from_returns(candidate, start="2025-01-01")
    benchmark_history = history_from_returns(market, start="2025-01-01")

    shifted = history_bad.index.to_series().copy()

    # Force the coherent joint sample below min_observations=60.
    # With only 55 shifted rows, 65 common prices / 64 returns remain,
    # so the canonical covariance engine correctly does not prune BAD.
    shifted.iloc[:70] = shifted.iloc[:70] + pd.Timedelta(days=365)

    history_bad = history_bad.copy()
    history_bad.index = pd.DatetimeIndex(shifted.values)

    positions = [
        make_position("A", "LONG", 40_000, history_a),
        make_position("B", "LONG", 40_000, history_b),
        make_position("BAD", "LONG", 20_000, history_bad),
    ]

    result = PortfolioMarginalRiskEngine(
        min_observations=60,
        max_observations=None,
    ).assess(
        analyzed_positions=positions,
        candidate_symbol="CAND",
        direction="LONG",
        candidate_exposure_eur=10_000,
        candidate_history=candidate_history,
        benchmark_history=benchmark_history,
    )

    assert "BAD" in result.excluded_symbols_after
    assert result.candidate_correlation_to_portfolio is not None
    assert result.candidate_correlation_to_portfolio > 0.8
