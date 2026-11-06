from __future__ import annotations

from types import SimpleNamespace

from app.cio.models import Direction, PortfolioFitDecision
from app.cio.portfolio_filter_cli import (
    assess_portfolio_fit_cli,
    show_portfolio_fit_cli,
)


class FakeStore:
    def __init__(self):
        self.opportunity = SimpleNamespace(
            opportunity_id="OPP-1",
        )
        self.assessment = None

    def get_trade_opportunity(self, opportunity_id):
        if opportunity_id == "OPP-1":
            return self.opportunity
        return None

    def get_portfolio_fit_assessment(self, assessment_id):
        if (
            self.assessment is not None
            and self.assessment.assessment_id == assessment_id
        ):
            return self.assessment
        return None


class FakeService:
    def __init__(self, store):
        self.store = store

    def assess_and_persist(
        self,
        opportunity_id,
        *,
        assessment_id=None,
    ):
        assessment = SimpleNamespace(
            assessment_id=assessment_id or "PFIT-GENERATED",
            opportunity_id=opportunity_id,
            snapshot_id="SNAP-1",
            risk_state_id="RISK-1",
            account_state_id="ACC-1",
            ticker="QCOM",
            direction=Direction.LONG,
            decision=PortfolioFitDecision.PASS_WITH_WARNING,
            portfolio_fit_score=72.5,
            score_breakdown=None,
            exposure_context=None,
            constraint_checks=[],
            warnings=["test warning"],
            rationale="test rationale",
        )
        self.store.assessment = assessment
        return assessment


def fake_builder(store):
    return FakeService(store)


def test_assess_cli_persists_and_prints_summary(capsys):
    store = FakeStore()

    assessment = assess_portfolio_fit_cli(
        store,
        "OPP-1",
        assessment_id="PFIT-EXPLICIT",
        service_builder=fake_builder,
    )

    assert assessment is store.assessment
    assert assessment.assessment_id == "PFIT-EXPLICIT"

    out = capsys.readouterr().out
    assert "CIO PORTFOLIO FILTER" in out
    assert "PFIT-EXPLICIT" in out
    assert "PASS_WITH_WARNING" in out
    assert "72.50" in out
    assert "No lifecycle advancement was performed." in out


def test_assess_cli_missing_opportunity_fails_without_service(capsys):
    store = FakeStore()

    assessment = assess_portfolio_fit_cli(
        store,
        "OPP-MISSING",
        service_builder=lambda _store: (_ for _ in ()).throw(
            AssertionError("service must not be built")
        ),
    )

    assert assessment is None
    out = capsys.readouterr().out
    assert "Trade opportunity not found" in out


def test_show_cli_uses_exact_assessment_id(capsys):
    store = FakeStore()
    FakeService(store).assess_and_persist(
        "OPP-1",
        assessment_id="PFIT-1",
    )

    result = show_portfolio_fit_cli(
        store,
        "PFIT-1",
    )

    assert result is store.assessment
    out = capsys.readouterr().out
    assert "CIO PORTFOLIO FIT ASSESSMENT" in out
    assert "PFIT-1" in out


def test_show_cli_missing_assessment(capsys):
    store = FakeStore()

    result = show_portfolio_fit_cli(
        store,
        "PFIT-MISSING",
    )

    assert result is None
    out = capsys.readouterr().out
    assert "Portfolio fit assessment not found" in out
