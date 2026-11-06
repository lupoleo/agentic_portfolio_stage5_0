from __future__ import annotations

import pytest

from datetime import datetime, timezone

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
from app.cio.portfolio_simulator import (
    PortfolioRiskSimulator,
)


NOW = datetime(
    2026,
    8,
    19,
    10,
    0,
    tzinfo=timezone.utc,
)


# =============================================================
# Fixtures
# =============================================================


def _snapshot() -> PortfolioSnapshot:

    return PortfolioSnapshot(
        snapshot_id="SNAP-SIM-001",
        timestamp=NOW,
        source_file=(
            "data/input/portafoglio-export.xlsx"
        ),
        source_file_hash="TEST-HASH",
        quant_engine_version="2.5",
        analyzed_positions=32,
        gross_exposure_eur=364_531.64,
        net_exposure_eur=364_531.64,
        account_state_id="ACC-SIM-001",
    )


def _before() -> PortfolioRiskState:

    return PortfolioRiskState(
        gross_exposure_eur=364_531.64,
        net_exposure_eur=364_531.64,

        long_exposure_eur=364_531.64,
        short_exposure_eur=0.0,

        portfolio_volatility_pct=18.00,
        portfolio_beta=0.97,

        var_95_1d_eur=7_276.68,
        cvar_95_1d_eur=10_598.29,

        top5_concentration_pct=42.00,
        effective_positions=18.0,

        analytical_coverage_pct=96.93,
    )


def _account_state(
    *,
    max_position_weight_pct: float | None = None,
    max_trade_loss_eur: float | None = 6000,
    max_portfolio_gross_exposure_pct: float | None = 80,
    max_cio_deployable_pct: float | None = None,
    account_equity_eur: float | None = None,
    eur_available: float = 7_951.03,
    eur_reserve: float = 0.0,
) -> AccountState:

    return AccountState(
        account_state_id="ACC-SIM-001",
        timestamp=NOW,
        cash=[
            CurrencyCash(
                currency=Currency.EUR,
                available=eur_available,
                reserve=eur_reserve,
            ),
            CurrencyCash(
                currency=Currency.USD,
                available=53.17,
                reserve=0,
            ),
        ],
        account_equity_eur=(
            account_equity_eur
        ),
        constraints=RiskConstraints(
            max_trade_loss_eur=(
                max_trade_loss_eur
            ),
            max_position_weight_pct=(
                max_position_weight_pct
            ),
            max_portfolio_gross_exposure_pct=(
                max_portfolio_gross_exposure_pct
            ),
            max_cio_deployable_pct=(
                max_cio_deployable_pct
            ),
            max_portfolio_beta=None,
            max_var_95_1d_eur=None,
        ),
        source=DataSource.OPERATOR,
    )


def _short_proposal(
    *,
    gross_exposure_eur: float = 4_989.51,
    estimated_capital_required_eur: float | None = None,
    estimated_max_loss_eur: float | None = None,
    currency: Currency = Currency.USD,
) -> TradeProposal:

    return TradeProposal(
        proposal_id="PROP-SMCI-SIM",
        opportunity_id="OPP-SMCI-SIM",
        snapshot_id="SNAP-SIM-001",
        created_at=NOW,

        ticker="SMCI",
        direction=Direction.SHORT,

        instrument_id="FIN-SMCI-ORDINARY",

        sizing_id="SIZ-SMCI-SIM",

        execution_side=(
            ExecutionSide.SELL_SHORT
        ),

        quantity=158,
        reference_price=36.72,
        currency=currency,

        entry_type="MARKET",

        gross_exposure_eur=(
            gross_exposure_eur
        ),

        estimated_capital_required_eur=(
            estimated_capital_required_eur
        ),

        estimated_margin_eur=None,

        estimated_max_loss_eur=(
            estimated_max_loss_eur
        ),

        expected_holding_min_days=5,
        expected_holding_max_days=15,
    )


def _long_proposal(
    *,
    gross_exposure_eur: float = 5_000.0,
) -> TradeProposal:

    return TradeProposal(
        proposal_id="PROP-NVDA-SIM",
        opportunity_id="OPP-NVDA-SIM",
        snapshot_id="SNAP-SIM-001",
        created_at=NOW,

        ticker="NVDA",
        direction=Direction.LONG,

        instrument_id="FIN-NVDA-ORDINARY",

        sizing_id="SIZ-NVDA-SIM",

        execution_side=ExecutionSide.BUY,

        quantity=20,
        reference_price=250,
        currency=Currency.USD,

        entry_type="MARKET",

        gross_exposure_eur=(
            gross_exposure_eur
        ),

        estimated_margin_eur=None,
        estimated_max_loss_eur=None,

        expected_holding_min_days=5,
        expected_holding_max_days=15,
    )


# =============================================================
# SHORT exposure mechanics
# =============================================================


def test_short_adds_to_short_and_gross_exposure():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(),
        before=_before(),
        account_state=_account_state(),
    )

    assert (
        result.after.long_exposure_eur
        == 364_531.64
    )

    assert (
        result.after.short_exposure_eur
        == 4_989.51
    )

    assert (
        result.after.gross_exposure_eur
        == pytest.approx(
            369_521.15,
            abs=0.01,
        )
    )


def test_short_reduces_net_exposure():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(),
        before=_before(),
        account_state=_account_state(),
    )

    assert (
        result.after.net_exposure_eur
        == 359_542.13
    )

    assert (
        result.delta.net_exposure_eur
        == pytest.approx(
            -4_989.51,
            abs=0.01,
        )
    )


def test_short_delta_is_directionally_correct():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(),
        before=_before(),
        account_state=_account_state(),
    )

    assert (
        result.delta.gross_exposure_eur
        == pytest.approx(
            4_989.51,
            abs=0.01,
        )
    )

    assert (
        result.delta.long_exposure_eur
        == 0
    )

    assert (
        result.delta.short_exposure_eur
        == 4_989.51
    )


# =============================================================
# LONG exposure mechanics
# =============================================================


def test_long_adds_to_long_gross_and_net():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_long_proposal(),
        before=_before(),
        account_state=_account_state(),
    )

    assert (
        result.after.long_exposure_eur
        == 369_531.64
    )

    assert (
        result.after.short_exposure_eur
        == 0
    )

    assert (
        result.after.gross_exposure_eur
        == 369_531.64
    )

    assert (
        result.after.net_exposure_eur
        == pytest.approx(
            369_531.64,
            abs=0.01,
        )
    )


# =============================================================
# Metrics not yet recomputed
# =============================================================


def test_v1_preserves_unrecomputed_quant_metrics():

    simulator = PortfolioRiskSimulator()

    before = _before()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(),
        before=before,
        account_state=_account_state(),
    )

    assert (
        result.after.portfolio_volatility_pct
        == before.portfolio_volatility_pct
    )

    assert (
        result.after.portfolio_beta
        == before.portfolio_beta
    )

    assert (
        result.after.var_95_1d_eur
        == before.var_95_1d_eur
    )

    assert (
        result.after.cvar_95_1d_eur
        == before.cvar_95_1d_eur
    )

    assert (
        result.delta.portfolio_volatility_pct
        == 0
    )

    assert (
        result.delta.portfolio_beta
        == 0
    )

    assert (
        result.delta.var_95_1d_eur
        == 0
    )

    assert result.warnings


def test_v1_preserves_analytical_coverage():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(),
        before=_before(),
        account_state=_account_state(),
    )

    assert (
        result.after.analytical_coverage_pct
        == 96.93
    )

    assert (
        result.delta.analytical_coverage_pct
        == 0
    )


# =============================================================
# Consistency validation
# =============================================================


def test_rejects_snapshot_mismatch():

    simulator = PortfolioRiskSimulator()

    proposal_data = (
        _short_proposal()
        .model_dump()
    )

    proposal_data[
        "snapshot_id"
    ] = "SNAP-WRONG"

    proposal = (
        TradeProposal.model_validate(
            proposal_data
        )
    )

    try:

        simulator.simulate(
            snapshot=_snapshot(),
            proposal=proposal,
            before=_before(),
            account_state=_account_state(),
        )

        assert False

    except ValueError as exc:

        assert (
            "snapshot"
            in str(exc).lower()
        )


def test_rejects_snapshot_before_exposure_mismatch():

    simulator = PortfolioRiskSimulator()

    data = (
        _before()
        .model_dump()
    )

    data[
        "gross_exposure_eur"
    ] = 300_000

    before = (
        PortfolioRiskState.model_validate(
            data
        )
    )

    try:

        simulator.simulate(
            snapshot=_snapshot(),
            proposal=_short_proposal(),
            before=before,
            account_state=_account_state(),
        )

        assert False

    except ValueError as exc:

        assert (
            "gross exposure"
            in str(exc).lower()
        )


# =============================================================
# Constraint checking
# =============================================================


def test_trade_loss_constraint_passes_when_known_and_below_limit():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(
            estimated_max_loss_eur=2500,
        ),
        before=_before(),
        account_state=_account_state(
            max_trade_loss_eur=6000,
        ),
    )

    assert result.constraints_passed

    assert (
        result.violated_constraints
        == []
    )


def test_trade_loss_constraint_fails_above_limit():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(
            estimated_max_loss_eur=7000,
        ),
        before=_before(),
        account_state=_account_state(
            max_trade_loss_eur=6000,
        ),
    )

    assert not result.constraints_passed

    assert any(
        "max trade loss"
        in violation.lower()
        for violation
        in result.violated_constraints
    )


def test_unknown_trade_loss_generates_warning_not_failure():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(
            estimated_max_loss_eur=None,
        ),
        before=_before(),
        account_state=_account_state(
            max_trade_loss_eur=6000,
        ),
    )

    assert result.constraints_passed

    assert (
        result.violated_constraints
        == []
    )

    assert any(
        "trade loss"
        in warning.lower()
        for warning
        in result.warnings
    )


def test_position_weight_constraint():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(
            gross_exposure_eur=50_000,
        ),
        before=_before(),
        account_state=_account_state(
            max_position_weight_pct=10,
        ),
    )

    assert not result.constraints_passed

    assert any(
        "position weight"
        in violation.lower()
        for violation
        in result.violated_constraints
    )


# =============================================================
# Constraints that V1 cannot yet evaluate
# =============================================================


def test_unenforceable_quant_constraints_generate_warnings():

    simulator = PortfolioRiskSimulator()

    account = _account_state()

    data = (
        account.model_dump()
    )

    data["constraints"][
        "max_portfolio_beta"
    ] = 1.10

    data["constraints"][
        "max_var_95_1d_eur"
    ] = 10_000

    account = (
        AccountState.model_validate(
            data
        )
    )

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(
            estimated_max_loss_eur=2500,
        ),
        before=_before(),
        account_state=account,
    )

    assert result.constraints_passed

    assert any(
        "beta"
        in warning.lower()
        for warning
        in result.warnings
    )

    assert any(
        "var"
        in warning.lower()
        for warning
        in result.warnings
    )

# =============================================================
# Canonical NAV / portfolio gross exposure % constraint
# =============================================================


def test_gross_exposure_constraint_passes_when_below_limit():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(),
        before=_before(),
        account_state=_account_state(
            account_equity_eur=500_000,
            max_portfolio_gross_exposure_pct=80,
        ),
    )

    # AFTER gross = 369,521.15; NAV = 500,000 => 73.90%.
    assert result.constraints_passed

    assert not any(
        "gross exposure constraint exceeded"
        in violation.lower()
        for violation
        in result.violated_constraints
    )

    assert not any(
        "canonical account nav/equity"
        in warning.lower()
        for warning
        in result.warnings
    )


def test_gross_exposure_constraint_fails_when_above_limit():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(),
        before=_before(),
        account_state=_account_state(
            account_equity_eur=400_000,
            max_portfolio_gross_exposure_pct=90,
        ),
    )

    # AFTER gross = 369,521.15; NAV = 400,000 => 92.38% > 90%.
    assert not result.constraints_passed

    assert any(
        "max portfolio gross exposure constraint exceeded"
        in violation.lower()
        for violation
        in result.violated_constraints
    )


def test_gross_exposure_constraint_is_unknown_without_nav():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(),
        before=_before(),
        account_state=_account_state(
            account_equity_eur=None,
            max_portfolio_gross_exposure_pct=80,
        ),
    )

    # Missing NAV is UNKNOWN, not a known hard failure.
    assert result.constraints_passed

    assert any(
        "max portfolio gross exposure % cannot yet be evaluated"
        in warning.lower()
        for warning
        in result.warnings
    )


def test_gross_exposure_constraint_uses_after_not_before():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(
            gross_exposure_eur=20_000,
        ),
        before=_before(),
        account_state=_account_state(
            account_equity_eur=400_000,
            max_portfolio_gross_exposure_pct=95,
        ),
    )

    # BEFORE gross = 364,531.64 / 400,000 = 91.13% (passes).
    # AFTER gross  = 384,531.64 / 400,000 = 96.13% (fails).
    assert not result.constraints_passed

    assert any(
        "max portfolio gross exposure constraint exceeded"
        in violation.lower()
        for violation
        in result.violated_constraints
    )

# =============================================================
# CIO deployable capital constraint
# =============================================================


def test_cio_deployable_constraint_passes_when_below_limit():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(
            gross_exposure_eur=10_000,
            estimated_capital_required_eur=10_000,
            currency=Currency.EUR,
        ),
        before=_before(),
        account_state=_account_state(
            max_portfolio_gross_exposure_pct=None,
            max_cio_deployable_pct=80,
            eur_available=20_000,
            eur_reserve=5_000,
        ),
    )

    # Deployable EUR = 20,000 - 5,000 = 15,000.
    # CIO cap = 80% * 15,000 = 12,000.
    # Proposal = 10,000 => PASS.
    assert result.constraints_passed is True

    assert not any(
        "max cio deployable constraint exceeded"
        in violation.lower()
        for violation in result.violated_constraints
    )


def test_cio_deployable_constraint_fails_when_above_limit():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(
            gross_exposure_eur=13_000,
            estimated_capital_required_eur=13_000,
            currency=Currency.EUR,
        ),
        before=_before(),
        account_state=_account_state(
            max_portfolio_gross_exposure_pct=None,
            max_cio_deployable_pct=80,
            eur_available=20_000,
            eur_reserve=5_000,
        ),
    )

    # Deployable EUR = 15,000.
    # CIO cap = 12,000.
    # Proposal = 13,000 => FAIL.
    assert result.constraints_passed is False

    assert any(
        "max cio deployable constraint exceeded"
        in violation.lower()
        for violation in result.violated_constraints
    )


def test_cio_deployable_constraint_respects_operator_reserve():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(
            gross_exposure_eur=13_000,
            estimated_capital_required_eur=13_000,
            currency=Currency.EUR,
        ),
        before=_before(),
        account_state=_account_state(
            max_portfolio_gross_exposure_pct=None,
            max_cio_deployable_pct=80,
            eur_available=20_000,
            eur_reserve=5_000,
        ),
    )

    # If reserve were ignored, 80% of 20,000 = 16,000 and this
    # proposal would pass. Correct behavior uses deployable cash:
    # 20,000 - 5,000 = 15,000; 80% = 12,000, so it must fail.
    assert result.constraints_passed is False

    assert any(
        "€12,000.00"
        in violation
        for violation in result.violated_constraints
    )

    assert any(
        "€15,000.00"
        in violation
        for violation in result.violated_constraints
    )


def test_cio_deployable_constraint_works_for_non_eur_proposal():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(
            gross_exposure_eur=10_000,
            estimated_capital_required_eur=10_000,
            currency=Currency.USD,
        ),
        before=_before(),
        account_state=_account_state(
            max_portfolio_gross_exposure_pct=None,
            max_cio_deployable_pct=80,
            eur_available=20_000,
            eur_reserve=5_000,
        ),
    )

    # Deployable EUR = 15,000.
    # CIO cap = 12,000.
    #
    # PositionSizer has already persisted the canonical EUR capital
    # requirement, therefore proposal currency does not prevent the
    # simulator from evaluating the deployment constraint.
    #
    # Capital required = 10,000 <= 12,000 => PASS.
    assert result.constraints_passed is True

    assert not any(
        "max cio deployable % cannot yet be evaluated"
        in warning.lower()
        for warning in result.warnings
    )

    assert not any(
        "max cio deployable constraint exceeded"
        in violation.lower()
        for violation in result.violated_constraints
    )


def test_cio_deployable_constraint_fails_for_non_eur_proposal():

    simulator = PortfolioRiskSimulator()

    result = simulator.simulate(
        snapshot=_snapshot(),
        proposal=_short_proposal(
            gross_exposure_eur=13_000,
            estimated_capital_required_eur=13_000,
            currency=Currency.USD,
        ),
        before=_before(),
        account_state=_account_state(
            max_portfolio_gross_exposure_pct=None,
            max_cio_deployable_pct=80,
            eur_available=20_000,
            eur_reserve=5_000,
        ),
    )

    # Deployable EUR = 15,000.
    # CIO cap = 12,000.
    # Persisted capital requirement = 13,000.
    #
    # Currency is USD, but the canonical funding requirement is
    # already expressed in EUR.
    assert result.constraints_passed is False

    assert any(
        "max cio deployable constraint exceeded"
        in violation.lower()
        for violation in result.violated_constraints
    )