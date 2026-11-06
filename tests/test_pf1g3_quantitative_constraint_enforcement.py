from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

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


NOW = datetime(2026, 9, 13, 19, 0, tzinfo=timezone.utc)


def _snapshot():
    return PortfolioSnapshot(
        snapshot_id="SNAP-PF1G3",
        timestamp=NOW,
        source_file="data/input/portafoglio-export.xlsx",
        source_file_hash="PF1G3",
        quant_engine_version="2.5",
        analyzed_positions=33,
        gross_exposure_eur=100_000.0,
        net_exposure_eur=90_000.0,
        account_state_id="ACC-PF1G3",
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
        proposal_id="PROP-PF1G3",
        opportunity_id="OPP-PF1G3",
        snapshot_id="SNAP-PF1G3",
        created_at=NOW,
        ticker="QCOM",
        direction=Direction.SHORT,
        instrument_id="FIN-QCOM",
        sizing_id="SIZ-PF1G3",
        execution_side=ExecutionSide.SELL_SHORT,
        quantity=10,
        reference_price=100.0,
        currency=Currency.USD,
        entry_type="MARKET",
        gross_exposure_eur=10_000.0,
    )


def _account(*, max_beta=None, max_var=None):
    return AccountState(
        account_state_id="ACC-PF1G3",
        timestamp=NOW,
        cash=[
            CurrencyCash(
                currency=Currency.EUR,
                available=20_000.0,
                reserve=1_000.0,
            ),
            CurrencyCash(
                currency=Currency.USD,
                available=100.0,
                reserve=0.0,
            ),
        ],
        constraints=RiskConstraints(
            max_trade_loss_eur=None,
            max_position_weight_pct=None,
            max_portfolio_gross_exposure_pct=None,
            max_cio_deployable_pct=None,
            max_portfolio_beta=max_beta,
            max_var_95_1d_eur=max_var,
        ),
        source=DataSource.OPERATOR,
    )


def _metric(before, after):
    return SimpleNamespace(
        before=before,
        after=after,
        delta=after - before,
    )


def _result(*, beta_after=0.71, var_after=3750.0):
    return SimpleNamespace(
        volatility_pct=_metric(15.0, 14.25),
        beta=_metric(0.80, beta_after),
        var_95_1d_eur=_metric(4000.0, var_after),
        cvar_95_1d_eur=_metric(6000.0, 5550.0),
        analytical_coverage_before_pct=90.0,
        analytical_coverage_after_pct=92.5,
    )


class Adapter:
    def __init__(self, result):
        self.result = result

    def assess(self, *, snapshot, proposal):
        return self.result


def _simulate(*, result, max_beta=None, max_var=None):
    return PortfolioRiskSimulator(
        marginal_risk_adapter=Adapter(result)
    ).simulate(
        snapshot=_snapshot(),
        proposal=_proposal(),
        before=_before(),
        account_state=_account(
            max_beta=max_beta,
            max_var=max_var,
        ),
    )


def test_pf1g3_v2_passes_beta_and_var_constraints_on_after_values():
    simulation = _simulate(
        result=_result(beta_after=0.71, var_after=3750.0),
        max_beta=0.75,
        max_var=4000.0,
    )

    assert simulation.constraints_passed is True
    assert simulation.violated_constraints == []

    warning_text = " ".join(simulation.warnings).lower()
    assert "max portfolio beta cannot yet be enforced" not in warning_text
    assert "max var 95% 1d cannot yet be enforced" not in warning_text


def test_pf1g3_v2_beta_limit_is_enforced_against_after_not_before():
    # BEFORE beta=0.80 would fail 0.75, but projected AFTER=0.71 passes.
    simulation = _simulate(
        result=_result(beta_after=0.71, var_after=3750.0),
        max_beta=0.75,
    )

    assert simulation.constraints_passed is True
    assert not any(
        "Max portfolio beta constraint exceeded" in item
        for item in simulation.violated_constraints
    )


def test_pf1g3_v2_beta_breach_is_hard_violation():
    simulation = _simulate(
        result=_result(beta_after=0.82, var_after=3750.0),
        max_beta=0.75,
    )

    assert simulation.constraints_passed is False
    assert any(
        "Max portfolio beta constraint exceeded" in item
        and "0.8200 > 0.7500" in item
        for item in simulation.violated_constraints
    )


def test_pf1g3_v2_var_limit_is_enforced_against_after_not_delta():
    # AFTER VaR 4100 breaches 4000 even though the delta is only +100.
    simulation = _simulate(
        result=_result(beta_after=0.71, var_after=4100.0),
        max_var=4000.0,
    )

    assert simulation.constraints_passed is False
    assert any(
        "Max VaR 95% 1D constraint exceeded" in item
        and "€4,100.00 > €4,000.00" in item
        for item in simulation.violated_constraints
    )


def test_pf1g3_v2_exact_limit_is_pass():
    simulation = _simulate(
        result=_result(beta_after=0.75, var_after=4000.0),
        max_beta=0.75,
        max_var=4000.0,
    )

    assert simulation.constraints_passed is True
    assert simulation.violated_constraints == []


def test_pf1g3_v1_compatibility_keeps_constraints_unknown_as_warnings():
    simulation = PortfolioRiskSimulator().simulate(
        snapshot=_snapshot(),
        proposal=_proposal(),
        before=_before(),
        account_state=_account(
            max_beta=0.75,
            max_var=4000.0,
        ),
    )

    # V1 has no projected quantitative AFTER metrics. It must not fabricate
    # either compliance or a breach from persisted BEFORE values.
    assert simulation.constraints_passed is True

    warning_text = " ".join(simulation.warnings).lower()
    assert "max portfolio beta cannot yet be enforced" in warning_text
    assert "max var 95% 1d cannot yet be enforced" in warning_text
