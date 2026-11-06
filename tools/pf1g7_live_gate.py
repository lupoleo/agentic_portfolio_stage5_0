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
    TradeProposal,
)
from app.cio.portfolio_simulation_factory import (
    build_canonical_portfolio_simulation_service,
)
from app.cio.storage import Stage3Store


DEFAULT_DB = Path("data/state/portfolio_cio.db")
DEFAULT_TICKER = "QCOM"
DEFAULT_EXPOSURE_EUR = 10_000.0


def _proposal(
    *,
    snapshot_id: str,
    ticker: str,
    direction: Direction,
    exposure_eur: float,
) -> TradeProposal:
    side = (
        ExecutionSide.BUY
        if direction == Direction.LONG
        else ExecutionSide.SELL_SHORT
    )

    return TradeProposal(
        proposal_id=f"PF1G7-{ticker}-{direction.value}-{int(exposure_eur)}",
        opportunity_id=f"PF1G7-LIVE-{ticker}-{direction.value}",
        snapshot_id=snapshot_id,
        created_at=datetime.now(timezone.utc),
        ticker=ticker,
        direction=direction,
        instrument_id=f"PF1G7-LIVE-{ticker}",
        sizing_id=f"PF1G7-SIZE-{ticker}-{direction.value}",
        execution_side=side,
        quantity=1.0,
        reference_price=exposure_eur,
        currency=Currency.EUR,
        fx_to_eur=1.0,
        entry_type="MARKET",
        gross_exposure_eur=exposure_eur,
        estimated_capital_required_eur=exposure_eur,
        estimated_max_loss_eur=None,
    )


def _fmt(value):
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "PF-1G.7 live QCOM LONG/SHORT validation against the "
            "canonical Portfolio Simulator V2. This gate is read-only: "
            "it does not persist synthetic proposals or simulations."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
    )
    parser.add_argument(
        "--ticker",
        default=DEFAULT_TICKER,
    )
    parser.add_argument(
        "--exposure-eur",
        type=float,
        default=DEFAULT_EXPOSURE_EUR,
    )
    args = parser.parse_args()

    if args.exposure_eur <= 0:
        raise SystemExit("--exposure-eur must be > 0")

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
            "No PortfolioRiskState exists for latest snapshot "
            f"{snapshot.snapshot_id}."
        )

    before = risk_record.state

    account = store.get_latest_account_state()
    if account is None and snapshot.account_state_id is not None:
        account = store.get_account_state(snapshot.account_state_id)
    if account is None:
        raise RuntimeError(
            "No AccountState available for live simulation."
        )

    service = build_canonical_portfolio_simulation_service(store)
    simulator = service.simulator

    ticker = args.ticker.upper()
    exposure = args.exposure_eur

    long_proposal = _proposal(
        snapshot_id=snapshot.snapshot_id,
        ticker=ticker,
        direction=Direction.LONG,
        exposure_eur=exposure,
    )
    short_proposal = _proposal(
        snapshot_id=snapshot.snapshot_id,
        ticker=ticker,
        direction=Direction.SHORT,
        exposure_eur=exposure,
    )

    long_sim = simulator.simulate(
        snapshot=snapshot,
        proposal=long_proposal,
        before=before,
        account_state=account,
    )
    short_sim = simulator.simulate(
        snapshot=snapshot,
        proposal=short_proposal,
        before=before,
        account_state=account,
    )

    # ------------------------------------------------------------
    # Structural / provenance acceptance
    # ------------------------------------------------------------
    assert simulator.marginal_risk_adapter is not None

    assert long_sim.snapshot_id == snapshot.snapshot_id
    assert short_sim.snapshot_id == snapshot.snapshot_id

    assert long_sim.proposal_id == long_proposal.proposal_id
    assert short_sim.proposal_id == short_proposal.proposal_id

    # Exact proposal exposure mechanics.
    assert abs(
        long_sim.after.gross_exposure_eur
        - (before.gross_exposure_eur + exposure)
    ) <= 0.01
    assert abs(
        short_sim.after.gross_exposure_eur
        - (before.gross_exposure_eur + exposure)
    ) <= 0.01

    assert abs(
        long_sim.after.net_exposure_eur
        - (before.net_exposure_eur + exposure)
    ) <= 0.01
    assert abs(
        short_sim.after.net_exposure_eur
        - (before.net_exposure_eur - exposure)
    ) <= 0.01

    # ------------------------------------------------------------
    # Canonical quantitative acceptance
    # ------------------------------------------------------------
    required_long = [
        long_sim.after.portfolio_volatility_pct,
        long_sim.after.portfolio_beta,
        long_sim.after.var_95_1d_eur,
        long_sim.after.cvar_95_1d_eur,
        long_sim.after.top5_concentration_pct,
        long_sim.after.effective_positions,
        long_sim.after.analytical_coverage_pct,
    ]
    required_short = [
        short_sim.after.portfolio_volatility_pct,
        short_sim.after.portfolio_beta,
        short_sim.after.var_95_1d_eur,
        short_sim.after.cvar_95_1d_eur,
        short_sim.after.top5_concentration_pct,
        short_sim.after.effective_positions,
        short_sim.after.analytical_coverage_pct,
    ]

    assert all(value is not None for value in required_long)
    assert all(value is not None for value in required_short)

    # The live QCOM result should preserve the qualitative relationship
    # already established by PF-1C/PF-1D on the same portfolio family:
    # LONG increases directional/risk load relative to SHORT; SHORT is the
    # diversifying direction.
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

    # Analytical coverage must remain usable rather than silently falling
    # back to a placeholder.
    assert long_sim.after.analytical_coverage_pct >= 70.0
    assert short_sim.after.analytical_coverage_pct >= 70.0

    # Concentration metrics must be actual PF-1G.4 V2 outputs, not missing.
    assert 0.0 <= long_sim.after.top5_concentration_pct <= 100.0
    assert 0.0 <= short_sim.after.top5_concentration_pct <= 100.0
    assert long_sim.after.effective_positions > 0.0
    assert short_sim.after.effective_positions > 0.0

    # ------------------------------------------------------------
    # Read-only guarantee
    # ------------------------------------------------------------
    assert store.get_trade_proposal(long_proposal.proposal_id) is None
    assert store.get_trade_proposal(short_proposal.proposal_id) is None

    print("=== PF-1G.7 LIVE INPUT ===")
    print(f"DB: {args.db}")
    print(f"Snapshot: {snapshot.snapshot_id}")
    print(f"Risk state: {risk_record.risk_state_id}")
    print(f"Account state: {account.account_state_id}")
    print(f"Ticker: {ticker}")
    print(f"Exact proposal exposure: EUR {exposure:.2f}")
    print()

    print("[BEFORE]")
    print(f"gross={_fmt(before.gross_exposure_eur)}")
    print(f"net={_fmt(before.net_exposure_eur)}")
    print(f"vol={_fmt(before.portfolio_volatility_pct)}")
    print(f"beta={_fmt(before.portfolio_beta)}")
    print(f"VaR95={_fmt(before.var_95_1d_eur)}")
    print(f"CVaR95={_fmt(before.cvar_95_1d_eur)}")
    print(f"top5={_fmt(before.top5_concentration_pct)}")
    print(f"effective_positions={_fmt(before.effective_positions)}")
    print(f"coverage={_fmt(before.analytical_coverage_pct)}")
    print()

    for label, sim in (
        ("LONG", long_sim),
        ("SHORT", short_sim),
    ):
        print(f"[QCOM {label}]")
        print(f"gross={_fmt(sim.after.gross_exposure_eur)}")
        print(f"net={_fmt(sim.after.net_exposure_eur)}")
        print(f"vol={_fmt(sim.after.portfolio_volatility_pct)}")
        print(f"beta={_fmt(sim.after.portfolio_beta)}")
        print(f"VaR95={_fmt(sim.after.var_95_1d_eur)}")
        print(f"CVaR95={_fmt(sim.after.cvar_95_1d_eur)}")
        print(f"top5={_fmt(sim.after.top5_concentration_pct)}")
        print(f"effective_positions={_fmt(sim.after.effective_positions)}")
        print(f"coverage={_fmt(sim.after.analytical_coverage_pct)}")
        print(f"constraints_passed={sim.constraints_passed}")
        if sim.violated_constraints:
            print("violations:")
            for item in sim.violated_constraints:
                print(f"  - {item}")
        if sim.warnings:
            print("warnings:")
            for item in sim.warnings:
                print(f"  - {item}")
        print()

    print("[A] Canonical V2 adapter configured: PASS")
    print("[B] Exact LONG/SHORT proposal exposure mechanics: PASS")
    print("[C] Live shared-engine quantitative metrics present: PASS")
    print("[D] QCOM LONG risk metrics > QCOM SHORT risk metrics: PASS")
    print("[E] Analytical coverage >= 70% for both directions: PASS")
    print("[F] PF-1G.4 concentration metrics present: PASS")
    print("[G] Live gate remained read-only: PASS")
    print("=== PF-1G.7 LIVE GATE PASS ===")


if __name__ == "__main__":
    main()
