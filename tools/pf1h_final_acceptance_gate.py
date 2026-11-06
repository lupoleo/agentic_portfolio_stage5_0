from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cio.models import (
    Currency,
    Direction,
    ExecutionSide,
    PortfolioDirectionalEffect,
    PortfolioFitDecision,
    TradeProposal,
)
from app.cio.portfolio_marginal_risk_provider import (
    build_canonical_portfolio_filter_service,
)
from app.cio.portfolio_simulation_factory import (
    build_canonical_portfolio_simulation_service,
)
from app.cio.storage import Stage3Store


DEFAULT_DB = Path("data/state/portfolio_cio.db")
DEFAULT_LONG_OPPORTUNITY = "PF1D4-QCOM-LONG-10000"
DEFAULT_SHORT_OPPORTUNITY = "PF1D4-QCOM-SHORT-10000"


def _proposal_from_opportunity(opportunity) -> TradeProposal:
    exposure = opportunity.target_exposure_eur
    if exposure is None or exposure <= 0:
        raise RuntimeError(
            f"{opportunity.opportunity_id} has no positive target_exposure_eur"
        )

    side = (
        ExecutionSide.BUY
        if opportunity.direction == Direction.LONG
        else ExecutionSide.SELL_SHORT
    )

    return TradeProposal(
        proposal_id=f"PF1H-{opportunity.opportunity_id}",
        opportunity_id=opportunity.opportunity_id,
        snapshot_id=opportunity.snapshot_id,
        created_at=datetime.now(timezone.utc),
        ticker=opportunity.ticker,
        direction=opportunity.direction,
        instrument_id=f"PF1H-LIVE-{opportunity.ticker}",
        sizing_id=f"PF1H-SIZE-{opportunity.direction.value}",
        execution_side=side,
        quantity=1.0,
        reference_price=float(exposure),
        currency=Currency.EUR,
        fx_to_eur=1.0,
        entry_type="MARKET",
        gross_exposure_eur=float(exposure),
        estimated_capital_required_eur=float(exposure),
        estimated_max_loss_eur=opportunity.max_intended_loss_eur,
    )


def _assert_close(label: str, a: float, b: float, tolerance: float) -> None:
    delta = abs(a - b)
    if delta > tolerance:
        raise AssertionError(
            f"{label}: PF/Simulator disagreement {a} vs {b}; "
            f"|delta|={delta} > tolerance={tolerance}"
        )


def _fmt(value) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "PF-1H final Portfolio Filter acceptance. "
            "Read-only live validation of Portfolio Filter + lifecycle "
            "persistence artifacts + shared quantitative consistency with "
            "Portfolio Simulator V2."
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--long-opportunity",
        default=DEFAULT_LONG_OPPORTUNITY,
    )
    parser.add_argument(
        "--short-opportunity",
        default=DEFAULT_SHORT_OPPORTUNITY,
    )
    args = parser.parse_args()

    store = Stage3Store(args.db)

    snapshot = store.get_latest_portfolio_snapshot()
    if snapshot is None:
        raise RuntimeError(
            "No PortfolioSnapshot found. Run Portfolio Analysis first."
        )

    risk_record = store.get_latest_portfolio_risk_state(
        snapshot.snapshot_id
    )
    if risk_record is None:
        raise RuntimeError(
            f"No risk state for latest snapshot {snapshot.snapshot_id}"
        )

    account = store.get_latest_account_state()
    if account is None and snapshot.account_state_id is not None:
        account = store.get_account_state(snapshot.account_state_id)
    if account is None:
        raise RuntimeError("No AccountState available.")

    long_opp = store.get_trade_opportunity(args.long_opportunity)
    short_opp = store.get_trade_opportunity(args.short_opportunity)
    if long_opp is None:
        raise RuntimeError(
            f"TradeOpportunity not found: {args.long_opportunity}"
        )
    if short_opp is None:
        raise RuntimeError(
            f"TradeOpportunity not found: {args.short_opportunity}"
        )

    # ------------------------------------------------------------
    # Provenance / current-state acceptance
    # ------------------------------------------------------------
    assert long_opp.snapshot_id == snapshot.snapshot_id
    assert short_opp.snapshot_id == snapshot.snapshot_id
    assert long_opp.ticker.upper() == short_opp.ticker.upper()
    assert long_opp.direction == Direction.LONG
    assert short_opp.direction == Direction.SHORT
    assert long_opp.target_exposure_eur is not None
    assert short_opp.target_exposure_eur is not None
    assert abs(
        long_opp.target_exposure_eur
        - short_opp.target_exposure_eur
    ) <= 0.01

    latest_long_persisted_before = (
        store.get_latest_portfolio_fit_assessment(
            long_opp.opportunity_id
        )
    )
    latest_short_persisted_before = (
        store.get_latest_portfolio_fit_assessment(
            short_opp.opportunity_id
        )
    )

    if latest_long_persisted_before is None:
        raise RuntimeError(
            "No persisted PortfolioFitAssessment exists for LONG opportunity."
        )
    if latest_short_persisted_before is None:
        raise RuntimeError(
            "No persisted PortfolioFitAssessment exists for SHORT opportunity."
        )

    # ------------------------------------------------------------
    # Fresh, side-effect-free canonical Portfolio Filter
    # ------------------------------------------------------------
    filter_service = build_canonical_portfolio_filter_service(store)

    long_pf = filter_service.assess(
        long_opp.opportunity_id,
        assessment_id="PF1H-LIVE-LONG-NOT-PERSISTED",
    )
    short_pf = filter_service.assess(
        short_opp.opportunity_id,
        assessment_id="PF1H-LIVE-SHORT-NOT-PERSISTED",
    )

    assert long_pf.snapshot_id == snapshot.snapshot_id
    assert short_pf.snapshot_id == snapshot.snapshot_id

    assert long_pf.marginal_risk_context is not None
    assert short_pf.marginal_risk_context is not None
    assert long_pf.exposure_context is not None
    assert short_pf.exposure_context is not None
    assert long_pf.score_breakdown is not None
    assert short_pf.score_breakdown is not None

    assert (
        long_pf.exposure_context.directional_effect
        == PortfolioDirectionalEffect.CONCENTRATION
    )
    assert (
        short_pf.exposure_context.directional_effect
        == PortfolioDirectionalEffect.DIVERSIFICATION
    )

    # Frozen PF-1D policy on this wide-separation live control pair.
    assert long_pf.decision == PortfolioFitDecision.REJECT
    assert long_pf.portfolio_fit_score is not None
    assert long_pf.portfolio_fit_score < 35.0

    assert short_pf.decision in {
        PortfolioFitDecision.PASS,
        PortfolioFitDecision.PASS_WITH_WARNING,
    }
    assert short_pf.portfolio_fit_score is not None
    assert short_pf.portfolio_fit_score >= 70.0

    assert long_pf.score_breakdown.scoring_coverage_pct >= 70.0
    assert short_pf.score_breakdown.scoring_coverage_pct >= 70.0
    assert (
        long_pf.score_breakdown.analytical_coverage_pct is not None
        and long_pf.score_breakdown.analytical_coverage_pct >= 70.0
    )
    assert (
        short_pf.score_breakdown.analytical_coverage_pct is not None
        and short_pf.score_breakdown.analytical_coverage_pct >= 70.0
    )

    # Existing persistence artifacts from PF-1E/PF-1F remain tied to the
    # current snapshot and preserve the same broad policy result.
    assert latest_long_persisted_before.snapshot_id == snapshot.snapshot_id
    assert latest_short_persisted_before.snapshot_id == snapshot.snapshot_id
    assert (
        latest_long_persisted_before.decision
        == PortfolioFitDecision.REJECT
    )
    assert latest_short_persisted_before.decision in {
        PortfolioFitDecision.PASS,
        PortfolioFitDecision.PASS_WITH_WARNING,
    }

    # ------------------------------------------------------------
    # Shared quantitative truth: PF vs Simulator V2
    # ------------------------------------------------------------
    sim_service = build_canonical_portfolio_simulation_service(store)
    simulator = sim_service.simulator
    assert simulator.marginal_risk_adapter is not None

    long_proposal = _proposal_from_opportunity(long_opp)
    short_proposal = _proposal_from_opportunity(short_opp)

    long_sim = simulator.simulate(
        snapshot=snapshot,
        proposal=long_proposal,
        before=risk_record.state,
        account_state=account,
    )
    short_sim = simulator.simulate(
        snapshot=snapshot,
        proposal=short_proposal,
        before=risk_record.state,
        account_state=account,
    )

    for label, pf, sim in (
        ("LONG", long_pf, long_sim),
        ("SHORT", short_pf, short_sim),
    ):
        ctx = pf.marginal_risk_context
        assert ctx is not None

        # Providers may download independently from Yahoo. Historical
        # adjustments/backfills can therefore introduce tiny numerical drift.
        # These tolerances detect model divergence without requiring bitwise
        # identity from an external market-data source.
        _assert_close(
            f"{label} volatility",
            ctx.volatility_pct.after,
            sim.after.portfolio_volatility_pct,
            0.10,
        )
        _assert_close(
            f"{label} beta",
            ctx.beta.after,
            sim.after.portfolio_beta,
            0.01,
        )
        _assert_close(
            f"{label} VaR95",
            ctx.var_95_1d_eur.after,
            sim.after.var_95_1d_eur,
            50.0,
        )
        _assert_close(
            f"{label} CVaR95",
            ctx.cvar_95_1d_eur.after,
            sim.after.cvar_95_1d_eur,
            50.0,
        )
        _assert_close(
            f"{label} analytical coverage",
            ctx.analytical_coverage_after_pct,
            sim.after.analytical_coverage_pct,
            0.50,
        )

    # Directional relationship must agree in both consumers of the shared
    # PortfolioMarginalRiskEngine.
    assert (
        long_pf.marginal_risk_context.volatility_pct.after
        > short_pf.marginal_risk_context.volatility_pct.after
    )
    assert (
        long_pf.marginal_risk_context.beta.after
        > short_pf.marginal_risk_context.beta.after
    )
    assert (
        long_pf.marginal_risk_context.var_95_1d_eur.after
        > short_pf.marginal_risk_context.var_95_1d_eur.after
    )
    assert (
        long_pf.marginal_risk_context.cvar_95_1d_eur.after
        > short_pf.marginal_risk_context.cvar_95_1d_eur.after
    )

    assert (
        long_sim.after.portfolio_volatility_pct
        > short_sim.after.portfolio_volatility_pct
    )
    assert (
        long_sim.after.portfolio_beta
        > short_sim.after.portfolio_beta
    )
    assert (
        long_sim.after.var_95_1d_eur
        > short_sim.after.var_95_1d_eur
    )
    assert (
        long_sim.after.cvar_95_1d_eur
        > short_sim.after.cvar_95_1d_eur
    )

    # ------------------------------------------------------------
    # Read-only guarantee
    # ------------------------------------------------------------
    assert (
        store.get_portfolio_fit_assessment(
            "PF1H-LIVE-LONG-NOT-PERSISTED"
        )
        is None
    )
    assert (
        store.get_portfolio_fit_assessment(
            "PF1H-LIVE-SHORT-NOT-PERSISTED"
        )
        is None
    )
    assert store.get_trade_proposal(long_proposal.proposal_id) is None
    assert store.get_trade_proposal(short_proposal.proposal_id) is None

    latest_long_persisted_after = (
        store.get_latest_portfolio_fit_assessment(
            long_opp.opportunity_id
        )
    )
    latest_short_persisted_after = (
        store.get_latest_portfolio_fit_assessment(
            short_opp.opportunity_id
        )
    )

    assert (
        latest_long_persisted_after.assessment_id
        == latest_long_persisted_before.assessment_id
    )
    assert (
        latest_short_persisted_after.assessment_id
        == latest_short_persisted_before.assessment_id
    )

    # ------------------------------------------------------------
    # Audit output
    # ------------------------------------------------------------
    print("=== PF-1H FINAL PORTFOLIO FILTER ACCEPTANCE ===")
    print(f"DB: {args.db}")
    print(f"Snapshot: {snapshot.snapshot_id}")
    print(f"Risk state: {risk_record.risk_state_id}")
    print(f"Account state: {account.account_state_id}")
    print(
        f"Control pair: {long_opp.ticker.upper()} "
        f"EUR {long_opp.target_exposure_eur:.2f}"
    )
    print()

    for label, pf, sim in (
        ("LONG", long_pf, long_sim),
        ("SHORT", short_pf, short_sim),
    ):
        ctx = pf.marginal_risk_context
        print(f"[{label}]")
        print(f"PF decision={pf.decision.value}")
        print(f"PF fit score={_fmt(pf.portfolio_fit_score)}")
        print(
            "PF directional effect="
            f"{pf.exposure_context.directional_effect.value}"
        )
        print(
            "PF scoring coverage="
            f"{_fmt(pf.score_breakdown.scoring_coverage_pct)}"
        )
        print(
            "PF analytical coverage="
            f"{_fmt(pf.score_breakdown.analytical_coverage_pct)}"
        )
        print(
            "PF marginal AFTER "
            f"vol={_fmt(ctx.volatility_pct.after)} "
            f"beta={_fmt(ctx.beta.after)} "
            f"VaR95={_fmt(ctx.var_95_1d_eur.after)} "
            f"CVaR95={_fmt(ctx.cvar_95_1d_eur.after)}"
        )
        print(
            "SIM AFTER "
            f"vol={_fmt(sim.after.portfolio_volatility_pct)} "
            f"beta={_fmt(sim.after.portfolio_beta)} "
            f"VaR95={_fmt(sim.after.var_95_1d_eur)} "
            f"CVaR95={_fmt(sim.after.cvar_95_1d_eur)} "
            f"coverage={_fmt(sim.after.analytical_coverage_pct)}"
        )
        print()

    print("[A] Current snapshot/risk/account provenance: PASS")
    print("[B] Canonical PF LONG REJECT / SHORT eligible policy: PASS")
    print("[C] PF scoring + analytical coverage gate: PASS")
    print("[D] PF-1E persisted assessment provenance: PASS")
    print("[E] PF and Simulator V2 share quantitative truth: PASS")
    print("[F] LONG > SHORT risk directional relationship: PASS")
    print("[G] Final acceptance remained read-only: PASS")
    print("=== PF-1H ACCEPTANCE PASS ===")


if __name__ == "__main__":
    main()
