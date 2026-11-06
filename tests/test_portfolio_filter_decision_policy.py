from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.cio.models import (
    PortfolioCheckStatus,
    PortfolioConstraintCheck,
    PortfolioFitDecision,
)
from app.cio.portfolio_filter_service import PortfolioFilterService
from app.cio.portfolio_fit_scoring import (
    PortfolioFitComponentScore,
    PortfolioFitScoringResult,
)

NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def check(status, code="X"):
    return PortfolioConstraintCheck(code=code, status=status)


def scoring(score, coverage=100.0, analytical=100.0):
    return PortfolioFitScoringResult(
        portfolio_fit_score=score,
        scoring_coverage_pct=coverage,
        analytical_coverage_pct=analytical,
        components=(),
    )


def service():
    return PortfolioFilterService(SimpleNamespace())


def test_hard_fail_dominates_even_high_score():
    d=service()._derive_decision([check(PortfolioCheckStatus.FAIL)], [], scoring(99))
    assert d == PortfolioFitDecision.REJECT


def test_low_scoring_coverage_is_unknown():
    d=service()._derive_decision([check(PortfolioCheckStatus.PASS)], [], scoring(None, 69.99))
    assert d == PortfolioFitDecision.UNKNOWN


@pytest.mark.parametrize("value", [0.0, 34.999999])
def test_score_below_35_rejects(value):
    d=service()._derive_decision([check(PortfolioCheckStatus.PASS)], [], scoring(value))
    assert d == PortfolioFitDecision.REJECT


def test_score_exactly_35_is_not_rejected():
    d=service()._derive_decision([check(PortfolioCheckStatus.PASS)], [], scoring(35))
    assert d == PortfolioFitDecision.PASS_WITH_WARNING


def test_configured_unknown_degrades_high_score_to_warning():
    d=service()._derive_decision(
        [check(PortfolioCheckStatus.PASS, "A"), check(PortfolioCheckStatus.UNKNOWN, "B")],
        [], scoring(90),
    )
    assert d == PortfolioFitDecision.PASS_WITH_WARNING


def test_analytical_coverage_below_70_degrades_high_score():
    d=service()._derive_decision([check(PortfolioCheckStatus.PASS)], [], scoring(90, analytical=69.99))
    assert d == PortfolioFitDecision.PASS_WITH_WARNING


def test_existing_warning_degrades_high_score():
    d=service()._derive_decision([check(PortfolioCheckStatus.PASS)], ["diagnostic"], scoring(90))
    assert d == PortfolioFitDecision.PASS_WITH_WARNING


def test_score_exactly_70_passes_when_clean():
    d=service()._derive_decision([check(PortfolioCheckStatus.PASS)], [], scoring(70))
    assert d == PortfolioFitDecision.PASS


def test_score_between_35_and_70_warns():
    d=service()._derive_decision([check(PortfolioCheckStatus.PASS)], [], scoring(50))
    assert d == PortfolioFitDecision.PASS_WITH_WARNING


def test_legacy_behavior_preserved_without_scoring_engine():
    d=service()._derive_decision(
        [check(PortfolioCheckStatus.PASS, "A"), check(PortfolioCheckStatus.UNKNOWN, "B")],
        [], None,
    )
    assert d == PortfolioFitDecision.PASS_WITH_WARNING
