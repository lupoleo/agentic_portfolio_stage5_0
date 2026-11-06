"""Run the real E2E-S4.0A composition without execution side effects."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from app.e2e.stage4_contracts import (
    Stage4E2ERequest,
    Stage4Mode,
    Stage4RunStatus,
)
from app.e2e.stage4_runtime import (
    CanonicalStage4Runtime,
    Stage4RuntimeConfiguration,
    file_fingerprint,
)
from app.e2e.stage4_service import Stage4E2EService
from app.e2e.stage4_store import Stage4E2EStore


DEFAULT_DB = Path("data/state/portfolio_cio.db")
DEFAULT_OUTPUT = Path("data/cache/e2e/stage4/first_complete_e2e")


def _aware(value: str) -> datetime:
    normalized = value.strip()
    if normalized[-1:] in {"Z", "z"}:
        normalized = normalized[:-1] + "+00:00"
    normalized = re.sub(
        r"(\.\d{6})\d+(?=[+-]\d\d:\d\d$)", r"\1", normalized,
    )
    result = datetime.fromisoformat(normalized)
    if result.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return result


def _parameters(args):
    values = (
        args.exposure_eur, args.max_loss_eur, args.reference_price,
        args.fx_to_eur, args.stop_price, args.market_observed_at,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise SystemExit(
            "Exposure, max loss, reference price, FX, stop and "
            "market-observed-at must be supplied together"
        )
    return {
        "requested_exposure_eur": args.exposure_eur,
        "max_intended_loss_eur": args.max_loss_eur,
        "reference_price": args.reference_price,
        "fx_to_eur": args.fx_to_eur,
        "stop_price": args.stop_price,
        "market_observed_at": args.market_observed_at,
        "instrument_id": args.instrument_id,
        "entry_type": args.entry_type,
        "entry_price": args.entry_price,
        "target_1": args.target_1,
        "target_2": args.target_2,
    }


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portfolio-file", type=Path, required=True)
    parser.add_argument("--account-state-id", required=True)
    parser.add_argument("--portfolio-snapshot-id", required=True)
    parser.add_argument("--portfolio-risk-state-id", required=True)
    parser.add_argument("--eligibility-report", type=Path, required=True)
    parser.add_argument("--mapping-report", type=Path, required=True)
    parser.add_argument("--history-report", type=Path, action="append", required=True)
    parser.add_argument("--as-of", type=_aware, required=True)
    parser.add_argument(
        "--mode", choices=[value.value for value in Stage4Mode],
        default=Stage4Mode.PREFER_CACHE.value,
    )
    parser.add_argument("--opportunity-id")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-hypotheses", type=int)
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

    configuration = Stage4RuntimeConfiguration(
        database_path=str(args.db),
        portfolio_snapshot_id=args.portfolio_snapshot_id,
        portfolio_risk_state_id=args.portfolio_risk_state_id,
        eligibility_report_path=str(args.eligibility_report),
        mapping_report_path=str(args.mapping_report),
        history_report_paths=tuple(str(value) for value in args.history_report),
        watch_output_directory=str(args.output_directory / "watch_universe"),
        model_name=args.model,
        timeout_seconds=args.timeout_seconds,
        max_hypotheses=args.max_hypotheses,
        selected_parameters=_parameters(args),
    )
    request = Stage4E2ERequest(
        portfolio_file=str(args.portfolio_file),
        portfolio_file_fingerprint=file_fingerprint(args.portfolio_file),
        account_state_id=args.account_state_id,
        scanner_configuration_id="stage4-first-complete-e2e-v1",
        scanner_configuration_fingerprint=configuration.fingerprint,
        as_of=args.as_of,
        mode=Stage4Mode(args.mode),
        selected_opportunity_ids=(args.opportunity_id,) if args.opportunity_id else (),
    )
    runtime = CanonicalStage4Runtime(configuration)
    store = Stage4E2EStore(args.db)
    service = Stage4E2EService(store=store, adapters=runtime.adapters())
    run = service.run(request, now=datetime.now(timezone.utc))
    stages = store.list_stages(run.run_id)
    attempts = store.list_attempts(run.run_id)
    report = {
        "audit_id": "E2E-S4.0A",
        "schema": "stage4-first-complete-e2e-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": configuration.model_dump(mode="json"),
        "configuration_fingerprint": configuration.fingerprint,
        "request": request.model_dump(mode="json"),
        "run": run.model_dump(mode="json"),
        "stages": [
            value.model_dump(mode="json")
            for value in stages
        ],
        "stage_attempts": [
            value.model_dump(mode="json")
            for value in attempts
        ],
        "side_effect_guarantee": {
            "broker_orders_submitted": run.broker_orders_submitted,
            "portfolio_mutations": run.portfolio_mutations,
            "automatic_executions": run.automatic_executions,
        },
    }
    path = args.output_directory / f"stage4_first_complete_e2e_{run.run_id}.json"
    _atomic_json(path, report)

    print("checkpoint: E2E-S4.0A")
    print("mode:", run.mode.value)
    print("run_id:", run.run_id)
    print("run_status:", run.status.value)
    print("terminal_reason:", run.terminal_reason.value if run.terminal_reason else None)
    print("portfolio_snapshot_id:", run.portfolio_snapshot_id)
    print("portfolio_risk_state_id:", run.portfolio_risk_state_id)
    print("scanner_run_id:", run.scanner_run_id)
    print("watch_universe_run_id:", run.watch_universe_run_id)
    print("research_run_id:", run.research_run_id)
    print("selectable_opportunities:", list(run.selectable_opportunity_ids))
    print("selected_opportunity_id:", run.selected_opportunity_id)
    print("execution_plan_id:", run.execution_plan_id)
    print(
        "completed_stages:",
        [value.value for value in run.completed_stages],
    )
    print("partial_attempt_count:", len(attempts))
    if attempts:
        latest_attempt = attempts[-1]
        print(
            "latest_partial_stage:",
            latest_attempt.stage.value,
        )
        print(
            "latest_partial_reason:",
            (
                latest_attempt.reason.value
                if latest_attempt.reason
                else None
            ),
        )
        print(
            "latest_partial_output_ids:",
            list(latest_attempt.output_ids),
        )
        print(
            "latest_partial_diagnostics:",
            list(latest_attempt.diagnostics),
        )
    print("broker_orders_submitted:", run.broker_orders_submitted)
    print("portfolio_mutations:", run.portfolio_mutations)
    print("automatic_executions:", run.automatic_executions)
    print("report:", path)
    if run.status is Stage4RunStatus.COMPLETED:
        print("E2E-S4.0A: PASS")
        return 0
    if run.status in {
        Stage4RunStatus.WAITING_FOR_OPERATOR_SELECTION,
        Stage4RunStatus.BLOCKED,
        Stage4RunStatus.PARTIAL,
    }:
        print("E2E-S4.0A: VALID_NON_COMPLETE")
        return 2
    print("E2E-S4.0A: FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
