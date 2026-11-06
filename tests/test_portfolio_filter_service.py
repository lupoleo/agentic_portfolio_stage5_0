from datetime import datetime, timezone
import pytest
from app.cio.models import *
from app.cio.portfolio_filter_service import PortfolioFilterService
from app.cio.storage import Stage3Store

NOW=datetime(2026,9,10,12,0,tzinfo=timezone.utc)

def make_store(tmp_path,constraints=None,coverage=100.0):
    s=Stage3Store(tmp_path/"pf.sqlite")
    s.save_account_state(AccountState(account_state_id="ACC-LATEST",timestamp=NOW,account_equity_eur=300000,cash=[CurrencyCash(currency=Currency.EUR,available=20000,reserve=1000)],constraints=constraints or RiskConstraints(),source=DataSource.OPERATOR))
    s.save_portfolio_snapshot(PortfolioSnapshot(snapshot_id="SNAP-1",timestamp=NOW,source_file="p.xlsx",source_file_hash="abc",quant_engine_version="2.5",analyzed_positions=10,gross_exposure_eur=200000,net_exposure_eur=160000,account_state_id="ACC-LATEST"))
    s.save_portfolio_risk_state(PortfolioRiskStateRecord(risk_state_id="RISK-1",snapshot_id="SNAP-1",created_at=NOW,state=PortfolioRiskState(gross_exposure_eur=200000,net_exposure_eur=160000,long_exposure_eur=180000,short_exposure_eur=20000,portfolio_volatility_pct=15,portfolio_beta=.8,var_95_1d_eur=5000,cvar_95_1d_eur=7000,top5_concentration_pct=50,effective_positions=8,analytical_coverage_pct=coverage)))
    return s

def save_opp(s,target=10000,maxloss=1000):
    s.save_trade_opportunity(TradeOpportunity(opportunity_id="OPP-1",snapshot_id="SNAP-1",created_at=NOW,ticker="NVDA",direction=Direction.LONG,horizon=TradingHorizon.SWING,confidence=.8,target_exposure_eur=target,max_intended_loss_eur=maxloss,thesis="test"))

def by_code(a): return {x.code:x for x in a.constraint_checks}

def test_pf1a_passes_known_constraints(tmp_path):
    s=make_store(tmp_path,RiskConstraints(max_trade_loss_eur=6000,max_position_weight_pct=10,max_portfolio_gross_exposure_pct=80)); save_opp(s)
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T")
    c=by_code(r); assert r.decision==PortfolioFitDecision.PASS; assert r.hard_constraints_passed is True; assert r.portfolio_fit_score is None
    assert c["MAX_TRADE_LOSS_EUR"].status==PortfolioCheckStatus.PASS; assert c["MAX_POSITION_WEIGHT_PCT"].status==PortfolioCheckStatus.PASS; assert c["MAX_PORTFOLIO_GROSS_EXPOSURE_PCT"].status==PortfolioCheckStatus.PASS

def test_pf1a_rejects_gross_breach(tmp_path):
    s=make_store(tmp_path,RiskConstraints(max_portfolio_gross_exposure_pct=65)); save_opp(s)
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T"); c=by_code(r)["MAX_PORTFOLIO_GROSS_EXPOSURE_PCT"]
    assert r.decision==PortfolioFitDecision.REJECT; assert r.hard_constraints_passed is False; assert c.status==PortfolioCheckStatus.FAIL; assert c.projected_value==pytest.approx(70)

def test_pf1a_unknown_target_never_manufactures_compliance(tmp_path):
    s=make_store(tmp_path,RiskConstraints(max_position_weight_pct=10,max_portfolio_gross_exposure_pct=80)); save_opp(s,target=None)
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T"); c=by_code(r)
    assert r.decision==PortfolioFitDecision.UNKNOWN; assert r.hard_constraints_passed is None; assert c["TARGET_EXPOSURE_AVAILABLE"].status==PortfolioCheckStatus.UNKNOWN

def test_pf1a_preinstrument_constraints_unknown(tmp_path):
    s=make_store(tmp_path,RiskConstraints(max_cio_deployable_pct=80,max_portfolio_beta=1,max_var_95_1d_eur=7000,min_cash_reserve_eur=1000)); save_opp(s)
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T"); c=by_code(r)
    assert r.decision==PortfolioFitDecision.PASS_WITH_WARNING; assert c["MAX_CIO_DEPLOYABLE_PCT"].status==PortfolioCheckStatus.UNKNOWN; assert c["MAX_PORTFOLIO_BETA"].status==PortfolioCheckStatus.UNKNOWN; assert c["MAX_VAR_95_1D_EUR"].status==PortfolioCheckStatus.UNKNOWN

def test_pf1a_latest_account_state_is_authority(tmp_path):
    s=make_store(tmp_path,RiskConstraints(max_portfolio_gross_exposure_pct=80)); save_opp(s)
    s.save_account_state(AccountState(account_state_id="ACC-NEW",timestamp=datetime(2026,9,10,13,tzinfo=timezone.utc),account_equity_eur=250000,cash=[CurrencyCash(currency=Currency.EUR,available=20000,reserve=1000)],constraints=RiskConstraints(max_portfolio_gross_exposure_pct=80),source=DataSource.OPERATOR))
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T"); c=by_code(r)["MAX_PORTFOLIO_GROSS_EXPOSURE_PCT"]
    assert r.account_state_id=="ACC-NEW"; assert c.projected_value==pytest.approx(84); assert r.decision==PortfolioFitDecision.REJECT

def test_pf1a_incomplete_coverage_warns(tmp_path):
    s=make_store(tmp_path,RiskConstraints(),coverage=92.89); save_opp(s)
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T")
    assert r.decision==PortfolioFitDecision.PASS_WITH_WARNING; assert any("92.89%" in x for x in r.warnings)

def test_contract_reject_requires_fail():
    with pytest.raises(ValueError,match="REJECT requires"):
        PortfolioFitAssessment(assessment_id="PF",opportunity_id="O",snapshot_id="S",created_at=NOW,ticker="X",direction=Direction.LONG,target_exposure_eur=1,decision=PortfolioFitDecision.REJECT,constraint_checks=[PortfolioConstraintCheck(code="X",status=PortfolioCheckStatus.PASS)],rationale="x")


def test_pf1b_long_on_net_long_is_concentration(tmp_path):
    s=make_store(tmp_path); save_opp(s,target=10000)
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T")
    x=r.exposure_context
    assert x is not None
    assert x.long_exposure_after_eur==pytest.approx(190000)
    assert x.short_exposure_after_eur==pytest.approx(20000)
    assert x.gross_exposure_after_eur==pytest.approx(210000)
    assert x.net_exposure_after_eur==pytest.approx(170000)
    assert x.directional_effect==PortfolioDirectionalEffect.CONCENTRATION

def test_pf1b_short_on_net_long_is_diversification(tmp_path):
    s=make_store(tmp_path)
    s.save_trade_opportunity(TradeOpportunity(opportunity_id="OPP-1",snapshot_id="SNAP-1",created_at=NOW,ticker="NVDA",direction=Direction.SHORT,horizon=TradingHorizon.SWING,confidence=.8,target_exposure_eur=10000,max_intended_loss_eur=1000,thesis="test"))
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T")
    x=r.exposure_context
    assert x.net_exposure_after_eur==pytest.approx(150000)
    assert x.directional_effect==PortfolioDirectionalEffect.DIVERSIFICATION

def test_pf1b_short_overshoot_is_concentration(tmp_path):
    s=make_store(tmp_path)
    s.save_trade_opportunity(TradeOpportunity(opportunity_id="OPP-1",snapshot_id="SNAP-1",created_at=NOW,ticker="NVDA",direction=Direction.SHORT,horizon=TradingHorizon.SWING,confidence=.8,target_exposure_eur=400000,max_intended_loss_eur=1000,thesis="test"))
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T")
    x=r.exposure_context
    assert x.net_exposure_after_eur==pytest.approx(-240000)
    assert x.directional_effect==PortfolioDirectionalEffect.CONCENTRATION

def test_pf1b_equal_absolute_net_is_neutral(tmp_path):
    s=make_store(tmp_path)
    s.save_trade_opportunity(TradeOpportunity(opportunity_id="OPP-1",snapshot_id="SNAP-1",created_at=NOW,ticker="NVDA",direction=Direction.SHORT,horizon=TradingHorizon.SWING,confidence=.8,target_exposure_eur=320000,max_intended_loss_eur=1000,thesis="test"))
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T")
    assert r.exposure_context.directional_effect==PortfolioDirectionalEffect.NEUTRAL

def test_pf1b_unknown_target_has_no_projected_exposure(tmp_path):
    s=make_store(tmp_path); save_opp(s,target=None)
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T")
    x=r.exposure_context
    assert x.directional_effect==PortfolioDirectionalEffect.UNKNOWN
    assert x.gross_exposure_after_eur is None
    assert x.net_exposure_after_eur is None

def test_pf1b_gross_nav_before_after(tmp_path):
    s=make_store(tmp_path); save_opp(s,target=10000)
    r=PortfolioFilterService(s).assess("OPP-1",as_of=NOW,assessment_id="PFIT-T")
    x=r.exposure_context
    assert x.gross_exposure_before_pct_nav==pytest.approx(66.6666667)
    assert x.gross_exposure_after_pct_nav==pytest.approx(70.0)

def test_pf1b_context_contract_rejects_inconsistent_after():
    with pytest.raises(ValueError,match="gross_exposure_after_eur must equal"):
        PortfolioExposureContext(
            long_exposure_before_eur=180000,short_exposure_before_eur=20000,
            gross_exposure_before_eur=200000,net_exposure_before_eur=160000,
            long_exposure_after_eur=190000,short_exposure_after_eur=20000,
            gross_exposure_after_eur=999000,net_exposure_after_eur=170000,
            absolute_net_exposure_before_eur=160000,
            absolute_net_exposure_after_eur=170000,
            directional_effect=PortfolioDirectionalEffect.CONCENTRATION,
        )
