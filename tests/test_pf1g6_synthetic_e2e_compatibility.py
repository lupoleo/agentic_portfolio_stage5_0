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
    OpportunityStatus,
    PortfolioRiskState,
    PortfolioRiskStateRecord,
    PortfolioSnapshot,
    RiskConstraints,
    TradeOpportunity,
    TradeProposal,
    TradingHorizon,
)
from app.cio.portfolio_simulation_service import PortfolioSimulationService
from app.cio.portfolio_simulator import PortfolioRiskSimulator
from app.cio.storage import Stage3Store


NOW = datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc)


def _metric(before: float, after: float):
    return SimpleNamespace(
        before=before,
        after=after,
        delta=after - before,
    )


def _marginal_result(
    *,
    beta_after: float = 0.70,
):
    return SimpleNamespace(
        volatility_pct=_metric(15.0, 14.2),
        beta=_metric(0.80, beta_after),
        var_95_1d_eur=_metric(4_000.0, 3_700.0),
        cvar_95_1d_eur=_metric(6_000.0, 5_500.0),
        analytical_coverage_before_pct=90.0,
        analytical_coverage_after_pct=92.0,
        top5_concentration_before_pct=50.0,
        top5_concentration_after_pct=47.0,
        effective_positions_before=8.0,
        effective_positions_after=8.8,
    )


class SyntheticMarginalRiskAdapter:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def assess(self, *, snapshot, proposal):
        self.calls.append((snapshot.snapshot_id, proposal.proposal_id))
        return self.result


def _install_chain(
    tmp_path,
    *,
    beta_limit: float | None = 0.75,
):
    store = Stage3Store(tmp_path / "pf1g6.sqlite")

    account = AccountState(
        account_state_id="ACC-PF1G6",
        timestamp=NOW,
        account_equity_eur=250_000.0,
        cash=[
            CurrencyCash(
                currency=Currency.EUR,
                available=50_000.0,
                reserve=1_000.0,
            ),
            CurrencyCash(
                currency=Currency.USD,
                available=100.0,
                reserve=0.0,
            ),
        ],
        constraints=RiskConstraints(
            max_portfolio_beta=beta_limit,
            max_var_95_1d_eur=5_000.0,
        ),
        source=DataSource.OPERATOR,
    )
    store.save_account_state(account)

    snapshot = PortfolioSnapshot(
        snapshot_id="SNAP-PF1G6",
        timestamp=NOW,
        source_file="synthetic-portfolio.xlsx",
        source_file_hash="pf1g6-synthetic",
        quant_engine_version="2.5",
        analyzed_positions=8,
        gross_exposure_eur=100_000.0,
        net_exposure_eur=80_000.0,
        account_state_id=account.account_state_id,
    )
    store.save_portfolio_snapshot(snapshot)

    before = PortfolioRiskState(
        gross_exposure_eur=100_000.0,
        net_exposure_eur=80_000.0,
        long_exposure_eur=90_000.0,
        short_exposure_eur=10_000.0,
        portfolio_volatility_pct=15.0,
        portfolio_beta=0.80,
        var_95_1d_eur=4_000.0,
        cvar_95_1d_eur=6_000.0,
        top5_concentration_pct=50.0,
        effective_positions=8.0,
        analytical_coverage_pct=90.0,
    )
    store.save_portfolio_risk_state(
        PortfolioRiskStateRecord(
            risk_state_id="RISK-PF1G6",
            snapshot_id=snapshot.snapshot_id,
            created_at=NOW,
            state=before,
        )
    )

    opportunity = TradeOpportunity(
        opportunity_id="OPP-PF1G6",
        snapshot_id=snapshot.snapshot_id,
        created_at=NOW,
        ticker="QCOM",
        direction=Direction.LONG,
        horizon=TradingHorizon.SWING,
        confidence=0.80,
        target_exposure_eur=10_000.0,
        max_intended_loss_eur=1_000.0,
        thesis="PF-1G.6 synthetic acceptance opportunity.",
        status=OpportunityStatus.READY_FOR_PROPOSAL,
    )
    store.save_trade_opportunity(opportunity)

    proposal = TradeProposal(
        proposal_id="PROP-PF1G6",
        opportunity_id=opportunity.opportunity_id,
        snapshot_id=snapshot.snapshot_id,
        created_at=NOW,
        ticker="QCOM",
        direction=Direction.LONG,
        instrument_id="FIN-QCOM-PF1G6",
        sizing_id="SIZE-PF1G6",
        execution_side=ExecutionSide.BUY,
        quantity=100.0,
        reference_price=100.0,
        currency=Currency.EUR,
        fx_to_eur=1.0,
        entry_type="MARKET",
        gross_exposure_eur=10_000.0,
        estimated_capital_required_eur=10_000.0,
        estimated_max_loss_eur=1_000.0,
    )
    store.save_trade_proposal(proposal)

    return store, snapshot, before, opportunity, proposal


def test_pf1g6_synthetic_v2_service_e2e_persists_real_simulation(tmp_path):
    store, snapshot, before, opportunity, proposal = _install_chain(tmp_path)

    adapter = SyntheticMarginalRiskAdapter(
        _marginal_result(beta_after=0.70)
    )
    simulator = PortfolioRiskSimulator(
        marginal_risk_adapter=adapter
    )
    service = PortfolioSimulationService(
        store,
        simulator=simulator,
    )

    simulation = service.create_simulation(
        opportunity.opportunity_id
    )

    assert adapter.calls == [
        (snapshot.snapshot_id, proposal.proposal_id)
    ]

    # Exact proposal exposure mechanics.
    assert simulation.after.gross_exposure_eur == pytest.approx(110_000.0)
    assert simulation.after.net_exposure_eur == pytest.approx(90_000.0)
    assert simulation.after.long_exposure_eur == pytest.approx(100_000.0)
    assert simulation.after.short_exposure_eur == pytest.approx(10_000.0)

    # Shared marginal-risk V2 metrics.
    assert simulation.after.portfolio_volatility_pct == pytest.approx(14.2)
    assert simulation.after.portfolio_beta == pytest.approx(0.70)
    assert simulation.after.var_95_1d_eur == pytest.approx(3_700.0)
    assert simulation.after.cvar_95_1d_eur == pytest.approx(5_500.0)
    assert simulation.after.top5_concentration_pct == pytest.approx(47.0)
    assert simulation.after.effective_positions == pytest.approx(8.8)
    assert simulation.after.analytical_coverage_pct == pytest.approx(92.0)

    # Decision-time constraints are enforced on projected AFTER metrics.
    assert simulation.constraints_passed is True
    assert simulation.violated_constraints == []

    # EUR BUY cash projection remains exact V1/V2 shared behavior.
    assert simulation.cash_after_eur == pytest.approx(40_000.0)

    # Service persists the same canonical artifact.
    persisted = store.get_latest_portfolio_simulation(
        proposal.proposal_id
    )
    assert persisted == simulation


def test_pf1g6_synthetic_v2_hard_beta_breach_is_persisted(tmp_path):
    store, _, _, opportunity, proposal = _install_chain(tmp_path)

    adapter = SyntheticMarginalRiskAdapter(
        _marginal_result(beta_after=0.82)
    )
    service = PortfolioSimulationService(
        store,
        simulator=PortfolioRiskSimulator(
            marginal_risk_adapter=adapter
        ),
    )

    simulation = service.create_simulation(
        opportunity.opportunity_id
    )

    assert simulation.constraints_passed is False
    assert any(
        "Max portfolio beta constraint exceeded"
        in item
        for item in simulation.violated_constraints
    )

    persisted = store.get_latest_portfolio_simulation(
        proposal.proposal_id
    )
    assert persisted == simulation
    assert persisted.constraints_passed is False


def test_pf1g6_simulation_never_mutates_persisted_before_state(tmp_path):
    store, snapshot, before, opportunity, _ = _install_chain(tmp_path)

    service = PortfolioSimulationService(
        store,
        simulator=PortfolioRiskSimulator(
            marginal_risk_adapter=SyntheticMarginalRiskAdapter(
                _marginal_result()
            )
        ),
    )

    service.create_simulation(opportunity.opportunity_id)

    snapshot_after = store.get_portfolio_snapshot(
        snapshot.snapshot_id
    )
    risk_record_after = store.get_latest_portfolio_risk_state(
        snapshot.snapshot_id
    )

    assert snapshot_after == snapshot
    assert risk_record_after is not None
    assert risk_record_after.state == before


def test_pf1g6_direct_service_remains_v1_compatible(tmp_path):
    store, _, before, opportunity, proposal = _install_chain(tmp_path)

    # No injected adapter = frozen direct/V1 compatibility boundary.
    service = PortfolioSimulationService(store)

    simulation = service.create_simulation(
        opportunity.opportunity_id
    )

    assert service.simulator.marginal_risk_adapter is None

    assert (
        simulation.after.portfolio_volatility_pct
        == before.portfolio_volatility_pct
    )
    assert simulation.after.portfolio_beta == before.portfolio_beta
    assert simulation.after.var_95_1d_eur == before.var_95_1d_eur
    assert simulation.after.cvar_95_1d_eur == before.cvar_95_1d_eur
    assert (
        simulation.after.top5_concentration_pct
        == before.top5_concentration_pct
    )
    assert (
        simulation.after.effective_positions
        == before.effective_positions
    )

    warning_text = " ".join(simulation.warnings).lower()

    assert "volatility after is not yet" in warning_text
    assert "beta after is not yet" in warning_text
    assert "var and cvar after are not yet" in warning_text
    assert "concentration after is not yet" in warning_text

    # Configured beta limit is UNKNOWN on the legacy path, not manufactured
    # into a false hard breach using persisted BEFORE.
    assert simulation.constraints_passed is True
    assert not any(
        "Max portfolio beta constraint exceeded"
        in item
        for item in simulation.violated_constraints
    )

    persisted = store.get_latest_portfolio_simulation(
        proposal.proposal_id
    )
    assert persisted == simulation


def test_pf1g6_v2_and_v1_preserve_same_provenance_contract(tmp_path):
    store, snapshot, _, opportunity, proposal = _install_chain(tmp_path)

    simulation = PortfolioSimulationService(
        store,
        simulator=PortfolioRiskSimulator(
            marginal_risk_adapter=SyntheticMarginalRiskAdapter(
                _marginal_result()
            )
        ),
    ).create_simulation(opportunity.opportunity_id)

    assert simulation.snapshot_id == snapshot.snapshot_id
    assert simulation.proposal_id == proposal.proposal_id
    assert simulation.before.gross_exposure_eur == snapshot.gross_exposure_eur
    assert simulation.before.net_exposure_eur == snapshot.net_exposure_eur
