"""Stage 5 selected-opportunity dry run (S5.0-7, part B).

Run on the operator's PC with IB Gateway logged in, after
``python -m tools.s5_first_e2e`` stopped at operator selection:

    python -m tools.s5_dry_run [--opportunity-id OPP] [--exposure-eur 1000]

It reads the part A report (default: the newest one), the IBKR account state
version and the instrument directory of that run, then runs the 4.0 S2.2G
chain unchanged: Portfolio Filter -> lifecycle gate -> instrument selection
-> position sizing -> TradeProposal -> Portfolio Simulator -> CIO decision ->
ExecutionPlan. Market inputs come from IB Gateway (last close, IBKR FX, ATR
stop) unless given explicitly.

No Claude call, no scan. Read-only on IBKR: no order, alert or watchlist; the
ExecutionPlan is a non-authorized dry run and the side-effect counters stay 0.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from app.ai.ibkr_market import IbkrHistoryLoader
from app.broker.gateway import IbAsyncGatewaySession, RecordedGatewaySession
from app.broker.sync_store import SyncStore
from app.cio.models import Direction
from app.cio.portfolio_marginal_risk_provider import build_canonical_portfolio_filter_service
from app.cio.portfolio_simulation_factory import build_canonical_portfolio_simulation_service
from app.cio.storage import Stage3Store
from app.e2e.stage5_dry_run import (
    DEFAULT_STOP_ATR_MULTIPLE,
    IbkrMarginalRiskInputProvider,
    NlvRiskPolicy,
    default_exposure_eur,
    fx_to_eur,
    ibkr_instrument,
    market_inputs,
    resolve_contract,
)
from app.e2e.stage5_portfolio import RISK_HISTORY_DURATION, directory_from_version, positions_from_version
from app.scanner.ibkr_universe import ScannerReports
from app.scanner.research_integration_store import ScannerResearchIntegrationStore
from app.scanner.selected_opportunity_dry_run import CanonicalSelectedOpportunityExecutor
from app.scanner.selected_opportunity_dry_run_contracts import (
    DryRunMode,
    DryRunStatus,
    SelectedOpportunityDryRunRequest,
    SelectedOpportunityParameters,
)
from app.scanner.selected_opportunity_dry_run_service import SelectedOpportunityDryRunService
from app.scanner.selected_opportunity_dry_run_store import SelectedOpportunityDryRunStore
from tools.s5_first_e2e import DEFAULT_OUTPUT, merge_scanner_refs

INPUT_SOURCE = "STAGE5_IBKR_DRY_RUN"


def latest_report(directory: Path) -> Path:
    reports = sorted(directory.glob("stage5_first_e2e_*.json"), key=lambda p: p.stat().st_mtime)
    if not reports:
        raise SystemExit(f"no part A report in {directory}: run python -m tools.s5_first_e2e first")
    return reports[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path, help="part A report (default: the newest in --output-directory)")
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--opportunity-id", help="default: the first selectable opportunity of the part A run")
    parser.add_argument("--db", type=Path, default=Path("data/state/stage5_cio.db"))
    parser.add_argument("--sync-db", type=Path, default=Path("data/state/stage5_account_sync.db"))
    parser.add_argument("--gateway-recording", type=Path, help="replay a Gateway recording instead of the live Gateway")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--exposure-eur", type=float, help="default: half the PF-1 position-weight cap of the NLV")
    parser.add_argument("--max-loss-eur", type=float, help="default: the PF-1 max trade loss")
    parser.add_argument("--reference-price", type=float, help="default: last IB Gateway daily close")
    parser.add_argument("--fx-to-eur", type=float, help="default: the account's IBKR exchange rate")
    parser.add_argument("--stop-price", type=float, help="default: close -/+ ATR multiple")
    parser.add_argument("--stop-atr-multiple", type=float, default=DEFAULT_STOP_ATR_MULTIPLE)
    parser.add_argument("--funding", choices=["base", "trading"], default="base",
                        help="fund the trade from the account base-currency cash (IBKR converts; default) or, "
                             "as 4.0/Fineco did, only from cash in the trading currency")
    parser.add_argument("--entry-type", default="MARKET")
    parser.add_argument("--entry-price", type=float)
    parser.add_argument("--target-1", type=float)
    parser.add_argument("--target-2", type=float)
    args = parser.parse_args(argv)

    report_path = args.report or latest_report(args.output_directory)
    part_a = json.loads(report_path.read_text(encoding="utf-8"))
    run_a = part_a["run"]
    selectable = list(run_a.get("selectable_opportunity_ids") or ())
    opportunity_id = args.opportunity_id or (selectable[0] if selectable else None)
    print(f"part A: {report_path.name} research_run_id={run_a.get('research_run_id')} selectable={selectable}")
    if opportunity_id is None:
        print("S5_DRY_RUN: NO_SELECTABLE_OPPORTUNITY (no opportunity passed the scoring threshold in part A)")
        return 2

    stage3 = Stage3Store(args.db)
    opportunity = stage3.get_trade_opportunity(opportunity_id)
    if opportunity is None:
        raise SystemExit(f"TradeOpportunity {opportunity_id} not found in {args.db}")
    snapshot_id = part_a["configuration"]["portfolio_snapshot_id"]
    snapshot = stage3.get_portfolio_snapshot(snapshot_id)
    version = SyncStore(args.sync_db).get_version(part_a["account_state_version"])
    positions = positions_from_version(version)
    directory = directory_from_version(version)
    if part_a.get("scanner"):
        merge_scanner_refs(directory, ScannerReports.load(Path(part_a["configuration"]["mapping_report_path"]).parent)
                           .instrument_refs)

    now = datetime.now(timezone.utc)
    nlv = version.reading.metrics.net_liquidation
    policy = NlvRiskPolicy()
    constraints = policy.constraints(nlv)
    session = RecordedGatewaySession(str(args.gateway_recording)) if args.gateway_recording \
        else IbAsyncGatewaySession(port=args.port)
    try:
        loader = IbkrHistoryLoader(session, directory, duration=RISK_HISTORY_DURATION)
        info = resolve_contract(session, directory, opportunity.ticker)
        funding = version.reading.metrics.base_currency if args.funding == "base" else None
        instrument = ibkr_instrument(opportunity.ticker, info, as_of=now, funding_currency=funding)
        stage3.save_fineco_instrument(instrument)

        fx = args.fx_to_eur or fx_to_eur(version, info.currency)
        market = market_inputs(loader(opportunity.ticker), direction=Direction(opportunity.direction),
                               fx=fx, now=now, stop_atr_multiple=args.stop_atr_multiple)
        parameters = SelectedOpportunityParameters(
            requested_exposure_eur=args.exposure_eur or default_exposure_eur(nlv, policy),
            max_intended_loss_eur=args.max_loss_eur or constraints.max_trade_loss_eur,
            reference_price=args.reference_price or market.reference_price,
            fx_to_eur=fx,
            stop_price=args.stop_price or market.stop_price,
            market_observed_at=market.market_observed_at,
            instrument_id=instrument.instrument_id,
            entry_type=args.entry_type,
            entry_price=args.entry_price,
            target_1=args.target_1,
            target_2=args.target_2,
            input_source=INPUT_SOURCE,
        )
        print(f"opportunity: {opportunity_id} {opportunity.ticker} {opportunity.direction.value}")
        print(f"instrument: {instrument.instrument_id} {instrument.market} {instrument.quote_currency.value} "
              f"{instrument.instrument_type.value}, funded from {funding or info.currency} cash")
        print(f"market: close {parameters.reference_price} ({market.source}), ATR {market.atr}, "
              f"stop {parameters.stop_price}, fx_to_eur {fx}")
        print(f"risk: exposure {parameters.requested_exposure_eur} EUR, max loss {parameters.max_intended_loss_eur} EUR "
              f"(NLV {nlv:.2f} EUR)")

        provider = IbkrMarginalRiskInputProvider(positions, loader, snapshot_reference=snapshot.source_file)
        executor = CanonicalSelectedOpportunityExecutor(
            stage3_store=stage3,
            integration_store=ScannerResearchIntegrationStore(args.db),
            portfolio_filter_service=build_canonical_portfolio_filter_service(stage3, input_provider=provider),
            portfolio_simulation_service=build_canonical_portfolio_simulation_service(stage3, input_provider=provider),
        )
        dry_store = SelectedOpportunityDryRunStore(args.db)
        request = SelectedOpportunityDryRunRequest(
            scanner_research_run_id=run_a["research_run_id"],
            opportunity_ids=(opportunity_id,),
            portfolio_snapshot_id=snapshot_id,
            as_of=now,
            mode=DryRunMode.LIVE,
            parameters=parameters,
        )
        run = SelectedOpportunityDryRunService(store=dry_store, executor=executor).run(request, now=now)
        stages = dry_store.list_stages(run.run_id)
    finally:
        if hasattr(session, "close"):
            session.close()

    report = {
        "audit_id": "E2E-S5.0-7B",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "part_a_report": str(report_path),
        "account_state_version": version.version_id,
        "state_origin": version.origin.value,
        "pf1_constraints": constraints.model_dump(mode="json"),
        "instrument": instrument.model_dump(mode="json"),
        "market_inputs_source": market.source,
        "marginal_risk_history_failures": provider.failures,
        "request": request.model_dump(mode="json"),
        "run": run.model_dump(mode="json"),
        "stages": [value.model_dump(mode="json") for value in stages],
        "side_effect_guarantee": {
            "broker_orders_submitted": run.broker_orders_submitted,
            "portfolio_mutations": run.portfolio_mutations,
            "automatic_executions": run.automatic_executions,
        },
    }
    path = args.output_directory / "dry_run" / f"stage5_dry_run_{run.run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("run_id:", run.run_id)
    print("run_status:", run.status.value)
    print("terminal_reason:", run.terminal_reason.value if run.terminal_reason else None)
    print("completed_stages:", [value.value for value in run.completed_stages])
    for stage in stages:
        if stage.status.value != "COMPLETED":
            print(f"  {stage.stage.value}: {stage.status.value} {stage.message} {list(stage.diagnostics)}")
    fit = stage3.get_latest_portfolio_fit_assessment(opportunity_id)
    if fit is not None:
        print(f"portfolio_fit: {fit.decision.value} score={fit.portfolio_fit_score}")
        for check in fit.constraint_checks:
            if check.status.value in {"FAIL", "UNKNOWN"}:
                print(f"  {check.code} {check.status.value}: {check.projected_value} vs limit {check.limit_value}"
                      f" ({check.reason})")
    print("execution_plan_id:", run.execution_plan_id)
    print("broker_orders_submitted:", run.broker_orders_submitted)
    print("portfolio_mutations:", run.portfolio_mutations)
    print("automatic_executions:", run.automatic_executions)
    print("report:", path)
    if run.status is DryRunStatus.COMPLETED:
        print("S5_DRY_RUN: PASS")
        return 0
    print(f"S5_DRY_RUN: {run.status.value}")
    return 2 if run.status is DryRunStatus.BLOCKED else 1


if __name__ == "__main__":
    raise SystemExit(main())
