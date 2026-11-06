from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.cio.models import (
    AccountState,
    CacheStatus,
    CioDecision,
    CioDecisionType,
    Currency,
    CurrencyCash,
    DataSource,
    DecisionEvidence,
    Direction,
    FinecoInstrument,
    InstrumentCandidate,
    InstrumentType,
    PortfolioRiskState,
    PortfolioSimulation,
    PortfolioSnapshot,
    ProposalStatus,
    RiskConstraints,
    TradeOpportunity,
    TradeProposal,
    TradingHorizon,
    TradingMode,
)


NOW = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)


def test_account_state_accepts_eur_and_usd_cash():
    state = AccountState(
        account_state_id="ACC-1",
        timestamp=NOW,
        cash=[
            CurrencyCash(
                currency=Currency.EUR,
                available=18_500,
                reserve=5_000,
            ),
            CurrencyCash(
                currency=Currency.USD,
                available=6_200,
                reserve=1_000,
            ),
        ],
        constraints=RiskConstraints(
            max_trade_loss_eur=500,
        ),
        source=DataSource.OPERATOR,
    )

    assert len(state.cash) == 2
    assert state.cash[0].available == 18_500


def test_cash_reserve_cannot_exceed_available_cash():
    with pytest.raises(ValidationError):
        CurrencyCash(
            currency=Currency.EUR,
            available=1_000,
            reserve=1_500,
        )


def test_account_state_rejects_duplicate_currency():
    with pytest.raises(ValidationError):
        AccountState(
            account_state_id="ACC-1",
            timestamp=NOW,
            cash=[
                CurrencyCash(
                    currency=Currency.EUR,
                    available=1_000,
                ),
                CurrencyCash(
                    currency=Currency.EUR,
                    available=2_000,
                ),
            ],
            source=DataSource.OPERATOR,
        )


def test_fineco_instrument_preserves_unknown_fields_as_none():
    instrument = FinecoInstrument(
        instrument_id="FINECO-MSFT-CFDC-X5",
        underlying="MSFT",
        instrument_type=InstrumentType.CFDC,
        trading_mode=TradingMode.INTRADAY,
        quote_currency=Currency.USD,
        settlement_currency=Currency.EUR,
        margin_currency=Currency.EUR,
        long_available=True,
        short_available=True,
        intraday_available=True,
        overnight_available=True,
        leverage=5,
        margin_pct=20,
        source=DataSource.OPERATOR,
        cache_status=CacheStatus.CURRENT,
    )

    assert instrument.market_snapshot is None
    assert instrument.commission is None
    assert instrument.overnight_financing_pct is None
    assert instrument.tick_size is None


def test_trade_opportunity_is_broker_independent_and_direction_explicit():
    opportunity = TradeOpportunity(
        opportunity_id="OPP-1",
        snapshot_id="SNAP-1",
        created_at=NOW,
        ticker="GOOGL",
        direction=Direction.SHORT,
        horizon=TradingHorizon.SWING,
        expected_holding_min_days=2,
        expected_holding_max_days=5,
        confidence=0.72,
        target_exposure_eur=8_000,
        max_intended_loss_eur=300,
        thesis="Bearish tactical thesis.",
        key_risks=["Positive catalyst"],
        evidence_ids=["EVD-1"],
    )

    assert opportunity.direction is Direction.SHORT
    assert "instrument_id" not in TradeOpportunity.model_fields


def test_trade_opportunity_rejects_invalid_confidence():
    with pytest.raises(ValidationError):
        TradeOpportunity(
            opportunity_id="OPP-1",
            snapshot_id="SNAP-1",
            created_at=NOW,
            ticker="GOOGL",
            direction=Direction.SHORT,
            horizon=TradingHorizon.SWING,
            confidence=1.2,
            thesis="Invalid confidence.",
        )


def test_trade_opportunity_rejects_invalid_holding_range():
    with pytest.raises(ValidationError):
        TradeOpportunity(
            opportunity_id="OPP-1",
            snapshot_id="SNAP-1",
            created_at=NOW,
            ticker="GOOGL",
            direction=Direction.SHORT,
            horizon=TradingHorizon.SWING,
            expected_holding_min_days=5,
            expected_holding_max_days=2,
            confidence=0.5,
            thesis="Invalid holding range.",
        )


def test_rejected_instrument_candidate_requires_reason():
    with pytest.raises(ValidationError):
        InstrumentCandidate(
            instrument_id="FIN-1",
            opportunity_id="OPP-1",
            eligible=False,
        )


def test_trade_proposal_quantity_is_positive_even_for_short():
    proposal = TradeProposal(
        proposal_id="CIO-1",
        opportunity_id="OPP-1",
        snapshot_id="SNAP-1",
        created_at=NOW,
        ticker="GOOGL",
        direction=Direction.SHORT,
        instrument_id="FIN-GOOGL-X10",
        quantity=20,
        reference_price=345.90,
        currency=Currency.USD,
        entry_type="LIMIT",
        entry_price=346.50,
        stop_price=355.00,
        target_1=337.00,
        gross_exposure_eur=6_300,
        estimated_margin_eur=630,
        estimated_max_loss_eur=180,
        expected_holding_min_days=2,
        expected_holding_max_days=5,
        status=ProposalStatus.PROPOSED,
    )

    assert proposal.direction is Direction.SHORT
    assert proposal.quantity == 20

    with pytest.raises(ValidationError):
        TradeProposal(
            proposal_id="CIO-2",
            opportunity_id="OPP-1",
            snapshot_id="SNAP-1",
            created_at=NOW,
            ticker="GOOGL",
            direction=Direction.SHORT,
            instrument_id="FIN-GOOGL-X10",
            quantity=-20,
            reference_price=345.90,
            currency=Currency.USD,
            entry_type="MARKET",
            gross_exposure_eur=6_300,
        )


def _risk_state(
    gross=326_000.0,
    net=326_000.0,
    vol=18.36,
    beta=0.913,
    var=5_759.0,
    cvar=8_414.0,
    top5=44.07,
    effective=17.13,
):
    return PortfolioRiskState(
        gross_exposure_eur=gross,
        net_exposure_eur=net,
        portfolio_volatility_pct=vol,
        portfolio_beta=beta,
        var_95_1d_eur=var,
        cvar_95_1d_eur=cvar,
        top5_concentration_pct=top5,
        effective_positions=effective,
    )


def test_portfolio_simulation_requires_violation_details_when_failed():
    before = _risk_state()
    after = _risk_state(beta=1.2)
    delta = _risk_state(
        gross=0,
        net=0,
        vol=0,
        beta=0.287,
        var=0,
        cvar=0,
        top5=0,
        effective=0,
    )

    with pytest.raises(ValidationError):
        PortfolioSimulation(
            simulation_id="SIM-1",
            snapshot_id="SNAP-1",
            proposal_id="CIO-1",
            created_at=NOW,
            before=before,
            after=after,
            delta=delta,
            constraints_passed=False,
            violated_constraints=[],
        )


def test_decision_contract_links_proposal_simulation_and_evidence():
    evidence = DecisionEvidence(
        evidence_id="EVD-1",
        snapshot_id="SNAP-1",
        created_at=NOW,
        ticker="GOOGL",
        quantitative_summary="Portfolio beta and risk contribution reviewed.",
    )

    decision = CioDecision(
        decision_id="DEC-1",
        proposal_id="CIO-1",
        created_at=NOW,
        decision=CioDecisionType.ACCEPT,
        confidence=0.74,
        rationale="Risk-adjusted proposal passes configured constraints.",
        evidence_ids=[evidence.evidence_id],
        simulation_id="SIM-1",
    )

    assert decision.decision is CioDecisionType.ACCEPT
    assert decision.evidence_ids == ["EVD-1"]


def test_portfolio_snapshot_is_bound_to_quant_engine_version_and_hash():
    snapshot = PortfolioSnapshot(
        snapshot_id="SNAP-1",
        timestamp=NOW,
        source_file="data/input/portafoglio-export.xlsx",
        source_file_hash="abc123",
        quant_engine_version="2.5.0",
        analyzed_positions=31,
        gross_exposure_eur=326_774.28,
        net_exposure_eur=326_774.28,
    )

    assert snapshot.quant_engine_version == "2.5.0"
    assert snapshot.source_file_hash == "abc123"
