from dataclasses import replace
from datetime import date
import json

import pandas as pd
import pytest

from app.portfolio.instrument_reference import (
    InstrumentReferenceRegistry,
    enrich_position,
)
from app.portfolio.leveraged_history import (
    build_leveraged_proxy_history,
    leveraged_proxy_factor_id,
)
from app.portfolio.models import PortfolioPosition
from app.portfolio.ticker_resolver import resolve_yahoo_symbol


def position(**overrides):
    values = dict(
        name="Instrument", isin="", broker_symbol="UNKNOWN",
        market="VORVEL", instrument_type="Certificate", currency="EUR",
        quantity=100.0, average_price=2.0, load_exchange_rate=1.0,
        cost_value_eur=200.0, market_price=2.1, market_exchange_rate=1.0,
        market_value_eur=210.0, pnl_percent=5.0, pnl_eur=10.0,
        pnl_currency=10.0,
    )
    values.update(overrides)
    return PortfolioPosition(**values)


def registry():
    return InstrumentReferenceRegistry.load(
        "config/portfolio/instrument_references_v1.json"
    )


@pytest.mark.parametrize(
    "isin,symbol,direction",
    [
        ("IT0005686685", "DIA.MI", "LONG"),
        ("IT0005686883", "FCT.MI", "SHORT"),
        ("IT0005687683", "MONC.MI", "SHORT"),
        ("IT0005687667", "BZU.MI", "SHORT"),
        ("IT0005670135", "UCG.MI", "LONG"),  # operator review 2026-10-04
    ],
)
def test_reviewed_certificates_resolve_exactly(isin, symbol, direction):
    enriched = enrich_position(
        position(isin=isin), registry=registry(), as_of=date(2026, 9, 26)
    )
    assert enriched.market_data_symbol == symbol
    assert enriched.market_data_method == "LEVERAGED_PROXY"
    assert enriched.leverage_multiplier == 5.0
    assert enriched.direction == direction
    assert not enriched.history_is_real_product_price


def test_kering_cfd_uses_direct_underlying_without_second_leverage():
    enriched = enrich_position(
        position(broker_symbol="KERCFD.CFD", quantity=-10, market_value_eur=2234.34),
        registry=registry(), as_of=date(2026, 9, 26),
    )
    assert resolve_yahoo_symbol(enriched) == "KER.PA"
    assert enriched.market_data_method == "DIRECT"
    assert enriched.leverage_multiplier == 1.0
    assert enriched.market_value_eur == 2234.34
    assert enriched.direction == "SHORT"


def test_unknown_position_is_not_guessed_from_name():
    raw = position(name="LEVA FISSA DIASORIN LONG 5X", isin="UNKNOWN")
    assert enrich_position(raw, registry=registry()) == raw


def test_expired_reference_fails_closed():
    with pytest.raises(ValueError, match="expired"):
        enrich_position(
            position(isin="IT0005686685"),
            registry=registry(),
            as_of=date(2026, 10, 30),
        )


def test_unreviewed_reference_is_rejected(tmp_path):
    path = tmp_path / "references.json"
    path.write_text(json.dumps({
        "schema": "portfolio-instrument-references-v1",
        "references": [{
            "reference_id": "bad", "isin": "X", "broker_symbol": "",
            "market_data_symbol": "X", "market_data_method": "DIRECT",
            "economic_direction": "LONG", "leverage_multiplier": 1,
            "reviewed": False,
        }],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="Unreviewed"):
        InstrumentReferenceRegistry.load(path)


def history(returns):
    prices = [100.0]
    for value in returns:
        prices.append(prices[-1] * (1 + value))
    return pd.DataFrame(
        {"Close": prices, "Adj Close": prices, "Volume": 1000},
        index=pd.date_range("2026-01-01", periods=len(prices), freq="B"),
    )


def test_proxy_returns_are_five_times_underlying_and_not_directional():
    underlying = history([0.01, -0.02, 0.005])
    proxy = build_leveraged_proxy_history(underlying, 5.0)
    actual = proxy["Adj Close"].pct_change(fill_method=None).dropna()
    assert actual.tolist() == pytest.approx([-0.10, 0.025])
    assert proxy.attrs["direction_embedded"] is False


def test_proxy_total_loss_fails_closed():
    with pytest.raises(ValueError, match="total loss"):
        build_leveraged_proxy_history(history([-0.25]), 5.0)


def test_proxy_factor_is_unique_per_product():
    assert leveraged_proxy_factor_id("IT0005686685") == "PROXY:IT0005686685"
    assert leveraged_proxy_factor_id("IT0005686883") != "PROXY:IT0005686685"


def test_direction_override_does_not_change_accounting_exposure():
    raw = position(isin="IT0005686883", quantity=200, market_value_eur=1922)
    enriched = enrich_position(raw, registry=registry(), as_of=date(2026, 9, 26))
    assert enriched.direction == "SHORT"
    assert enriched.market_value_eur == 1922
    assert abs(enriched.market_value_eur) == abs(raw.market_value_eur)


def test_portfolio_analysis_uses_unique_proxy_factor_without_double_exposure(monkeypatch):
    from app.analysis.portfolio import analyze_portfolio

    raw = position(isin="IT0005686883", quantity=200, market_value_eur=1922)
    enriched = enrich_position(raw, registry=registry(), as_of=date(2026, 9, 26))
    prices = pd.Series(
        [100 + i * 0.1 for i in range(80)],
        index=pd.date_range("2026-01-01", periods=80, freq="B"),
    )
    frame = pd.DataFrame({
        "Open": prices, "High": prices, "Low": prices,
        "Close": prices, "Adj Close": prices, "Volume": 1000,
    })
    monkeypatch.setattr(
        "app.analysis.portfolio.load_fineco_positions", lambda _: [enriched]
    )
    monkeypatch.setattr(
        "app.analysis.portfolio.download_price_history",
        lambda symbols, period: {"FCT.MI": frame},
    )
    result = analyze_portfolio("ignored.xlsx")
    assert len(result) == 1
    assert result[0].yahoo_symbol == "PROXY:IT0005686883"
    assert result[0].market_data_symbol == "FCT.MI"
    assert result[0].position.market_value_eur == 1922
    assert result[0].position.direction == "SHORT"
    assert not result[0].price_gap_comparable
