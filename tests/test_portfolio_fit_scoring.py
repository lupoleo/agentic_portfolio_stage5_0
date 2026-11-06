from __future__ import annotations

import math
import pytest

from app.cio.models import (
    Direction,
    PortfolioDirectionalEffect,
    PortfolioExposureContext,
    PortfolioMarginalRiskContext,
    PortfolioMetricDelta,
    PortfolioRiskState,
)
from app.cio.portfolio_fit_scoring import PortfolioFitScoringEngine


def before(*, net=100000.0, gross=100000.0, effective_positions=10.0, coverage=100.0):
    long = (gross + net) / 2.0
    short = (gross - net) / 2.0
    return PortfolioRiskState(
        gross_exposure_eur=gross,
        net_exposure_eur=net,
        long_exposure_eur=long,
        short_exposure_eur=short,
        portfolio_volatility_pct=10.0,
        portfolio_beta=0.5,
        var_95_1d_eur=2000.0,
        cvar_95_1d_eur=3000.0,
        top5_concentration_pct=50.0,
        effective_positions=effective_positions,
        analytical_coverage_pct=coverage,
    )


def exposure(*, target=10000.0, net_before=100000.0, net_after=110000.0, gross_before=100000.0):
    long_before=(gross_before+net_before)/2
    short_before=(gross_before-net_before)/2
    gross_after=gross_before+target
    long_after=(gross_after+net_after)/2
    short_after=(gross_after-net_after)/2
    abs_before=abs(net_before); abs_after=abs(net_after)
    effect = (
        PortfolioDirectionalEffect.DIVERSIFICATION if abs_after < abs_before
        else PortfolioDirectionalEffect.CONCENTRATION if abs_after > abs_before
        else PortfolioDirectionalEffect.NEUTRAL
    )
    return PortfolioExposureContext(
        long_exposure_before_eur=long_before,
        short_exposure_before_eur=short_before,
        gross_exposure_before_eur=gross_before,
        net_exposure_before_eur=net_before,
        long_exposure_after_eur=long_after,
        short_exposure_after_eur=short_after,
        gross_exposure_after_eur=gross_after,
        net_exposure_after_eur=net_after,
        absolute_net_exposure_before_eur=abs_before,
        absolute_net_exposure_after_eur=abs_after,
        directional_effect=effect,
    )


def delta(b, a):
    return PortfolioMetricDelta(before=b, after=a, delta=a-b)


def marginal(*, corr=0.0, vol=(10,10), beta=(.5,.5), var=(2000,2000), cvar=(3000,3000), coverage=100):
    return PortfolioMarginalRiskContext(
        candidate_symbol="CAND",
        volatility_pct=delta(*vol),
        beta=delta(*beta),
        var_95_1d_eur=delta(*var),
        cvar_95_1d_eur=delta(*cvar),
        candidate_correlation_to_portfolio=corr,
        analytical_coverage_before_pct=coverage,
        analytical_coverage_after_pct=coverage,
        observations_before=600,
        observations_after=600,
    )


def test_frozen_weights_sum_to_one():
    assert sum(PortfolioFitScoringEngine.WEIGHTS.values()) == pytest.approx(1.0)
    assert PortfolioFitScoringEngine.WEIGHTS == {
        "directional": .10, "concentration": .15, "correlation": .15,
        "volatility": .20, "beta": .15, "tail_risk": .25,
    }


def test_zero_risk_delta_is_neutral_for_risk_components():
    r=PortfolioFitScoringEngine().score(
        direction=Direction.LONG, target_exposure_eur=10000,
        before=before(), exposure_context=exposure(), marginal_risk_context=marginal(),
    )
    assert r.component("volatility").score == pytest.approx(50)
    assert r.component("beta").score == pytest.approx(50)
    assert r.component("tail_risk").score == pytest.approx(50)


def test_directional_full_concentration_and_full_diversification_boundaries():
    eng=PortfolioFitScoringEngine()
    concentrated=eng.score(
        direction=Direction.LONG,target_exposure_eur=10000,before=before(),
        exposure_context=exposure(net_after=110000),marginal_risk_context=marginal()
    )
    diversified=eng.score(
        direction=Direction.SHORT,target_exposure_eur=10000,before=before(),
        exposure_context=exposure(net_after=90000),marginal_risk_context=marginal()
    )
    assert concentrated.component("directional").score == pytest.approx(0)
    assert diversified.component("directional").score == pytest.approx(100)


def test_correlation_long_short_symmetry_for_net_long_portfolio():
    eng=PortfolioFitScoringEngine()
    common=dict(target_exposure_eur=10000,before=before(),exposure_context=exposure(),marginal_risk_context=marginal(corr=.5))
    long=eng.score(direction=Direction.LONG,**common)
    short=eng.score(direction=Direction.SHORT,**common)
    assert long.component("correlation").score == pytest.approx(25)
    assert short.component("correlation").score == pytest.approx(75)


def test_correlation_direction_reverses_for_net_short_portfolio():
    eng=PortfolioFitScoringEngine()
    b=before(net=-100000)
    ctx=exposure(net_before=-100000,net_after=-90000)
    common=dict(target_exposure_eur=10000,before=b,exposure_context=ctx,marginal_risk_context=marginal(corr=.5))
    assert eng.score(direction=Direction.LONG,**common).component("correlation").score == pytest.approx(75)
    assert eng.score(direction=Direction.SHORT,**common).component("correlation").score == pytest.approx(25)


def test_concentration_equal_effective_average_scores_50():
    # target/gross_after = 10k/110k = 1/11; effective_positions=11.
    r=PortfolioFitScoringEngine().score(
        direction=Direction.LONG,target_exposure_eur=10000,
        before=before(effective_positions=11),exposure_context=exposure(),
        marginal_risk_context=marginal(),
    )
    assert r.component("concentration").score == pytest.approx(50)


def test_risk_normalization_is_exposure_scale_invariant():
    eng=PortfolioFitScoringEngine()
    # 10% exposure causing +5% relative vol risk.
    r1=eng.score(
        direction=Direction.LONG,target_exposure_eur=10000,before=before(gross=100000),
        exposure_context=exposure(target=10000,gross_before=100000),
        marginal_risk_context=marginal(vol=(10,10.5)),
    )
    # 5% exposure causing +2.5% relative vol risk -> same efficiency.
    r2=eng.score(
        direction=Direction.LONG,target_exposure_eur=5000,before=before(gross=100000),
        exposure_context=exposure(target=5000,gross_before=100000,net_after=105000),
        marginal_risk_context=marginal(vol=(10,10.25)),
    )
    assert r1.component("volatility").score == pytest.approx(r2.component("volatility").score)


def test_beta_uses_absolute_beta_and_epsilon_floor():
    eng=PortfolioFitScoringEngine()
    r=eng.score(
        direction=Direction.LONG,target_exposure_eur=10000,before=before(),
        exposure_context=exposure(),
        marginal_risk_context=marginal(beta=(-.01,.0)),
    )
    assert r.component("beta").score > 50


def test_tail_score_is_40pct_var_60pct_cvar():
    eng=PortfolioFitScoringEngine()
    r=eng.score(
        direction=Direction.LONG,target_exposure_eur=10000,before=before(),
        exposure_context=exposure(),
        marginal_risk_context=marginal(var=(2000,2200),cvar=(3000,3000)),
    )
    var_eff=((2200-2000)/2000)/.1
    s_var=50-50*math.tanh(var_eff)
    expected=.4*s_var+.6*50
    assert r.component("tail_risk").score == pytest.approx(expected)


def test_unknown_is_excluded_not_neutralized_and_coverage_is_weighted():
    # No marginal context => directional + concentration only = 25% coverage.
    r=PortfolioFitScoringEngine().score(
        direction=Direction.LONG,target_exposure_eur=10000,
        before=before(),exposure_context=exposure(),marginal_risk_context=None,
    )
    assert r.scoring_coverage_pct == pytest.approx(25)
    assert r.portfolio_fit_score is None
    assert r.component("correlation").score is None


def test_exact_70pct_coverage_is_scorable():
    # directional 10 + correlation 15 + volatility 20 + beta 15 + tail 25 = 85 normally.
    # Make concentration UNKNOWN by effective_positions=0; still 85%.
    r=PortfolioFitScoringEngine().score(
        direction=Direction.LONG,target_exposure_eur=10000,
        before=before(effective_positions=0),exposure_context=exposure(),
        marginal_risk_context=marginal(),
    )
    assert r.scoring_coverage_pct == pytest.approx(85)
    assert r.portfolio_fit_score is not None


def test_analytical_coverage_is_metadata_not_score_component():
    eng=PortfolioFitScoringEngine()
    common=dict(
        direction=Direction.LONG,target_exposure_eur=10000,
        before=before(),exposure_context=exposure(),
    )
    a=eng.score(marginal_risk_context=marginal(coverage=95),**common)
    b=eng.score(marginal_risk_context=marginal(coverage=60),**common)
    assert a.portfolio_fit_score == pytest.approx(b.portfolio_fit_score)
    assert a.analytical_coverage_pct == 95
    assert b.analytical_coverage_pct == 60


def test_all_component_scores_and_final_score_are_bounded():
    r=PortfolioFitScoringEngine().score(
        direction=Direction.LONG,target_exposure_eur=10000,before=before(),
        exposure_context=exposure(),
        marginal_risk_context=marginal(corr=1,vol=(10,1000),beta=(.5,100),var=(2000,200000),cvar=(3000,300000)),
    )
    assert r.portfolio_fit_score is not None
    assert 0 <= r.portfolio_fit_score <= 100
    assert all(x.score is None or 0 <= x.score <= 100 for x in r.components)
