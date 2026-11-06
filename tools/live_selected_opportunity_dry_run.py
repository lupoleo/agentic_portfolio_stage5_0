"""Run the E2E-S2.2G selected-opportunity lifecycle without execution."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from app.cio.portfolio_marginal_risk_provider import (
    build_canonical_portfolio_filter_service,
)
from app.cio.portfolio_simulation_factory import (
    build_canonical_portfolio_simulation_service,
)
from app.cio.storage import Stage3Store
from app.scanner.research_integration_store import ScannerResearchIntegrationStore
from app.scanner.selected_opportunity_dry_run import (
    CanonicalSelectedOpportunityExecutor,
)
from app.scanner.selected_opportunity_dry_run_contracts import (
    DryRunMode,
    DryRunStatus,
    SelectedOpportunityDryRunRequest,
    SelectedOpportunityParameters,
)
from app.scanner.selected_opportunity_dry_run_service import (
    SelectedOpportunityDryRunService,
)
from app.scanner.selected_opportunity_dry_run_store import (
    SelectedOpportunityDryRunStore,
)


DEFAULT_DB = Path("data/state/portfolio_cio.db")
DEFAULT_OUTPUT = Path("data/cache/scanner/selected_opportunity_dry_run")


def _aware(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    value = re.sub(r"(\.\d{6})\d+(?=[+-]\d\d:\d\d$)", r"\1", value)
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return result


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parameters(args) -> SelectedOpportunityParameters | None:
    values = (
        args.exposure_eur,
        args.max_loss_eur,
        args.reference_price,
        args.fx_to_eur,
        args.stop_price,
        args.market_observed_at,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise SystemExit(
            "Exposure, max loss, reference price, FX, stop and market-observed-at "
            "must be supplied together"
        )
    return SelectedOpportunityParameters(
        requested_exposure_eur=args.exposure_eur,
        max_intended_loss_eur=args.max_loss_eur,
        reference_price=args.reference_price,
        fx_to_eur=args.fx_to_eur,
        stop_price=args.stop_price,
        market_observed_at=args.market_observed_at,
        instrument_id=args.instrument_id,
        entry_type=args.entry_type,
        entry_price=args.entry_price,
        target_1=args.target_1,
        target_2=args.target_2,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--scanner-research-run-id", required=True)
    parser.add_argument("--opportunity-id")
    parser.add_argument("--portfolio-snapshot-id", required=True)
    parser.add_argument("--as-of", type=_aware, default=datetime.now(timezone.utc))
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--exposure-eur", type=float)
    parser.add_argument("--max-loss-eur", type=float)
    parser.add_argument("--reference-price", type=float)
    parser.add_argument("--fx-to-eur", type=float)
    parser.add_argument("--stop-price", type=float)
    parser.add_argument("--market-observed-at", type=_aware)
    parser.add_argument("--instrument-id")
    parser.add_argument("--entry-type", default="MARKET")
    parser.add_argument("--entry-price", type=float)
    parser.add_argument("--target-1", type=float)
    parser.add_argument("--target-2", type=float)
    args = parser.parse_args()

    stage3_store = Stage3Store(args.db)
    integration_store = ScannerResearchIntegrationStore(args.db)
    orchestration_store = SelectedOpportunityDryRunStore(args.db)
    request = SelectedOpportunityDryRunRequest(
        scanner_research_run_id=args.scanner_research_run_id,
        opportunity_ids=(args.opportunity_id,) if args.opportunity_id else (),
        portfolio_snapshot_id=args.portfolio_snapshot_id,
        as_of=args.as_of,
        mode=DryRunMode.CACHE_ONLY if args.cache_only else DryRunMode.LIVE,
        parameters=_parameters(args),
    )
    executor = CanonicalSelectedOpportunityExecutor(
        stage3_store=stage3_store,
        integration_store=integration_store,
        portfolio_filter_service=build_canonical_portfolio_filter_service(stage3_store),
        portfolio_simulation_service=build_canonical_portfolio_simulation_service(
            stage3_store
        ),
    )
    service = SelectedOpportunityDryRunService(
        store=orchestration_store,
        executor=executor,
    )
    run = service.run(request)
    stages = orchestration_store.list_stages(run.run_id)
    report = {
        "audit_id": "E2E-S2.2G",
        "schema": "selected-opportunity-dry-run-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "request": request.model_dump(mode="json"),
        "run": run.model_dump(mode="json"),
        "stages": [value.model_dump(mode="json") for value in stages],
        "side_effect_guarantee": {
            "broker_orders_submitted": 0,
            "portfolio_mutations": 0,
            "automatic_executions": 0,
        },
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = args.output_directory / f"selected_opportunity_dry_run_{stamp}.json"
    _atomic_json(path, report)

    print("checkpoint: E2E-S2.2G")
    print("mode:", request.mode.value)
    print("run_id:", run.run_id)
    print("run_status:", run.status.value)
    print("terminal_reason:", run.terminal_reason.value if run.terminal_reason else None)
    print("selected_opportunity:", run.opportunity_id)
    print("completed_stages:", [value.value for value in run.completed_stages])
    print("execution_plan_id:", run.execution_plan_id)
    print("broker_orders_submitted:", run.broker_orders_submitted)
    print("portfolio_mutations:", run.portfolio_mutations)
    print("automatic_executions:", run.automatic_executions)
    print("report:", path)
    if run.status is DryRunStatus.COMPLETED:
        print("S2.2G_DRY_RUN: PASS")
        return 0
    if run.status is DryRunStatus.BLOCKED:
        print("S2.2G_DRY_RUN: BLOCKED")
        return 2
    print("S2.2G_DRY_RUN: FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
