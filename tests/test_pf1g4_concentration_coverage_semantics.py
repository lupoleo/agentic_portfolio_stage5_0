from __future__ import annotations
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from app.analysis.marginal_risk import _gross_weight_concentration
from app.cio.models import (
    AccountState, Currency, CurrencyCash, DataSource, Direction,
    ExecutionSide, PortfolioRiskState, PortfolioSnapshot,
    RiskConstraints, TradeProposal,
)
from app.cio.portfolio_simulator import PortfolioRiskSimulator

NOW = datetime(2026, 9, 13, 19, 30, tzinfo=timezone.utc)

def _p(x):
    return SimpleNamespace(position=SimpleNamespace(market_value_eur=x))

def test_pf1g4_stage2_gross_weight_concentration():
    top5, effective = _gross_weight_concentration([_p(40), _p(30), _p(20), _p(10)])
    assert top5 == pytest.approx(100.0)
    assert effective == pytest.approx(1/(.4**2+.3**2+.2**2+.1**2))

def test_pf1g4_short_does_not_cancel_concentration():
    top5, effective = _gross_weight_concentration([_p(60), _p(-40)])
    assert top5 == pytest.approx(100.0)
    assert effective == pytest.approx(1/(.6**2+.4**2))

def _snapshot():
    return PortfolioSnapshot(
        snapshot_id="SNAP-PF1G4", timestamp=NOW,
        source_file="data/input/portafoglio-export.xlsx",
        source_file_hash="PF1G4", quant_engine_version="2.5",
        analyzed_positions=4, gross_exposure_eur=100000,
        net_exposure_eur=90000, account_state_id="ACC-PF1G4",
    )

def _before():
    return PortfolioRiskState(
        gross_exposure_eur=100000, net_exposure_eur=90000,
        long_exposure_eur=95000, short_exposure_eur=5000,
        portfolio_volatility_pct=15, portfolio_beta=.8,
        var_95_1d_eur=4000, cvar_95_1d_eur=6000,
        top5_concentration_pct=80, effective_positions=4,
        analytical_coverage_pct=90,
    )

def _proposal():
    return TradeProposal(
        proposal_id="PROP-PF1G4", opportunity_id="OPP-PF1G4",
        snapshot_id="SNAP-PF1G4", created_at=NOW, ticker="QCOM",
        direction=Direction.SHORT, instrument_id="FIN-QCOM",
        sizing_id="SIZ-PF1G4", execution_side=ExecutionSide.SELL_SHORT,
        quantity=10, reference_price=100, currency=Currency.USD,
        entry_type="MARKET", gross_exposure_eur=10000,
    )

def _account():
    return AccountState(
        account_state_id="ACC-PF1G4", timestamp=NOW,
        cash=[CurrencyCash(currency=Currency.EUR, available=20000, reserve=1000)],
        constraints=RiskConstraints(), source=DataSource.OPERATOR,
    )

def _m(a,b):
    return SimpleNamespace(before=a, after=b, delta=b-a)

def _result(top5=72.5, effective=5.2, coverage=92.5):
    return SimpleNamespace(
        volatility_pct=_m(15,14.25), beta=_m(.8,.71),
        var_95_1d_eur=_m(4000,3750), cvar_95_1d_eur=_m(6000,5550),
        analytical_coverage_before_pct=90,
        analytical_coverage_after_pct=coverage,
        top5_concentration_before_pct=80,
        top5_concentration_after_pct=top5,
        effective_positions_before=4,
        effective_positions_after=effective,
    )

class Adapter:
    def __init__(self, result): self.result=result
    def assess(self, *, snapshot, proposal): return self.result

def test_pf1g4_v2_consumes_concentration_and_coverage():
    s = PortfolioRiskSimulator(
        marginal_risk_adapter=Adapter(_result())
    ).simulate(
        snapshot=_snapshot(), proposal=_proposal(),
        before=_before(), account_state=_account(),
    )
    assert s.after.top5_concentration_pct == pytest.approx(72.5)
    assert s.after.effective_positions == pytest.approx(5.2)
    assert s.after.analytical_coverage_pct == pytest.approx(92.5)
    assert s.delta.top5_concentration_pct == pytest.approx(-7.5)
    assert s.delta.effective_positions == pytest.approx(1.2)
    assert s.delta.analytical_coverage_pct == pytest.approx(2.5)
    assert "concentration after is not yet" not in " ".join(s.warnings).lower()

def test_pf1g4_missing_v2_concentration_uses_explicit_compatibility_fallback():
    r = _result()
    r.top5_concentration_after_pct = None
    before = _before()

    simulation = PortfolioRiskSimulator(
        marginal_risk_adapter=Adapter(r)
    ).simulate(
        snapshot=_snapshot(), proposal=_proposal(),
        before=before, account_state=_account(),
    )

    assert (
        simulation.after.top5_concentration_pct
        == before.top5_concentration_pct
    )
    assert (
        simulation.after.effective_positions
        == before.effective_positions
    )
    warning_text = " ".join(simulation.warnings).lower()
    assert "pf-1g.4 concentration metrics are unavailable" in warning_text

def test_pf1g4_v1_concentration_compatibility():
    before = _before()
    s = PortfolioRiskSimulator().simulate(
        snapshot=_snapshot(), proposal=_proposal(),
        before=before, account_state=_account(),
    )
    assert s.after.top5_concentration_pct == before.top5_concentration_pct
    assert s.after.effective_positions == before.effective_positions
    assert "concentration after is not yet" in " ".join(s.warnings).lower()
