from __future__ import annotations

import argparse
from dataclasses import dataclass

from app.cio.models import PortfolioCheckStatus
from app.cio.portfolio_filter_service import PortfolioFilterService
from app.cio.portfolio_fit_scoring import PortfolioFitScoringEngine
from app.cio.portfolio_marginal_risk_provider import (
    CanonicalPortfolioMarginalRiskInputProvider,
)
from app.analysis.marginal_risk import PortfolioMarginalRiskEngine
from app.cio.storage import Stage3Store


DEFAULT_SNAPSHOT_ID = "SNAP-20260910-194305-499c14"


@dataclass(frozen=True)
class LiveCase:
    ticker: str
    direction: str
    exposure_eur: float


DEFAULT_CASES = (
    LiveCase("QCOM", "LONG", 10000.0),
    LiveCase("QCOM", "SHORT", 10000.0),
)


def _direction(value: str):
    from app.cio.models import Direction
    return Direction(value.upper())


def _build_ephemeral_opportunity(store, snapshot_id, case, now):
    from app.cio.models import TradeOpportunity, TradingHorizon
    opp_id = f"PF1D4-{case.ticker}-{case.direction}-{int(case.exposure_eur)}"
    existing = store.get_trade_opportunity(opp_id)
    if existing is not None:
        return existing
    opp = TradeOpportunity(
        opportunity_id=opp_id,
        snapshot_id=snapshot_id,
        created_at=now,
        ticker=case.ticker,
        direction=_direction(case.direction),
        horizon=TradingHorizon.SWING,
        confidence=0.5,
        target_exposure_eur=case.exposure_eur,
        max_intended_loss_eur=None,
        thesis="PF-1D.4 controlled live validation only; not a trade recommendation.",
        notes="PF-1D.4 validation artifact.",
    )
    store.save_trade_opportunity(opp)
    return opp


def _fmt(value, digits=4):
    return "UNKNOWN" if value is None else f"{value:.{digits}f}"


def run_case(service, store, snapshot_id, case, now):
    opp = _build_ephemeral_opportunity(store, snapshot_id, case, now)
    result = service.assess(opp.opportunity_id, as_of=now)

    print()
    print("=" * 78)
    print(f"{case.ticker} {case.direction}  target EUR {case.exposure_eur:,.2f}")
    print(f"Assessment: {result.assessment_id}")
    print(f"Decision:   {result.decision.value}")
    print(f"Fit score:  {_fmt(result.portfolio_fit_score, 2)}")

    if result.score_breakdown is None:
        raise AssertionError("score_breakdown is missing")
    print(f"Scoring coverage:    {result.score_breakdown.scoring_coverage_pct:.2f}%")
    print(
        "Analytical coverage: "
        + _fmt(result.score_breakdown.analytical_coverage_pct, 2)
        + "%"
    )

    print("Components:")
    for item in result.score_breakdown.components:
        print(
            f"  {item.name:14s} "
            f"weight={item.weight:5.2f} score={_fmt(item.score, 2):>7s}  "
            f"{item.reason}"
        )

    if result.exposure_context is None:
        raise AssertionError("exposure_context is missing")
    ec = result.exposure_context
    print(
        "Exposure: "
        f"net {_fmt(ec.net_exposure_before_eur,2)} -> {_fmt(ec.net_exposure_after_eur,2)}, "
        f"gross {_fmt(ec.gross_exposure_before_eur,2)} -> {_fmt(ec.gross_exposure_after_eur,2)}, "
        f"effect={ec.directional_effect.value}"
    )

    if result.marginal_risk_context is None:
        raise AssertionError("marginal_risk_context is missing")
    mr = result.marginal_risk_context
    print(
        "Risk: "
        f"vol {_fmt(mr.volatility_pct.before)} -> {_fmt(mr.volatility_pct.after)}; "
        f"beta {_fmt(mr.beta.before)} -> {_fmt(mr.beta.after)}; "
        f"VaR {_fmt(mr.var_95_1d_eur.before,2)} -> {_fmt(mr.var_95_1d_eur.after,2)}; "
        f"CVaR {_fmt(mr.cvar_95_1d_eur.before,2)} -> {_fmt(mr.cvar_95_1d_eur.after,2)}"
    )
    print(
        f"Correlation={_fmt(mr.candidate_correlation_to_portfolio)}; "
        f"risk contribution={_fmt(mr.candidate_risk_contribution_pct,2)}%"
    )

    hard_fails = [
        x.code for x in result.constraint_checks
        if x.status == PortfolioCheckStatus.FAIL
    ]
    unknowns = [
        x.code for x in result.constraint_checks
        if x.status == PortfolioCheckStatus.UNKNOWN
    ]
    print("Hard FAIL:", ", ".join(hard_fails) if hard_fails else "none")
    print("UNKNOWN:  ", ", ".join(unknowns) if unknowns else "none")
    print("Rationale:", result.rationale)

    assert result.portfolio_fit_score is not None
    assert result.score_breakdown.scoring_coverage_pct >= 70.0
    assert len(result.score_breakdown.components) == 6
    assert all(x.score is not None for x in result.score_breakdown.components)
    assert 0.0 <= result.portfolio_fit_score <= 100.0
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/state/portfolio_cio.db")
    parser.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--direction", choices=["LONG", "SHORT"], default=None)
    parser.add_argument("--exposure", type=float, default=10000.0)
    args = parser.parse_args()

    store = Stage3Store(args.db)
    snapshot = store.get_portfolio_snapshot(args.snapshot_id)
    if snapshot is None:
        raise SystemExit(f"Snapshot not found: {args.snapshot_id}")

    risk_record = store.get_latest_portfolio_risk_state(args.snapshot_id)
    if risk_record is None:
        raise SystemExit(f"Risk state not found for snapshot: {args.snapshot_id}")

    account = store.get_latest_account_state()
    if account is None:
        raise SystemExit("No current AccountState found")

    # Same canonical quantitative configuration validated in PF-1C.4.
    provider = CanonicalPortfolioMarginalRiskInputProvider()
    service = PortfolioFilterService(
        store,
        marginal_risk_engine=PortfolioMarginalRiskEngine(
            benchmark_symbol="SPY",
            min_observations=504,
            max_observations=756,
        ),
        marginal_risk_input_provider=provider,
        scoring_engine=PortfolioFitScoringEngine(),
    )

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    print("PF-1D.4 LIVE VALIDATION")
    print(f"Snapshot:       {snapshot.snapshot_id}")
    print(f"Risk state:     {risk_record.risk_state_id}")
    print(f"Account state:  {account.account_state_id}")
    print(f"Gross exposure: EUR {risk_record.state.gross_exposure_eur:,.2f}")
    print(f"Net exposure:   EUR {risk_record.state.net_exposure_eur:,.2f}")
    print(f"Portfolio vol:  {risk_record.state.portfolio_volatility_pct:.4f}%")
    print(f"Portfolio beta: {risk_record.state.portfolio_beta:.4f}")
    print(f"VaR95 1D:       EUR {risk_record.state.var_95_1d_eur:,.2f}")
    print(f"CVaR95 1D:      EUR {risk_record.state.cvar_95_1d_eur:,.2f}")

    cases = (
        (LiveCase(args.ticker.upper(), args.direction, args.exposure),)
        if args.ticker and args.direction
        else DEFAULT_CASES
    )

    results = [run_case(service, store, args.snapshot_id, case, now) for case in cases]

    if len(results) == 2:
        long = next((x for x in results if x.direction.value == "LONG"), None)
        short = next((x for x in results if x.direction.value == "SHORT"), None)
        if long and short:
            lc = {x.name: x.score for x in long.score_breakdown.components}
            sc = {x.name: x.score for x in short.score_breakdown.components}
            print()
            print("LONG / SHORT CONTROL")
            print(f"  LONG fit:  {long.portfolio_fit_score:.2f}")
            print(f"  SHORT fit: {short.portfolio_fit_score:.2f}")
            print(f"  directional: LONG={lc['directional']:.2f} SHORT={sc['directional']:.2f}")
            print(f"  correlation: LONG={lc['correlation']:.2f} SHORT={sc['correlation']:.2f}")
            # Current snapshot is materially net-long. For the same candidate
            # exposure, the SHORT must receive the diversification advantage
            # in the structural directional component.
            assert sc["directional"] > lc["directional"]

    print()
    print("PF-1D.4 LIVE GATE: PASS")


if __name__ == "__main__":
    main()
