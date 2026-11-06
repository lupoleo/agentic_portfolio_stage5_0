from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.cio.models import Direction
from app.cio.portfolio_marginal_risk_provider import (
    CanonicalPortfolioMarginalRiskInputProvider,
    build_canonical_portfolio_filter_service,
)
from app.cio.portfolio_filter_service import PortfolioFilterService


NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def history(start="2024-01-01", periods=700):
    index = pd.bdate_range(start, periods=periods)
    return pd.DataFrame(
        {"Close": [100.0 + i * 0.1 for i in range(periods)]},
        index=index,
    )


def fake_position(name, direction="LONG", value=100_000):
    return SimpleNamespace(
        name=name,
        direction=direction,
        market_value_eur=value,
    )


def fake_snapshot(tmp_path):
    source = tmp_path / "portfolio.xlsx"
    source.write_bytes(b"placeholder")
    return SimpleNamespace(
        snapshot_id="SNAP-1",
        source_file=str(source),
        source_file_hash="pre-report-hash",
        timestamp=NOW,
    )


def fake_opportunity():
    return SimpleNamespace(
        snapshot_id="SNAP-1",
        ticker="CAND",
        direction=Direction.LONG,
    )


def test_provider_builds_real_inputs_and_cuts_all_history_at_snapshot(
    tmp_path, monkeypatch
):
    snapshot = fake_snapshot(tmp_path)
    opportunity = fake_opportunity()

    positions = [
        fake_position("A"),
        fake_position("B", direction="SHORT", value=50_000),
    ]

    monkeypatch.setattr(
        "app.cio.portfolio_marginal_risk_provider.load_fineco_positions",
        lambda path: positions,
    )
    monkeypatch.setattr(
        "app.cio.portfolio_marginal_risk_provider.resolve_yahoo_symbol",
        lambda p: {"A": "AAA", "B": "BBB"}[p.name],
    )

    future_history = history(periods=800)

    monkeypatch.setattr(
        "app.cio.portfolio_marginal_risk_provider.download_price_history",
        lambda symbols, period: {
            "AAA": future_history,
            "BBB": future_history,
        },
    )
    monkeypatch.setattr(
        "app.cio.portfolio_marginal_risk_provider.get_price_history",
        lambda symbol, period: future_history,
    )

    inputs = CanonicalPortfolioMarginalRiskInputProvider()(
        opportunity, snapshot
    )

    assert [x.yahoo_symbol for x in inputs.analyzed_positions] == ["AAA", "BBB"]
    assert inputs.candidate_symbol == "CAND"

    cutoff = pd.Timestamp(NOW).tz_localize(None)
    assert inputs.candidate_history.index.max() <= cutoff
    assert inputs.benchmark_history.index.max() <= cutoff
    assert all(
        item.history.index.max() <= cutoff
        for item in inputs.analyzed_positions
    )


def test_existing_portfolio_factor_reuses_history_for_candidate(
    tmp_path, monkeypatch
):
    snapshot = fake_snapshot(tmp_path)
    opportunity = SimpleNamespace(
        snapshot_id="SNAP-1",
        ticker="AAA",
        direction=Direction.LONG,
    )
    positions = [fake_position("A")]

    monkeypatch.setattr(
        "app.cio.portfolio_marginal_risk_provider.load_fineco_positions",
        lambda path: positions,
    )
    monkeypatch.setattr(
        "app.cio.portfolio_marginal_risk_provider.resolve_yahoo_symbol",
        lambda p: "AAA",
    )
    monkeypatch.setattr(
        "app.cio.portfolio_marginal_risk_provider.download_price_history",
        lambda symbols, period: {"AAA": history()},
    )

    calls = []

    def fake_get(symbol, period):
        calls.append(symbol)
        return history()

    monkeypatch.setattr(
        "app.cio.portfolio_marginal_risk_provider.get_price_history",
        fake_get,
    )

    inputs = CanonicalPortfolioMarginalRiskInputProvider()(
        opportunity, snapshot
    )

    assert inputs.candidate_history.equals(
        inputs.analyzed_positions[0].history
    )
    # Only benchmark should require the single-symbol getter.
    assert calls == ["SPY"]


def test_provider_rejects_snapshot_mismatch(tmp_path):
    snapshot = fake_snapshot(tmp_path)
    opportunity = SimpleNamespace(
        snapshot_id="OTHER",
        ticker="CAND",
        direction=Direction.LONG,
    )

    with pytest.raises(ValueError, match="snapshot_id does not match"):
        CanonicalPortfolioMarginalRiskInputProvider()(
            opportunity, snapshot
        )


def test_provider_rejects_missing_snapshot_source_file(tmp_path):
    snapshot = SimpleNamespace(
        snapshot_id="SNAP-1",
        source_file=str(tmp_path / "missing.xlsx"),
        source_file_hash="x",
        timestamp=NOW,
    )

    with pytest.raises(FileNotFoundError, match="does not exist"):
        CanonicalPortfolioMarginalRiskInputProvider()(
            fake_opportunity(), snapshot
        )


def test_factory_returns_operational_portfolio_filter_service(monkeypatch):
    store = SimpleNamespace()

    service = build_canonical_portfolio_filter_service(store)

    assert isinstance(service, PortfolioFilterService)
    assert service.marginal_risk_engine is not None
    assert service.marginal_risk_input_provider is not None
