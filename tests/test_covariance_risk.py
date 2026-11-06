from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.analysis.risk import calculate_covariance_risk


def history_from_returns(returns, start="2025-01-01"):
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
        ),
        history=history,
    )


def test_component_risk_sums_to_portfolio_volatility():
    rng = np.random.default_rng(42)
    base = rng.normal(0.0004, 0.012, 180)
    other = 0.55 * base + rng.normal(0.0002, 0.009, 180)

    positions = [
        make_analyzed("A", 10, 70_000, history_from_returns(base)),
        make_analyzed("B", 10, 30_000, history_from_returns(other)),
    ]

    risk = calculate_covariance_risk(positions, min_observations=60)

    assert risk.observations >= 60
    assert risk.assets == 2
    assert sum(m.component_risk_pct_points for m in risk.asset_metrics) == pytest.approx(
        risk.portfolio_volatility_pct, rel=1e-10, abs=1e-10
    )
    assert sum(m.risk_contribution_pct for m in risk.asset_metrics) == pytest.approx(
        100.0, rel=1e-10, abs=1e-10
    )


def test_short_positively_correlated_asset_reduces_portfolio_risk():
    rng = np.random.default_rng(7)
    base = rng.normal(0.0003, 0.013, 200)
    correlated = 0.95 * base + rng.normal(0.0, 0.002, 200)

    long_short = [
        make_analyzed("A", 10, 70_000, history_from_returns(base)),
        make_analyzed("B", -10, 30_000, history_from_returns(correlated)),
    ]
    long_long = [
        make_analyzed("A", 10, 70_000, history_from_returns(base)),
        make_analyzed("B", 10, 30_000, history_from_returns(correlated)),
    ]

    hedged = calculate_covariance_risk(long_short, min_observations=60)
    unhedged = calculate_covariance_risk(long_long, min_observations=60)

    assert hedged.portfolio_volatility_pct < unhedged.portfolio_volatility_pct

    by_symbol = {m.yahoo_symbol: m for m in hedged.asset_metrics}
    assert by_symbol["B"].signed_weight_pct == pytest.approx(-30.0)
    assert by_symbol["B"].component_risk_pct_points < 0
    assert by_symbol["B"].risk_contribution_pct < 0


def test_duplicate_symbol_positions_are_aggregated_before_covariance():
    rng = np.random.default_rng(11)
    a = rng.normal(0.0002, 0.01, 160)
    b = rng.normal(0.0002, 0.014, 160)

    positions = [
        make_analyzed("A", 10, 60_000, history_from_returns(a)),
        make_analyzed("A", -5, 20_000, history_from_returns(a)),
        make_analyzed("B", 10, 20_000, history_from_returns(b)),
    ]

    risk = calculate_covariance_risk(positions, min_observations=60)

    assert risk.assets == 2
    by_symbol = {m.yahoo_symbol: m for m in risk.asset_metrics}

    # Gross portfolio exposure = 100k; A net signed exposure = +40k.
    assert by_symbol["A"].gross_exposure_eur == pytest.approx(80_000)
    assert by_symbol["A"].signed_exposure_eur == pytest.approx(40_000)
    assert by_symbol["A"].signed_weight_pct == pytest.approx(40.0)


def test_insufficient_common_history_is_rejected():
    returns = np.full(20, 0.001)
    positions = [
        make_analyzed("A", 10, 50_000, history_from_returns(returns)),
        make_analyzed("B", -10, 50_000, history_from_returns(returns)),
    ]

    with pytest.raises(ValueError, match="No asset has enough usable price history"):
        calculate_covariance_risk(positions, min_observations=60)


def test_short_history_asset_is_excluded_and_coverage_reported():
    rng = np.random.default_rng(99)
    long_history = rng.normal(0.0002, 0.01, 160)
    short_history = rng.normal(0.0002, 0.02, 20)

    positions = [
        make_analyzed("A", 10, 80_000, history_from_returns(long_history)),
        make_analyzed("NEW", -10, 20_000, history_from_returns(short_history)),
    ]

    risk = calculate_covariance_risk(positions, min_observations=60)

    assert risk.assets == 1
    assert risk.risk_coverage_pct == pytest.approx(80.0)
    assert risk.excluded_symbols == ("NEW",)

def test_common_history_conflict_excludes_minimum_asset_and_reports_coverage():
    rng = np.random.default_rng(123)

    returns_a = rng.normal(0.0002, 0.01, 100)
    returns_b = rng.normal(0.0002, 0.012, 100)
    returns_bad = rng.normal(0.0002, 0.015, 100)

    history_a = history_from_returns(
        returns_a,
        start="2025-01-01",
    )
    history_b = history_from_returns(
        returns_b,
        start="2025-01-01",
    )
    history_bad = history_from_returns(
        returns_bad,
        start="2025-01-01",
    )

    # BAD still has 100 prices individually, so it passes the individual
    # eligibility gate. However, move half of its dates outside the A/B
    # calendar so the common sample falls below 60 observations.
    bad_index = history_bad.index.to_series().copy()
    bad_index.iloc[:50] = bad_index.iloc[:50] + pd.Timedelta(days=365)
    history_bad = history_bad.copy()
    history_bad.index = pd.DatetimeIndex(bad_index.values)

    positions = [
        make_analyzed("A", 10, 40_000, history_a),
        make_analyzed("B", 10, 40_000, history_b),
        make_analyzed("BAD", 10, 20_000, history_bad),
    ]

    risk = calculate_covariance_risk(
        positions,
        min_observations=60,
    )

    assert risk.assets == 2
    assert risk.observations >= 60
    assert risk.excluded_symbols == ("BAD",)
    assert risk.risk_coverage_pct == pytest.approx(80.0)

    symbols = {
        metric.yahoo_symbol
        for metric in risk.asset_metrics
    }
    assert symbols == {"A", "B"}
