from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.analysis.risk import (
    calculate_covariance_risk,
    calculate_historical_tail_risk,
)


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
        ),
        history=history,
    )


def test_covariance_window_is_capped_to_requested_observations():
    rng = np.random.default_rng(231)
    a = rng.normal(0.0002, 0.012, 1000)
    b = rng.normal(0.0001, 0.010, 1000)

    positions = [
        make_analyzed("A", 10, 60_000, history_from_returns(a)),
        make_analyzed("B", -10, 40_000, history_from_returns(b)),
    ]

    risk = calculate_covariance_risk(
        positions,
        min_observations=100,
        max_observations=756,
    )

    assert risk.observations == 756


def test_historical_tail_risk_window_is_capped_for_1d_and_10d():
    rng = np.random.default_rng(232)
    a = rng.normal(0.0002, 0.012, 1000)
    positions = [
        make_analyzed("A", 10, 100_000, history_from_returns(a)),
    ]

    one_day = calculate_historical_tail_risk(
        positions,
        horizon_days=1,
        min_observations=100,
        max_observations=756,
    )
    ten_day = calculate_historical_tail_risk(
        positions,
        horizon_days=10,
        min_observations=100,
        max_observations=756,
    )

    assert one_day.observations == 756
    assert ten_day.observations == 756


def test_max_observations_cannot_be_smaller_than_minimum():
    rng = np.random.default_rng(233)
    a = rng.normal(0.0, 0.01, 300)
    positions = [
        make_analyzed("A", 10, 100_000, history_from_returns(a)),
    ]

    with pytest.raises(ValueError):
        calculate_covariance_risk(
            positions,
            min_observations=200,
            max_observations=100,
        )

    with pytest.raises(ValueError):
        calculate_historical_tail_risk(
            positions,
            min_observations=200,
            max_observations=100,
        )
