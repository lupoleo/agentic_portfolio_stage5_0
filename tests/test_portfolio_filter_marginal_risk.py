from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.analysis.marginal_risk import PortfolioMarginalRiskEngine
from app.cio.models import *
from app.cio.portfolio_filter_service import (
    PortfolioFilterService,
    PortfolioMarginalRiskInputs,
)
from app.cio.storage import Stage3Store

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)

def history_from_returns(returns):
    returns = np.asarray(returns, dtype=float)
    prices = 100.0 * np.cumprod(1.0 + returns)
    index = pd.bdate_range("2023-01-02", periods=len(prices))
    return pd.DataFrame({"Close": prices}, index=index)

def make_position(symbol, direction, exposure, history):
    return SimpleNamespace(
        yahoo_symbol=symbol,
        position=SimpleNamespace(
            quantity=1 if direction == "LONG" else -1,
            market_value_eur=exposure,
            direction=direction,
            name=symbol,
        ),
        history=history,
    )

def make_store(tmp_path, constraints):
    s = Stage3Store(tmp_path / "pf1c2.sqlite")
    s.save_account_state(AccountState(
        account_state_id="ACC-1", timestamp=NOW, account_equity_eur=300000,
        cash=[CurrencyCash(currency=Currency.EUR, available=20000, reserve=1000)],
        constraints=constraints, source=DataSource.OPERATOR,
    ))
    s.save_portfolio_snapshot(PortfolioSnapshot(
        snapshot_id="SNAP-1", timestamp=NOW, source_file="portfolio.xlsx",
        source_file_hash="abc", quant_engine_version="2.5",
        analyzed_positions=1, gross_exposure_eur=100000,
        net_exposure_eur=100000, account_state_id="ACC-1",
    ))
    s.save_portfolio_risk_state(PortfolioRiskStateRecord(
        risk_state_id="RISK-1", snapshot_id="SNAP-1", created_at=NOW,
        state=PortfolioRiskState(
            gross_exposure_eur=100000, net_exposure_eur=100000,
            long_exposure_eur=100000, short_exposure_eur=0,
            portfolio_volatility_pct=10, portfolio_beta=.5,
            var_95_1d_eur=2000, cvar_95_1d_eur=3000,
            top5_concentration_pct=100, effective_positions=1,
            analytical_coverage_pct=100,
        ),
    ))
    return s

def save_opp(s):
    s.save_trade_opportunity(TradeOpportunity(
        opportunity_id="OPP-1", snapshot_id="SNAP-1", created_at=NOW,
        ticker="CAND", direction=Direction.LONG,
        horizon=TradingHorizon.SWING, confidence=.8,
        target_exposure_eur=50000, max_intended_loss_eur=1000,
        thesis="test",
    ))

def by_code(r):
    return {x.code: x for x in r.constraint_checks}

def build_provider(market, base, cand):
    return lambda opp, snap: PortfolioMarginalRiskInputs(
        analyzed_positions=[
            make_position("BASE", "LONG", 100000, history_from_returns(base))
        ],
        candidate_symbol="CAND",
        candidate_history=history_from_returns(cand),
        benchmark_history=history_from_returns(market),
    )

def test_marginal_context_attached_and_beta_known(tmp_path):
    rng=np.random.default_rng(320)
    market=rng.normal(.0002,.01,800)
    base=.5*market+rng.normal(0,.003,800)
    cand=1.5*market+rng.normal(0,.002,800)
    s=make_store(tmp_path,RiskConstraints(max_portfolio_beta=2.0)); save_opp(s)
    r=PortfolioFilterService(
        s,
        marginal_risk_engine=PortfolioMarginalRiskEngine(min_observations=500,max_observations=756),
        marginal_risk_input_provider=build_provider(market,base,cand),
    ).assess("OPP-1",as_of=NOW,assessment_id="PFIT-1")
    c=by_code(r)["MAX_PORTFOLIO_BETA"]
    assert r.marginal_risk_context is not None
    assert c.status==PortfolioCheckStatus.PASS
    assert c.projected_value==pytest.approx(r.marginal_risk_context.beta.after)

def test_beta_limit_can_reject(tmp_path):
    rng=np.random.default_rng(321)
    market=rng.normal(0,.01,800)
    base=.5*market+rng.normal(0,.002,800)
    cand=2.0*market+rng.normal(0,.001,800)
    s=make_store(tmp_path,RiskConstraints(max_portfolio_beta=.8)); save_opp(s)
    r=PortfolioFilterService(
        s,
        marginal_risk_engine=PortfolioMarginalRiskEngine(min_observations=500),
        marginal_risk_input_provider=build_provider(market,base,cand),
    ).assess("OPP-1",as_of=NOW,assessment_id="PFIT-1")
    assert by_code(r)["MAX_PORTFOLIO_BETA"].status==PortfolioCheckStatus.FAIL
    assert r.decision==PortfolioFitDecision.REJECT

def test_var_limit_can_reject(tmp_path):
    rng=np.random.default_rng(322)
    market=rng.normal(0,.01,800)
    base=.4*market+rng.normal(0,.003,800)
    cand=rng.normal(0,.04,800)
    s=make_store(tmp_path,RiskConstraints(max_var_95_1d_eur=1000)); save_opp(s)
    r=PortfolioFilterService(
        s,
        marginal_risk_engine=PortfolioMarginalRiskEngine(min_observations=500),
        marginal_risk_input_provider=build_provider(market,base,cand),
    ).assess("OPP-1",as_of=NOW,assessment_id="PFIT-1")
    assert by_code(r)["MAX_VAR_95_1D_EUR"].status==PortfolioCheckStatus.FAIL
    assert r.decision==PortfolioFitDecision.REJECT

def test_without_provider_preserves_unknown(tmp_path):
    s=make_store(tmp_path,RiskConstraints(max_portfolio_beta=1,max_var_95_1d_eur=10000)); save_opp(s)
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-1")
    assert r.marginal_risk_context is None
    assert by_code(r)["MAX_PORTFOLIO_BETA"].status==PortfolioCheckStatus.UNKNOWN
    assert by_code(r)["MAX_VAR_95_1D_EUR"].status==PortfolioCheckStatus.UNKNOWN

def test_engine_failure_fails_closed(tmp_path):
    class Broken:
        def assess(self, **kwargs):
            raise ValueError("synthetic failure")
    s=make_store(tmp_path,RiskConstraints(max_portfolio_beta=1)); save_opp(s)
    provider=lambda opp,snap: PortfolioMarginalRiskInputs(
        analyzed_positions=[], candidate_symbol="CAND",
        candidate_history=pd.DataFrame(), benchmark_history=pd.DataFrame(),
    )
    r=PortfolioFilterService(
        s,marginal_risk_engine=Broken(),marginal_risk_input_provider=provider
    ).assess("OPP-1",as_of=NOW,assessment_id="PFIT-1")
    assert r.marginal_risk_context is None
    assert by_code(r)["MAX_PORTFOLIO_BETA"].status==PortfolioCheckStatus.UNKNOWN
    assert any("synthetic failure" in w for w in r.warnings)
