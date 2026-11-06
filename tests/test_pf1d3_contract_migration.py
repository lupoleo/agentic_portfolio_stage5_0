from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from app.cio.models import (
    Direction, PortfolioCheckStatus, PortfolioConstraintCheck,
    PortfolioFitAssessment, PortfolioFitDecision, PortfolioFitScoreBreakdown,
)
NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)
def check(status):
    return PortfolioConstraintCheck(code="X", status=status)
def make(**overrides):
    data=dict(assessment_id="A",opportunity_id="O",snapshot_id="S",created_at=NOW,
      ticker="QCOM",direction=Direction.LONG,target_exposure_eur=10000.0,
      decision=PortfolioFitDecision.PASS_WITH_WARNING,
      constraint_checks=[check(PortfolioCheckStatus.PASS)],warnings=[],
      portfolio_fit_score=50.0,rationale="test")
    data.update(overrides)
    return PortfolioFitAssessment(**data)
def breakdown(c):
    return PortfolioFitScoreBreakdown(scoring_coverage_pct=c,analytical_coverage_pct=90.0,components=[])
def test_score_reject_without_fail_is_valid():
    assert make(decision=PortfolioFitDecision.REJECT,portfolio_fit_score=34.99).decision == PortfolioFitDecision.REJECT
def test_reject_without_fail_or_low_score_invalid():
    with pytest.raises(ValidationError): make(decision=PortfolioFitDecision.REJECT,portfolio_fit_score=35.0)
def test_fail_still_forces_reject():
    with pytest.raises(ValidationError): make(decision=PortfolioFitDecision.PASS_WITH_WARNING,constraint_checks=[check(PortfolioCheckStatus.FAIL)],portfolio_fit_score=90)
def test_low_coverage_unknown_with_pass_valid():
    assert make(decision=PortfolioFitDecision.UNKNOWN,portfolio_fit_score=None,score_breakdown=breakdown(69.99)).decision == PortfolioFitDecision.UNKNOWN
def test_unknown_with_pass_and_sufficient_coverage_invalid():
    with pytest.raises(ValidationError): make(decision=PortfolioFitDecision.UNKNOWN,portfolio_fit_score=50,score_breakdown=breakdown(70))
def test_unknown_with_fail_invalid():
    with pytest.raises(ValidationError): make(decision=PortfolioFitDecision.UNKNOWN,constraint_checks=[check(PortfolioCheckStatus.FAIL)],portfolio_fit_score=None,score_breakdown=breakdown(60))
