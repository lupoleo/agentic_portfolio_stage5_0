from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.analysis.marginal_risk import PortfolioMarginalRiskEngine
from app.cio.models import (
    AccountState, Currency, CurrencyCash, DataSource, Direction,
    PortfolioCheckStatus, PortfolioFitDecision, PortfolioRiskState,
    PortfolioRiskStateRecord, PortfolioSnapshot, RiskConstraints,
    TradeOpportunity, TradingHorizon,
)
from app.cio.portfolio_filter_service import (
    PortfolioFilterService, PortfolioMarginalRiskInputs,
)
from app.cio.portfolio_fit_scoring import PortfolioFitScoringEngine
from app.cio.storage import Stage3Store

NOW = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)


def history_from_returns(returns):
    returns = np.asarray(returns, dtype=float)
    prices = 100.0 * np.cumprod(1.0 + returns)
    index = pd.bdate_range("2023-01-02", periods=len(prices))
    return pd.DataFrame({"Close": prices}, index=index)


def analyzed_position(symbol, direction, exposure, history):
    return SimpleNamespace(
        yahoo_symbol=symbol,
        position=SimpleNamespace(
            quantity=1 if direction == "LONG" else -1,
            market_value_eur=float(exposure),
            direction=direction,
            name=symbol,
        ),
        history=history,
    )


def build_store(tmp_path):
    store = Stage3Store(tmp_path / "pf1d3_acceptance.sqlite")
    store.save_account_state(AccountState(
        account_state_id="ACC-PF1D3", timestamp=NOW, account_equity_eur=300000.0,
        cash=[CurrencyCash(currency=Currency.EUR, available=25000.0, reserve=1000.0)],
        constraints=RiskConstraints(
            max_trade_loss_eur=6000.0,
            max_position_weight_pct=25.0,
            max_portfolio_gross_exposure_pct=150.0,
            max_portfolio_beta=2.0,
            max_var_95_1d_eur=20000.0,
        ),
        source=DataSource.OPERATOR,
    ))
    store.save_portfolio_snapshot(PortfolioSnapshot(
        snapshot_id="SNAP-PF1D3", timestamp=NOW, source_file="synthetic.xlsx",
        source_file_hash="pf1d3", quant_engine_version="2.5",
        analyzed_positions=2, gross_exposure_eur=100000.0,
        net_exposure_eur=100000.0, account_state_id="ACC-PF1D3",
    ))
    store.save_portfolio_risk_state(PortfolioRiskStateRecord(
        risk_state_id="RISK-PF1D3", snapshot_id="SNAP-PF1D3", created_at=NOW,
        state=PortfolioRiskState(
            gross_exposure_eur=100000.0, net_exposure_eur=100000.0,
            long_exposure_eur=100000.0, short_exposure_eur=0.0,
            portfolio_volatility_pct=10.0, portfolio_beta=0.5,
            var_95_1d_eur=2000.0, cvar_95_1d_eur=3000.0,
            top5_concentration_pct=100.0, effective_positions=8.0,
            analytical_coverage_pct=100.0,
        ),
    ))
    store.save_trade_opportunity(TradeOpportunity(
        opportunity_id="OPP-PF1D3", snapshot_id="SNAP-PF1D3", created_at=NOW,
        ticker="CAND", direction=Direction.SHORT, horizon=TradingHorizon.SWING,
        confidence=0.8, target_exposure_eur=10000.0,
        max_intended_loss_eur=1000.0,
        thesis="Synthetic PF-1D.3 integrated acceptance candidate.",
    ))
    return store


def build_provider():
    rng = np.random.default_rng(1303)
    market = rng.normal(0.0002, 0.010, 800)
    base_a = 0.75 * market + rng.normal(0.0, 0.004, 800)
    base_b = 0.55 * market + rng.normal(0.0, 0.005, 800)
    candidate = 0.70 * market + rng.normal(0.0, 0.004, 800)
    positions = [
        analyzed_position("BASEA", "LONG", 60000.0, history_from_returns(base_a)),
        analyzed_position("BASEB", "LONG", 40000.0, history_from_returns(base_b)),
    ]
    def provider(opportunity, snapshot):
        return PortfolioMarginalRiskInputs(
            analyzed_positions=positions, candidate_symbol="CAND",
            candidate_history=history_from_returns(candidate),
            benchmark_history=history_from_returns(market),
        )
    return provider


def test_integrated_assess_materializes_complete_scored_assessment(tmp_path):
    store = build_store(tmp_path)
    service = PortfolioFilterService(
        store,
        marginal_risk_engine=PortfolioMarginalRiskEngine(
            min_observations=500, max_observations=756
        ),
        marginal_risk_input_provider=build_provider(),
        scoring_engine=PortfolioFitScoringEngine(),
    )
    result = service.assess(
        "OPP-PF1D3", as_of=NOW, assessment_id="PFIT-PF1D3"
    )

    assert result.assessment_id == "PFIT-PF1D3"
    assert result.opportunity_id == "OPP-PF1D3"
    assert result.snapshot_id == "SNAP-PF1D3"
    assert result.risk_state_id == "RISK-PF1D3"
    assert result.account_state_id == "ACC-PF1D3"

    assert result.exposure_context is not None
    assert result.exposure_context.net_exposure_before_eur == pytest.approx(100000.0)
    assert result.exposure_context.net_exposure_after_eur == pytest.approx(90000.0)
    assert result.marginal_risk_context is not None
    assert result.marginal_risk_context.candidate_correlation_to_portfolio is not None

    assert result.portfolio_fit_score is not None
    assert 0.0 <= result.portfolio_fit_score <= 100.0
    assert result.score_breakdown is not None
    assert result.score_breakdown.scoring_coverage_pct == pytest.approx(100.0)
    assert result.score_breakdown.analytical_coverage_pct is not None

    components = {x.name: x for x in result.score_breakdown.components}
    assert set(components) == {
        "directional", "concentration", "correlation",
        "volatility", "beta", "tail_risk",
    }
    assert all(x.score is not None for x in components.values())
    assert components["directional"].score == pytest.approx(100.0)
    assert components["correlation"].score > 50.0

    checks = {x.code: x for x in result.constraint_checks}
    for code in (
        "TARGET_EXPOSURE_AVAILABLE", "MAX_TRADE_LOSS_EUR",
        "MAX_POSITION_WEIGHT_PCT", "MAX_PORTFOLIO_GROSS_EXPOSURE_PCT",
        "MAX_PORTFOLIO_BETA", "MAX_VAR_95_1D_EUR",
    ):
        assert checks[code].status == PortfolioCheckStatus.PASS

    expected = service._derive_decision(
        result.constraint_checks,
        result.warnings,
        SimpleNamespace(
            portfolio_fit_score=result.portfolio_fit_score,
            scoring_coverage_pct=result.score_breakdown.scoring_coverage_pct,
            analytical_coverage_pct=result.score_breakdown.analytical_coverage_pct,
        ),
    )
    assert result.decision == expected
    assert result.decision in {
        PortfolioFitDecision.PASS, PortfolioFitDecision.PASS_WITH_WARNING
    }
    assert "Deterministic portfolio fit score=" in result.rationale


def test_integrated_assess_is_deterministic(tmp_path):
    store = build_store(tmp_path)
    service = PortfolioFilterService(
        store,
        marginal_risk_engine=PortfolioMarginalRiskEngine(
            min_observations=500, max_observations=756
        ),
        marginal_risk_input_provider=build_provider(),
        scoring_engine=PortfolioFitScoringEngine(),
    )
    a = service.assess("OPP-PF1D3", as_of=NOW, assessment_id="PFIT-A")
    b = service.assess("OPP-PF1D3", as_of=NOW, assessment_id="PFIT-B")

    assert a.portfolio_fit_score == pytest.approx(b.portfolio_fit_score, abs=1e-12)
    assert a.decision == b.decision
    assert a.score_breakdown.scoring_coverage_pct == pytest.approx(
        b.score_breakdown.scoring_coverage_pct
    )
    assert [
        (x.name, x.weight, x.score, x.reason)
        for x in a.score_breakdown.components
    ] == [
        (x.name, x.weight, x.score, x.reason)
        for x in b.score_breakdown.components
    ]
