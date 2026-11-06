from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.cio.models import (
    AccountState,
    Currency,
    CurrencyCash,
    DataSource,
    Direction,
    ExecutionSide,
    PortfolioRiskState,
    PortfolioSnapshot,
    RiskConstraints,
    TradeProposal,
)
from app.cio.portfolio_simulator import PortfolioRiskSimulator


NOW = datetime(2026, 9, 13, 18, 30, tzinfo=timezone.utc)


def _snapshot():
    return PortfolioSnapshot(
        snapshot_id="SNAP-PF1G2",
        timestamp=NOW,
        source_file="data/input/portafoglio-export.xlsx",
        source_file_hash="PF1G2",
        quant_engine_version="2.5",
        analyzed_positions=33,
        gross_exposure_eur=100_000.0,
        net_exposure_eur=90_000.0,
        account_state_id="ACC-PF1G2",
    )


def _before():
    return PortfolioRiskState(
        gross_exposure_eur=100_000.0,
        net_exposure_eur=90_000.0,
        long_exposure_eur=95_000.0,
        short_exposure_eur=5_000.0,
        portfolio_volatility_pct=15.0,
        portfolio_beta=0.80,
        var_95_1d_eur=4_000.0,
        cvar_95_1d_eur=6_000.0,
        top5_concentration_pct=40.0,
        effective_positions=18.0,
        analytical_coverage_pct=90.0,
    )


def _proposal():
    return TradeProposal(
        proposal_id="PROP-PF1G2",
        opportunity_id="OPP-PF1G2",
        snapshot_id="SNAP-PF1G2",
        created_at=NOW,
        ticker="QCOM",
        direction=Direction.SHORT,
        instrument_id="FIN-QCOM",
        sizing_id="SIZ-PF1G2",
        execution_side=ExecutionSide.SELL_SHORT,
        quantity=10,
        reference_price=100.0,
        currency=Currency.USD,
        entry_type="MARKET",
        gross_exposure_eur=10_000.0,
    )


def _account():
    return AccountState(
        account_state_id="ACC-PF1G2",
        timestamp=NOW,
        cash=[
            CurrencyCash(currency=Currency.EUR, available=20_000.0, reserve=1_000.0),
            CurrencyCash(currency=Currency.USD, available=100.0, reserve=0.0),
        ],
        constraints=RiskConstraints(
            max_trade_loss_eur=None,
            max_position_weight_pct=None,
            max_portfolio_gross_exposure_pct=None,
            max_cio_deployable_pct=None,
            max_portfolio_beta=None,
            max_var_95_1d_eur=None,
        ),
        source=DataSource.OPERATOR,
    )


class RecordingAdapter:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def assess(self, *, snapshot, proposal):
        self.calls.append((snapshot, proposal))
        if self.error is not None:
            raise self.error
        return self.result


def _marginal_result():
    metric = lambda before, after: SimpleNamespace(
        before=before,
        after=after,
        delta=after - before,
    )
    return SimpleNamespace(
        volatility_pct=metric(15.0, 14.25),
        beta=metric(0.80, 0.71),
        var_95_1d_eur=metric(4_000.0, 3_750.0),
        cvar_95_1d_eur=metric(6_000.0, 5_550.0),
        analytical_coverage_before_pct=90.0,
        analytical_coverage_after_pct=92.5,
    )


def test_pf1g2_v2_consumes_shared_marginal_risk_result():
    adapter = RecordingAdapter(_marginal_result())
    simulator = PortfolioRiskSimulator(marginal_risk_adapter=adapter)

    snapshot = _snapshot()
    proposal = _proposal()
    before = _before()

    result = simulator.simulate(
        snapshot=snapshot,
        proposal=proposal,
        before=before,
        account_state=_account(),
    )

    assert adapter.calls == [(snapshot, proposal)]

    assert result.after.portfolio_volatility_pct == 14.25
    assert result.after.portfolio_beta == 0.71
    assert result.after.var_95_1d_eur == 3_750.0
    assert result.after.cvar_95_1d_eur == 5_550.0
    assert result.after.analytical_coverage_pct == 92.5

    assert result.delta.portfolio_volatility_pct == pytest.approx(-0.75)
    assert result.delta.portfolio_beta == pytest.approx(-0.09)
    assert result.delta.var_95_1d_eur == pytest.approx(-250.0)
    assert result.delta.cvar_95_1d_eur == pytest.approx(-450.0)
    assert result.delta.analytical_coverage_pct == pytest.approx(2.5)

    warning_text = " ".join(result.warnings).lower()
    assert "volatility after is not yet" not in warning_text
    assert "beta after is not yet" not in warning_text
    assert "var and cvar after are not yet" not in warning_text

    # Explicitly still deferred to PF-1G.4.
    assert result.after.top5_concentration_pct == before.top5_concentration_pct
    assert result.after.effective_positions == before.effective_positions
    assert "concentration after is not yet" in warning_text


def test_pf1g2_configured_adapter_failure_is_fail_closed():
    adapter = RecordingAdapter(error=RuntimeError("market data unavailable"))
    simulator = PortfolioRiskSimulator(marginal_risk_adapter=adapter)

    with pytest.raises(RuntimeError, match="market data unavailable"):
        simulator.simulate(
            snapshot=_snapshot(),
            proposal=_proposal(),
            before=_before(),
            account_state=_account(),
        )


def test_pf1g2_unconfigured_simulator_preserves_v1_quant_semantics():
    simulator = PortfolioRiskSimulator()
    before = _before()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_proposal(),
        before=before,
        account_state=_account(),
    )

    assert result.after.portfolio_volatility_pct == before.portfolio_volatility_pct
    assert result.after.portfolio_beta == before.portfolio_beta
    assert result.after.var_95_1d_eur == before.var_95_1d_eur
    assert result.after.cvar_95_1d_eur == before.cvar_95_1d_eur
    assert result.after.analytical_coverage_pct == before.analytical_coverage_pct

    warning_text = " ".join(result.warnings).lower()
    assert "volatility after is not yet" in warning_text
    assert "beta after is not yet" in warning_text
    assert "var and cvar after are not yet" in warning_text
