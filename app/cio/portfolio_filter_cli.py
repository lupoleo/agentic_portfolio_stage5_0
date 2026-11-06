from __future__ import annotations

from typing import Callable

from app.cio.portfolio_marginal_risk_provider import (
    build_canonical_portfolio_filter_service,
)
from app.cio.storage import Stage3Store


def assess_portfolio_fit_cli(
    store: Stage3Store,
    opportunity_id: str,
    *,
    assessment_id: str | None = None,
    service_builder: Callable = build_canonical_portfolio_filter_service,
) -> object | None:
    opportunity = store.get_trade_opportunity(opportunity_id)

    if opportunity is None:
        print(
            "Trade opportunity not found: "
            f"{opportunity_id}"
        )
        return None

    service = service_builder(store)

    assessment = service.assess_and_persist(
        opportunity_id,
        assessment_id=assessment_id,
    )

    print("\n=== CIO PORTFOLIO FILTER ===\n")
    print(f"Assessment ID:       {assessment.assessment_id}")
    print(f"Opportunity ID:      {assessment.opportunity_id}")
    print(f"Snapshot ID:         {assessment.snapshot_id}")
    print(f"Risk State ID:       {assessment.risk_state_id}")
    print(f"Account State ID:    {assessment.account_state_id}")
    print(f"Ticker:              {assessment.ticker}")
    print(f"Direction:           {assessment.direction.value}")
    print(f"Decision:            {assessment.decision.value}")

    score = (
        "UNKNOWN"
        if assessment.portfolio_fit_score is None
        else f"{assessment.portfolio_fit_score:.2f}"
    )
    print(f"Portfolio Fit Score: {score}")

    breakdown = getattr(
        assessment,
        "score_breakdown",
        None,
    )

    if breakdown is not None:
        scoring_coverage = getattr(
            breakdown,
            "scoring_coverage_pct",
            None,
        )
        analytical_coverage = getattr(
            breakdown,
            "analytical_coverage_pct",
            None,
        )

        if scoring_coverage is not None:
            print(
                "Scoring Coverage:    "
                f"{scoring_coverage:.2f}%"
            )

        if analytical_coverage is not None:
            print(
                "Analytical Coverage: "
                f"{analytical_coverage:.2f}%"
            )

    exposure = getattr(
        assessment,
        "exposure_context",
        None,
    )

    if exposure is not None:
        print(
            "Directional Effect:   "
            f"{exposure.directional_effect.value}"
        )

        if exposure.gross_exposure_after_eur is not None:
            print(
                "Gross Exposure:      "
                f"{exposure.gross_exposure_before_eur:.2f}"
                " -> "
                f"{exposure.gross_exposure_after_eur:.2f} EUR"
            )

        if exposure.net_exposure_after_eur is not None:
            print(
                "Net Exposure:        "
                f"{exposure.net_exposure_before_eur:.2f}"
                " -> "
                f"{exposure.net_exposure_after_eur:.2f} EUR"
            )

    print("\nConstraint checks")
    print("-" * 72)

    for check in assessment.constraint_checks:
        projected = (
            "-"
            if check.projected_value is None
            else f"{check.projected_value}"
        )
        limit = (
            "-"
            if check.limit_value is None
            else f"{check.limit_value}"
        )

        print(
            f"{check.code:<34} "
            f"{check.status.value:<16} "
            f"projected={projected} "
            f"limit={limit}"
        )

    if assessment.warnings:
        print("\nWarnings")
        print("-" * 72)
        for warning in assessment.warnings:
            print(f"- {warning}")

    print("\nRationale")
    print("-" * 72)
    print(assessment.rationale)

    print(
        "\nPersisted PortfolioFitAssessment. "
        "No lifecycle advancement was performed."
    )

    return assessment


def show_portfolio_fit_cli(
    store: Stage3Store,
    assessment_id: str,
) -> object | None:
    assessment = store.get_portfolio_fit_assessment(
        assessment_id
    )

    if assessment is None:
        print(
            "Portfolio fit assessment not found: "
            f"{assessment_id}"
        )
        return None

    print("\n=== CIO PORTFOLIO FIT ASSESSMENT ===\n")
    print(f"Assessment ID:       {assessment.assessment_id}")
    print(f"Opportunity ID:      {assessment.opportunity_id}")
    print(f"Snapshot ID:         {assessment.snapshot_id}")
    print(f"Risk State ID:       {assessment.risk_state_id}")
    print(f"Account State ID:    {assessment.account_state_id}")
    print(f"Ticker:              {assessment.ticker}")
    print(f"Direction:           {assessment.direction.value}")
    print(f"Decision:            {assessment.decision.value}")

    score = (
        "UNKNOWN"
        if assessment.portfolio_fit_score is None
        else f"{assessment.portfolio_fit_score:.2f}"
    )
    print(f"Portfolio Fit Score: {score}")

    if assessment.warnings:
        print("\nWarnings")
        print("-" * 72)
        for warning in assessment.warnings:
            print(f"- {warning}")

    print("\nRationale")
    print("-" * 72)
    print(assessment.rationale)

    return assessment
