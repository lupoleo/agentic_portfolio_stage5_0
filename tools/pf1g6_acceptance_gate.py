from __future__ import annotations

# PF-1G.6A: allow direct execution from tools/ on Windows/POSIX.
# `python tools/pf1g6_acceptance_gate.py` puts tools/ rather than the
# repository root on sys.path.
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

from app.cio.models import (
    AccountState,
    Currency,
    CurrencyCash,
    DataSource,
    Direction,
    ExecutionSide,
    OpportunityStatus,
    PortfolioRiskState,
    PortfolioRiskStateRecord,
    PortfolioSnapshot,
    RiskConstraints,
    TradeOpportunity,
    TradeProposal,
    TradingHorizon,
)
from app.cio.portfolio_simulation_service import PortfolioSimulationService
from app.cio.portfolio_simulator import PortfolioRiskSimulator
from app.cio.storage import Stage3Store


NOW = datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc)


def metric(before, after):
    return SimpleNamespace(
        before=before,
        after=after,
        delta=after-before,
    )


class Adapter:
    def assess(self, *, snapshot, proposal):
        return SimpleNamespace(
            volatility_pct=metric(15.0, 14.2),
            beta=metric(0.80, 0.70),
            var_95_1d_eur=metric(4000.0, 3700.0),
            cvar_95_1d_eur=metric(6000.0, 5500.0),
            analytical_coverage_before_pct=90.0,
            analytical_coverage_after_pct=92.0,
            top5_concentration_before_pct=50.0,
            top5_concentration_after_pct=47.0,
            effective_positions_before=8.0,
            effective_positions_after=8.8,
        )


def install(store):
    store.save_account_state(AccountState(
        account_state_id="ACC-PF1G6",
        timestamp=NOW,
        account_equity_eur=250000.0,
        cash=[CurrencyCash(
            currency=Currency.EUR,
            available=50000.0,
            reserve=1000.0,
        )],
        constraints=RiskConstraints(
            max_portfolio_beta=0.75,
            max_var_95_1d_eur=5000.0,
        ),
        source=DataSource.OPERATOR,
    ))

    store.save_portfolio_snapshot(PortfolioSnapshot(
        snapshot_id="SNAP-PF1G6",
        timestamp=NOW,
        source_file="synthetic-portfolio.xlsx",
        source_file_hash="pf1g6-synthetic",
        quant_engine_version="2.5",
        analyzed_positions=8,
        gross_exposure_eur=100000.0,
        net_exposure_eur=80000.0,
        account_state_id="ACC-PF1G6",
    ))

    store.save_portfolio_risk_state(PortfolioRiskStateRecord(
        risk_state_id="RISK-PF1G6",
        snapshot_id="SNAP-PF1G6",
        created_at=NOW,
        state=PortfolioRiskState(
            gross_exposure_eur=100000.0,
            net_exposure_eur=80000.0,
            long_exposure_eur=90000.0,
            short_exposure_eur=10000.0,
            portfolio_volatility_pct=15.0,
            portfolio_beta=0.80,
            var_95_1d_eur=4000.0,
            cvar_95_1d_eur=6000.0,
            top5_concentration_pct=50.0,
            effective_positions=8.0,
            analytical_coverage_pct=90.0,
        ),
    ))

    store.save_trade_opportunity(TradeOpportunity(
        opportunity_id="OPP-PF1G6",
        snapshot_id="SNAP-PF1G6",
        created_at=NOW,
        ticker="QCOM",
        direction=Direction.LONG,
        horizon=TradingHorizon.SWING,
        confidence=0.80,
        target_exposure_eur=10000.0,
        max_intended_loss_eur=1000.0,
        thesis="PF-1G.6 synthetic E2E.",
        status=OpportunityStatus.READY_FOR_PROPOSAL,
    ))

    store.save_trade_proposal(TradeProposal(
        proposal_id="PROP-PF1G6",
        opportunity_id="OPP-PF1G6",
        snapshot_id="SNAP-PF1G6",
        created_at=NOW,
        ticker="QCOM",
        direction=Direction.LONG,
        instrument_id="FIN-QCOM-PF1G6",
        sizing_id="SIZE-PF1G6",
        execution_side=ExecutionSide.BUY,
        quantity=100.0,
        reference_price=100.0,
        currency=Currency.EUR,
        fx_to_eur=1.0,
        entry_type="MARKET",
        gross_exposure_eur=10000.0,
        estimated_capital_required_eur=10000.0,
        estimated_max_loss_eur=1000.0,
    ))


def main():
    with tempfile.TemporaryDirectory(prefix="pf1g6_") as tmp:
        store = Stage3Store(Path(tmp) / "pf1g6.sqlite")
        install(store)

        v2 = PortfolioSimulationService(
            store,
            simulator=PortfolioRiskSimulator(
                marginal_risk_adapter=Adapter()
            ),
        ).create_simulation("OPP-PF1G6")

        assert v2.after.portfolio_beta == 0.70
        assert v2.after.top5_concentration_pct == 47.0
        assert v2.constraints_passed is True
        assert store.get_latest_portfolio_simulation("PROP-PF1G6") == v2

        print("[A] Synthetic V2 service -> simulator -> persistence: PASS")

    with tempfile.TemporaryDirectory(prefix="pf1g6_v1_") as tmp:
        store = Stage3Store(Path(tmp) / "pf1g6-v1.sqlite")
        install(store)

        v1 = PortfolioSimulationService(
            store
        ).create_simulation("OPP-PF1G6")

        warning_text = " ".join(v1.warnings).lower()

        assert v1.after.portfolio_beta == 0.80
        assert "beta after is not yet" in warning_text
        assert v1.constraints_passed is True

        print("[B] Direct-service V1 compatibility boundary: PASS")

    print("=== PF-1G.6 ACCEPTANCE PASS ===")


if __name__ == "__main__":
    main()
