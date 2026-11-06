from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse

from app.cio.storage import Stage3Store
from app.cio.portfolio_marginal_risk_provider import (
    build_canonical_portfolio_filter_service,
)

DEFAULT_DB = Path("data/state/portfolio_cio.db")
DEFAULT_OPPORTUNITY_ID = "PF1D4-QCOM-LONG-10000"
EXPECTED_SNAPSHOT_ID = "SNAP-20260910-194305-499c14"


def fail(message: str) -> None:
    print(f"PF-1E.4 LIVE GATE: FAIL — {message}")
    raise SystemExit(1)


def canonical_json(model):
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PF-1E.4 live persistence acceptance gate."
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="Stage 3 SQLite database path.",
    )
    parser.add_argument(
        "--opportunity-id",
        default=DEFAULT_OPPORTUNITY_ID,
        help="Existing persisted TradeOpportunity to assess.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)

    if not db_path.exists():
        fail(f"canonical DB not found: {db_path}")

    store = Stage3Store(db_path)

    opportunity = store.get_trade_opportunity(args.opportunity_id)

    if opportunity is None:
        fail(
            "existing opportunity not found; "
            "this gate will not create a synthetic TradeOpportunity"
        )

    print("PF-1E.4 live persistence validation")
    print(f"DB: {db_path}")
    print(f"Opportunity: {opportunity.opportunity_id}")
    print(f"Ticker/direction: {opportunity.ticker} {opportunity.direction}")
    print(f"Snapshot: {opportunity.snapshot_id}")

    if (
        args.opportunity_id == DEFAULT_OPPORTUNITY_ID
        and opportunity.snapshot_id != EXPECTED_SNAPSHOT_ID
    ):
        fail(
            f"default opportunity snapshot drifted: "
            f"{opportunity.snapshot_id} != {EXPECTED_SNAPSHOT_ID}"
        )

    service = build_canonical_portfolio_filter_service(store)

    if getattr(service, "scoring_engine", None) is None:
        fail(
            "canonical PortfolioFilterService has no scoring_engine"
        )

    now = datetime.now(timezone.utc)

    assessment_id = (
        "PF1E4-"
        + opportunity.ticker.upper()
        + "-"
        + now.strftime("%Y%m%d-%H%M%S-%f")
    )

    before_list = store.list_portfolio_fit_assessments(
        opportunity.opportunity_id
    )
    before_count = len(before_list)

    assessment = service.assess_and_persist(
        opportunity.opportunity_id,
        as_of=now,
        assessment_id=assessment_id,
    )

    loaded = store.get_portfolio_fit_assessment(
        assessment.assessment_id
    )

    if loaded is None:
        fail("persisted assessment cannot be read back by assessment_id")

    latest = store.get_latest_portfolio_fit_assessment(
        opportunity.opportunity_id
    )

    if latest is None:
        fail("latest persisted assessment cannot be resolved")

    listed = store.list_portfolio_fit_assessments(
        opportunity.opportunity_id
    )

    checks = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append((name, bool(condition), detail))

        status = "PASS" if condition else "FAIL"
        suffix = f" — {detail}" if detail else ""

        print(f"[{status}] {name}{suffix}")

    check(
        "round-trip canonical payload identity",
        canonical_json(loaded) == canonical_json(assessment),
    )

    check(
        "assessment_id identity",
        loaded.assessment_id == assessment.assessment_id,
        loaded.assessment_id,
    )

    check(
        "opportunity_id identity",
        loaded.opportunity_id == opportunity.opportunity_id,
        loaded.opportunity_id,
    )

    check(
        "snapshot_id identity",
        loaded.snapshot_id == opportunity.snapshot_id,
        loaded.snapshot_id,
    )

    check(
        "risk_state_id preserved",
        loaded.risk_state_id == assessment.risk_state_id,
        str(loaded.risk_state_id),
    )

    check(
        "account_state_id preserved",
        loaded.account_state_id == assessment.account_state_id,
        str(loaded.account_state_id),
    )

    check(
        "ticker identity",
        loaded.ticker == assessment.ticker == opportunity.ticker.upper(),
        loaded.ticker,
    )

    check(
        "direction identity",
        loaded.direction == assessment.direction == opportunity.direction,
        str(loaded.direction),
    )

    check(
        "decision preserved",
        loaded.decision == assessment.decision,
        str(loaded.decision),
    )

    check(
        "portfolio_fit_score preserved",
        loaded.portfolio_fit_score == assessment.portfolio_fit_score,
        str(loaded.portfolio_fit_score),
    )

    check(
        "score_breakdown preserved",
        canonical_json(loaded.score_breakdown)
        == canonical_json(assessment.score_breakdown),
    )

    check(
        "exposure_context preserved",
        canonical_json(loaded.exposure_context)
        == canonical_json(assessment.exposure_context),
    )

    check(
        "marginal_risk_context preserved",
        canonical_json(loaded.marginal_risk_context)
        == canonical_json(assessment.marginal_risk_context),
    )

    check(
        "latest(opportunity_id) resolves persisted assessment",
        latest.assessment_id == assessment.assessment_id,
        latest.assessment_id,
    )

    check(
        "list count incremented by exactly one",
        len(listed) == before_count + 1,
        f"{before_count} -> {len(listed)}",
    )

    check(
        "list head is persisted assessment",
        bool(listed)
        and listed[0].assessment_id == assessment.assessment_id,
        listed[0].assessment_id if listed else "<empty>",
    )

    latest_account = store.get_latest_account_state()

    if latest_account is None:
        fail("no AccountState available after successful assessment")

    check(
        "decision-time AccountState is latest account",
        assessment.account_state_id == latest_account.account_state_id,
        str(assessment.account_state_id),
    )

    print()
    print("Persisted assessment summary")
    print(f"  assessment_id: {assessment.assessment_id}")
    print(f"  opportunity_id: {assessment.opportunity_id}")
    print(f"  snapshot_id: {assessment.snapshot_id}")
    print(f"  risk_state_id: {assessment.risk_state_id}")
    print(f"  account_state_id: {assessment.account_state_id}")
    print(f"  decision: {assessment.decision}")
    print(f"  portfolio_fit_score: {assessment.portfolio_fit_score}")

    if assessment.score_breakdown is not None:
        print(
            "  scoring_coverage_pct: "
            f"{assessment.score_breakdown.scoring_coverage_pct}"
        )
        print(
            "  analytical_coverage_pct: "
            f"{assessment.score_breakdown.analytical_coverage_pct}"
        )

    failures = [
        name
        for name, ok, _ in checks
        if not ok
    ]

    if failures:
        fail("; ".join(failures))

    print()
    print("PF-1E.4 LIVE GATE: PASS")


if __name__ == "__main__":
    main()