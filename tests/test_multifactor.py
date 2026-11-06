from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.analysis.multifactor import calculate_multifactor_risk


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


def factor_histories(factors):
    return {
        symbol: history_from_returns(values)
        for symbol, values in factors.items()
    }


def test_multifactor_recovers_known_portfolio_betas():
    rng = np.random.default_rng(250)

    f1 = rng.normal(0.0002, 0.010, 900)
    f2 = rng.normal(0.0001, 0.008, 900)
    f3 = rng.normal(0.0000, 0.007, 900)

    asset = (
        1.20 * f1
        + 0.45 * f2
        - 0.25 * f3
        + rng.normal(0.0, 0.001, 900)
    )

    positions = [
        make_analyzed("A", 10, 100_000, history_from_returns(asset))
    ]

    definitions = {
        "US": "F1",
        "EU": "F2",
        "GOLD": "F3",
    }

    risk = calculate_multifactor_risk(
        positions,
        factor_definitions=definitions,
        factor_histories=factor_histories(
            {"F1": f1, "F2": f2, "F3": f3}
        ),
        min_observations=500,
        max_observations=756,
    )

    betas = {
        metric.name: metric.beta
        for metric in risk.factor_metrics
    }

    assert betas["US"] == pytest.approx(1.20, abs=0.03)
    assert betas["EU"] == pytest.approx(0.45, abs=0.03)
    assert betas["GOLD"] == pytest.approx(-0.25, abs=0.03)
    assert risk.r_squared > 0.98


def test_factor_beta_contributions_sum_to_portfolio_betas():
    rng = np.random.default_rng(251)

    f1 = rng.normal(0.0, 0.010, 850)
    f2 = rng.normal(0.0, 0.009, 850)

    a = 1.4 * f1 + 0.2 * f2 + rng.normal(0.0, 0.002, 850)
    b = 0.5 * f1 + 1.1 * f2 + rng.normal(0.0, 0.002, 850)

    positions = [
        make_analyzed("A", 10, 60_000, history_from_returns(a)),
        make_analyzed("B", 10, 40_000, history_from_returns(b)),
    ]

    risk = calculate_multifactor_risk(
        positions,
        factor_definitions={"F1": "M1", "F2": "M2"},
        factor_histories=factor_histories({"M1": f1, "M2": f2}),
        min_observations=500,
        max_observations=756,
    )

    for metric in risk.factor_metrics:
        assert metric.beta_from_assets == pytest.approx(
            metric.beta, abs=1e-10
        )


def test_short_position_can_reduce_factor_exposure():
    rng = np.random.default_rng(252)

    market = rng.normal(0.0, 0.010, 850)
    gold = rng.normal(0.0, 0.008, 850)

    long_asset = (
        1.30 * market
        + 0.10 * gold
        + rng.normal(0.0, 0.002, 850)
    )
    hedge_asset = (
        1.00 * market
        + rng.normal(0.0, 0.002, 850)
    )

    definitions = {
        "US": "MKT",
        "GOLD": "GLD",
    }
    histories = factor_histories(
        {"MKT": market, "GLD": gold}
    )

    hedged = [
        make_analyzed("LONG", 10, 70_000, history_from_returns(long_asset)),
        make_analyzed("HEDGE", -10, 30_000, history_from_returns(hedge_asset)),
    ]

    unhedged = [
        make_analyzed("LONG", 10, 70_000, history_from_returns(long_asset)),
        make_analyzed("HEDGE", 10, 30_000, history_from_returns(hedge_asset)),
    ]

    h = calculate_multifactor_risk(
        hedged, definitions, histories, 500, 756
    )
    u = calculate_multifactor_risk(
        unhedged, definitions, histories, 500, 756
    )

    h_us = next(m for m in h.factor_metrics if m.name == "US")
    u_us = next(m for m in u.factor_metrics if m.name == "US")

    assert h_us.beta < u_us.beta

    hedge_row = next(
        row for row in h.asset_metrics
        if row.yahoo_symbol == "HEDGE"
    )

    us_index = h.factor_names.index("US")

    assert hedge_row.direction == "SHORT"
    assert hedge_row.factor_beta_contributions[us_index] < 0


def test_highly_correlated_factors_produce_high_vif():
    rng = np.random.default_rng(253)

    f1 = rng.normal(0.0, 0.01, 850)
    f2 = 0.98 * f1 + rng.normal(0.0, 0.001, 850)
    asset = 1.0 * f1 + rng.normal(0.0, 0.003, 850)

    positions = [
        make_analyzed("A", 10, 100_000, history_from_returns(asset))
    ]

    risk = calculate_multifactor_risk(
        positions,
        factor_definitions={"A": "F1", "B": "F2"},
        factor_histories=factor_histories({"F1": f1, "F2": f2}),
        min_observations=500,
        max_observations=756,
    )

    assert max(m.vif for m in risk.factor_metrics) > 10
    assert risk.condition_number > 5


def test_short_history_asset_is_excluded_and_coverage_reported():
    rng = np.random.default_rng(254)

    f1 = rng.normal(0.0, 0.010, 850)
    f2 = rng.normal(0.0, 0.008, 850)

    mature = (
        0.8 * f1 + 0.3 * f2
        + rng.normal(0.0, 0.003, 850)
    )
    young = rng.normal(0.0, 0.02, 200)

    positions = [
        make_analyzed("MATURE", 10, 80_000, history_from_returns(mature)),
        make_analyzed("YOUNG", 10, 20_000, history_from_returns(young)),
    ]

    risk = calculate_multifactor_risk(
        positions,
        factor_definitions={"F1": "M1", "F2": "M2"},
        factor_histories=factor_histories({"M1": f1, "M2": f2}),
        min_observations=500,
        max_observations=756,
    )

    assert risk.risk_coverage_pct == pytest.approx(80.0)
    assert risk.excluded_symbols == ("YOUNG",)

def test_common_history_conflict_excludes_minimum_asset_and_preserves_factors():
    rng = np.random.default_rng(255)

    f1 = rng.normal(0.0002, 0.010, 100)
    f2 = rng.normal(0.0001, 0.008, 100)

    a = 1.0 * f1 + 0.2 * f2 + rng.normal(0.0, 0.003, 100)
    b = 0.7 * f1 + 0.5 * f2 + rng.normal(0.0, 0.003, 100)
    bad = 1.2 * f1 + rng.normal(0.0, 0.004, 100)

    history_a = history_from_returns(a, start="2025-01-01")
    history_b = history_from_returns(b, start="2025-01-01")
    history_bad = history_from_returns(bad, start="2025-01-01")

    histories = {
        "M1": history_from_returns(
            f1,
            start="2025-01-01",
        ),
        "M2": history_from_returns(
            f2,
            start="2025-01-01",
        ),
    }

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

    risk = calculate_multifactor_risk(
        positions,
        factor_definitions={"F1": "M1", "F2": "M2"},
        factor_histories=histories,
        min_observations=60,
        max_observations=None,
    )

    assert risk.assets == 2
    assert risk.observations >= 60
    assert risk.excluded_symbols == ("BAD",)
    assert risk.risk_coverage_pct == pytest.approx(80.0)
    assert risk.factor_names == ("F1", "F2")
    assert risk.factor_symbols == ("M1", "M2")
