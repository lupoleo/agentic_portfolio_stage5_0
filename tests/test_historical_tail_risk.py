from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.analysis.risk import calculate_historical_tail_risk


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


def test_var_and_cvar_are_positive_loss_measures_and_cvar_is_not_smaller():
    rng = np.random.default_rng(123)
    returns = rng.normal(0.0003, 0.015, 260)
    positions = [make_analyzed("A", 10, 100_000, history_from_returns(returns))]

    risk = calculate_historical_tail_risk(positions, min_observations=60)
    by_conf = {x.confidence_level_pct: x for x in risk.levels}

    assert by_conf[95.0].var_pct >= 0
    assert by_conf[95.0].cvar_pct >= by_conf[95.0].var_pct
    assert by_conf[99.0].var_pct >= by_conf[95.0].var_pct
    assert by_conf[99.0].cvar_pct >= by_conf[99.0].var_pct


def test_short_correlated_hedge_reduces_historical_var():
    rng = np.random.default_rng(7)
    base = rng.normal(0.0002, 0.014, 260)
    correlated = 0.97 * base + rng.normal(0.0, 0.0015, 260)

    hedged = [
        make_analyzed("A", 10, 70_000, history_from_returns(base)),
        make_analyzed("B", -10, 30_000, history_from_returns(correlated)),
    ]
    unhedged = [
        make_analyzed("A", 10, 70_000, history_from_returns(base)),
        make_analyzed("B", 10, 30_000, history_from_returns(correlated)),
    ]

    h = calculate_historical_tail_risk(hedged, min_observations=60)
    u = calculate_historical_tail_risk(unhedged, min_observations=60)

    h95 = {x.confidence_level_pct: x for x in h.levels}[95.0]
    u95 = {x.confidence_level_pct: x for x in u.levels}[95.0]
    assert h95.var_pct < u95.var_pct
    assert h95.cvar_pct < u95.cvar_pct


def test_eur_var_is_based_on_total_gross_exposure():
    returns = np.array([0.01, -0.02, 0.005, -0.01] * 40)
    positions = [make_analyzed("A", 10, 200_000, history_from_returns(returns))]

    risk = calculate_historical_tail_risk(
        positions, confidence_levels=(0.95,), min_observations=60
    )
    level = risk.levels[0]

    assert level.var_eur == pytest.approx(level.var_pct / 100 * 200_000)
    assert level.cvar_eur == pytest.approx(level.cvar_pct / 100 * 200_000)


def test_ten_day_horizon_uses_compounded_rolling_returns():
    rng = np.random.default_rng(88)
    returns = rng.normal(0.0001, 0.01, 260)
    positions = [make_analyzed("A", 10, 100_000, history_from_returns(returns))]

    one_day = calculate_historical_tail_risk(
        positions, horizon_days=1, min_observations=60
    )
    ten_day = calculate_historical_tail_risk(
        positions, horizon_days=10, min_observations=60
    )

    assert ten_day.horizon_days == 10
    assert ten_day.observations == one_day.observations - 9


def test_short_history_asset_is_excluded_and_coverage_reported():
    rng = np.random.default_rng(99)
    long_history = rng.normal(0.0002, 0.01, 180)
    short_history = rng.normal(0.0002, 0.02, 20)

    positions = [
        make_analyzed("A", 10, 80_000, history_from_returns(long_history)),
        make_analyzed("NEW", -10, 20_000, history_from_returns(short_history)),
    ]

    risk = calculate_historical_tail_risk(positions, min_observations=60)

    assert risk.assets == 1
    assert risk.risk_coverage_pct == pytest.approx(80.0)
    assert risk.excluded_symbols == ("NEW",)

def test_common_history_conflict_excludes_minimum_asset_and_reports_coverage():
    rng = np.random.default_rng(100)

    a = rng.normal(0.0002, 0.010, 100)
    b = rng.normal(0.0002, 0.012, 100)
    bad = rng.normal(0.0002, 0.015, 100)

    history_a = history_from_returns(a, start="2025-01-01")
    history_b = history_from_returns(b, start="2025-01-01")
    history_bad = history_from_returns(bad, start="2025-01-01")

    # BAD remains individually eligible, but half of its dates are shifted
    # outside the A/B calendar so the common sample falls below 60.
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

    risk = calculate_historical_tail_risk(
        positions,
        min_observations=60,
        horizon_days=1,
    )

    assert risk.assets == 2
    assert risk.observations >= 60
    assert risk.excluded_symbols == ("BAD",)
    assert risk.risk_coverage_pct == pytest.approx(80.0)
