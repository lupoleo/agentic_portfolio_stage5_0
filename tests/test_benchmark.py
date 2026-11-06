from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.analysis.benchmark import calculate_benchmark_risk


def history_from_returns(returns, start="2023-01-02"):
    returns = np.asarray(returns, dtype=float)
    prices = 100.0 * np.cumprod(1.0 + returns)
    index = pd.bdate_range(start=start, periods=len(prices))
    return pd.DataFrame({"Close": prices}, index=index)


def make_analyzed(symbol, quantity, market_value_eur, history):
    direction = "LONG" if quantity > 0 else "SHORT" if quantity < 0 else "FLAT"
    return SimpleNamespace(
        yahoo_symbol=symbol,
        position=SimpleNamespace(
            quantity=quantity,
            market_value_eur=market_value_eur,
            direction=direction,
            name=symbol,
        ),
        history=history,
    )


def test_single_asset_beta_recovers_known_factor_loading():
    rng = np.random.default_rng(240)
    market = rng.normal(0.0003, 0.01, 800)
    asset = 1.5 * market + rng.normal(0.0, 0.002, 800)

    positions = [make_analyzed("A", 10, 100_000, history_from_returns(asset))]
    benchmark = history_from_returns(market)

    risk = calculate_benchmark_risk(
        positions,
        "SPY",
        benchmark,
        min_observations=500,
        max_observations=756,
    )

    assert risk.portfolio_beta == pytest.approx(1.5, abs=0.05)
    assert risk.beta_from_components == pytest.approx(risk.portfolio_beta, abs=1e-10)
    assert risk.r_squared > 0.9


def test_short_positive_beta_reduces_portfolio_beta():
    rng = np.random.default_rng(241)
    market = rng.normal(0.0002, 0.012, 800)
    long_asset = 1.2 * market + rng.normal(0.0, 0.002, 800)
    hedge_asset = 1.0 * market + rng.normal(0.0, 0.002, 800)

    hedged = [
        make_analyzed("LONG", 10, 70_000, history_from_returns(long_asset)),
        make_analyzed("HEDGE", -10, 30_000, history_from_returns(hedge_asset)),
    ]
    unhedged = [
        make_analyzed("LONG", 10, 70_000, history_from_returns(long_asset)),
        make_analyzed("HEDGE", 10, 30_000, history_from_returns(hedge_asset)),
    ]
    benchmark = history_from_returns(market)

    h = calculate_benchmark_risk(hedged, "SPY", benchmark, 500, 756)
    u = calculate_benchmark_risk(unhedged, "SPY", benchmark, 500, 756)

    assert h.portfolio_beta < u.portfolio_beta

    hedge_metric = next(x for x in h.asset_metrics if x.yahoo_symbol == "HEDGE")
    assert hedge_metric.direction == "SHORT"
    assert hedge_metric.beta > 0
    assert hedge_metric.beta_contribution < 0


def test_component_beta_sums_to_portfolio_beta():
    rng = np.random.default_rng(242)
    market = rng.normal(0.0002, 0.01, 800)
    a = 1.3 * market + rng.normal(0.0, 0.004, 800)
    b = 0.7 * market + rng.normal(0.0, 0.005, 800)

    positions = [
        make_analyzed("A", 10, 60_000, history_from_returns(a)),
        make_analyzed("B", 10, 40_000, history_from_returns(b)),
    ]
    benchmark = history_from_returns(market)

    risk = calculate_benchmark_risk(positions, "SPY", benchmark, 500, 756)

    assert sum(x.beta_contribution for x in risk.asset_metrics) == pytest.approx(
        risk.portfolio_beta, abs=1e-10
    )


def test_short_history_asset_is_excluded_and_coverage_reported():
    rng = np.random.default_rng(243)
    market = rng.normal(0.0, 0.01, 800)
    a = 1.0 * market + rng.normal(0.0, 0.003, 800)
    young = rng.normal(0.0, 0.02, 200)

    positions = [
        make_analyzed("A", 10, 80_000, history_from_returns(a)),
        make_analyzed("YOUNG", 10, 20_000, history_from_returns(young)),
    ]

    risk = calculate_benchmark_risk(
        positions,
        "SPY",
        history_from_returns(market),
        min_observations=500,
        max_observations=756,
    )

    assert risk.risk_coverage_pct == pytest.approx(80.0)
    assert risk.excluded_symbols == ("YOUNG",)

def test_common_history_conflict_excludes_minimum_asset_and_reports_coverage():
    rng = np.random.default_rng(244)

    market = rng.normal(0.0002, 0.01, 100)
    a = 1.0 * market + rng.normal(0.0, 0.003, 100)
    b = 0.8 * market + rng.normal(0.0, 0.004, 100)
    bad = 1.2 * market + rng.normal(0.0, 0.005, 100)

    history_a = history_from_returns(a, start="2025-01-01")
    history_b = history_from_returns(b, start="2025-01-01")
    history_bad = history_from_returns(bad, start="2025-01-01")
    benchmark = history_from_returns(market, start="2025-01-01")

    bad_index = history_bad.index.to_series().copy()
    bad_index.iloc[:50] = (
        bad_index.iloc[:50] + pd.Timedelta(days=365)
    )

    history_bad = history_bad.copy()
    history_bad.index = pd.DatetimeIndex(bad_index.values)

    positions = [
        make_analyzed("A", 10, 40_000, history_a),
        make_analyzed("B", 10, 40_000, history_b),
        make_analyzed("BAD", 10, 20_000, history_bad),
    ]

    risk = calculate_benchmark_risk(
        positions,
        "SPY",
        benchmark,
        min_observations=60,
        max_observations=None,
    )

    assert risk.assets == 2
    assert risk.observations >= 60
    assert risk.excluded_symbols == ("BAD",)
    assert risk.risk_coverage_pct == pytest.approx(80.0)
