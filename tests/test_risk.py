from types import SimpleNamespace

import pytest

from app.analysis.risk import calculate_portfolio_risk


def make_analyzed(
    symbol: str,
    quantity: float,
    market_value_eur: float,
    volatility: float,
    risk_score: float,
):
    direction = "LONG" if quantity > 0 else "SHORT" if quantity < 0 else "FLAT"

    return SimpleNamespace(
        yahoo_symbol=symbol,
        position=SimpleNamespace(
            quantity=quantity,
            market_value_eur=market_value_eur,
            direction=direction,
        ),
        technical=SimpleNamespace(
            volatility_20d_pct=volatility,
        ),
        scores=SimpleNamespace(
            risk_score=risk_score,
        ),
    )


def test_long_short_exposure_and_weights():
    positions = [
        make_analyzed("LONG1", 10, 60_000, 20, 30),
        # Deliberately POSITIVE market value for SHORT to verify that
        # direction is taken from quantity, not market-value sign.
        make_analyzed("SHORT1", -10, 40_000, 50, 70),
    ]

    risk = calculate_portfolio_risk(positions)

    assert risk.long_exposure_eur == pytest.approx(60_000)
    assert risk.short_exposure_eur == pytest.approx(40_000)
    assert risk.gross_exposure_eur == pytest.approx(100_000)
    assert risk.net_exposure_eur == pytest.approx(20_000)
    assert risk.net_to_gross_pct == pytest.approx(20.0)

    by_symbol = {m.yahoo_symbol: m for m in risk.position_metrics}

    assert by_symbol["LONG1"].gross_weight_pct == pytest.approx(60.0)
    assert by_symbol["LONG1"].net_weight_pct == pytest.approx(60.0)
    assert by_symbol["SHORT1"].gross_weight_pct == pytest.approx(40.0)
    assert by_symbol["SHORT1"].net_weight_pct == pytest.approx(-40.0)

    assert sum(m.gross_weight_pct for m in risk.position_metrics) == pytest.approx(100.0)
    assert sum(m.net_weight_pct for m in risk.position_metrics) == pytest.approx(20.0)


def test_short_negative_market_value_is_also_handled():
    positions = [
        make_analyzed("LONG1", 10, 50_000, 20, 30),
        make_analyzed("SHORT1", -10, -50_000, 40, 60),
    ]

    risk = calculate_portfolio_risk(positions)

    assert risk.long_exposure_eur == pytest.approx(50_000)
    assert risk.short_exposure_eur == pytest.approx(50_000)
    assert risk.gross_exposure_eur == pytest.approx(100_000)
    assert risk.net_exposure_eur == pytest.approx(0.0)
    assert risk.net_to_gross_pct == pytest.approx(0.0)


def test_concentration_uses_gross_exposure_not_net_exposure():
    positions = [
        make_analyzed("A", 10, 50_000, 20, 30),
        make_analyzed("B", -10, 30_000, 40, 60),
        make_analyzed("C", 10, 20_000, 30, 40),
    ]

    risk = calculate_portfolio_risk(positions)

    assert risk.largest_position_pct == pytest.approx(50.0)
    assert risk.top3_concentration_pct == pytest.approx(100.0)
    assert risk.hhi == pytest.approx(0.38)
    assert risk.effective_positions == pytest.approx(1 / 0.38)


def test_weighted_risk_metrics_are_gross_weighted():
    positions = [
        make_analyzed("LONG1", 10, 75_000, 20, 20),
        make_analyzed("SHORT1", -10, 25_000, 60, 80),
    ]

    risk = calculate_portfolio_risk(positions)

    # 75% * 20% + 25% * 60% = 30%
    assert risk.weighted_volatility_20d_pct == pytest.approx(30.0)

    # 75% * 20 + 25% * 80 = 35
    assert risk.weighted_technical_risk_score == pytest.approx(35.0)
