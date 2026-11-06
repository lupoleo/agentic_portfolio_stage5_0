"""First Stage 5 end-to-end run from the IBKR account (S5.0-7, part A).

Run on the operator's PC with IB Gateway logged in (Read-Only API, port 4001)
and ANTHROPIC_API_KEY set:

    python -m tools.s5_first_e2e [--gateway-recording data/recordings/gateway_fixtures_<ts>.json]

Steps, all read-only:

1. account state: the latest reading of the account recording (real IBKR cash
   and metrics) with the TEMPORARY simulated holdings, valued at the latest
   IB Gateway close, versioned in the Stage 5 sync store;
2. portfolio analysis on IB Gateway history (4.0 quant engines unchanged):
   AccountState, PortfolioSnapshot and PortfolioRiskState bound to the
   ``ibkr://`` state version;
3. the 4.0 E2E service with IBKR evidence channels and Claude: holdings-only
   watch universe (the IBKR scanner is S5.0-5), research and opportunity
   scoring. The run stops at operator selection; the dry run on an IBKR
   snapshot is part B.

No order, alert or watchlist is ever created; the side-effect counters stay 0.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from app.broker.account_sync import AccountSyncService
from app.broker.gateway import IbAsyncGatewaySession, RecordedGatewaySession
from app.broker.ibkr_client import ReadOnlyIbkrClient
from app.broker.recordings import ACCOUNT_TOOLS, RecordedTransport
from app.broker.sources import LiveIbkrAccountSource, SimulatedFinecoUsdSource, SimulationFixture
from app.broker.sync_store import SyncStore
from app.e2e.stage4_contracts import Stage4E2ERequest, Stage4Mode
from app.e2e.stage4_runtime import CanonicalStage4Runtime, Stage4RuntimeConfiguration
from app.e2e.stage4_service import Stage4E2EService
from app.e2e.stage4_store import Stage4E2EStore
from app.e2e.stage5_portfolio import analyze_account_version, directory_from_version

DEFAULT_OUTPUT = Path("data/cache/e2e/stage5/first_e2e")

EMPTY_SCANNER_REPORTS = {
    # The IBKR scanner universe arrives with S5.0-5: until then the watch
    # universe is built from the account holdings only.
    "eligibility.json": {"audit_id": "E2E-S2.2B", "all_decisions": [], "decision_count": 0},
    "mapping.json": {"audit_id": "E2E-S2.2C-MAPPING", "provider_id": "NONE", "mapping_version": "stage5-none",
                     "mappings": [], "mapping_count": 0},
    "history.json": {"audit_id": "E2E-S2.2D-HISTORY-PILOT", "results": []},
}


def latest_reading_transport(path: Path) -> tuple[RecordedTransport, datetime]:
    data = json.loads(path.read_text(encoding="utf-8"))
    last = data["readings"][-1]
    return RecordedTransport({tool: [last[tool]] for tool in ACCOUNT_TOOLS}), datetime.fromisoformat(last["observed_at"])


def latest_close(session):
    def lookup(conid: int) -> float | None:
        try:
            return session.daily_history(conid, "SMART", duration="1 W")[-1].close
        except Exception:  # noqa: BLE001 - fall back to the fixture export price
            return None

    return lookup


def write_scanner_placeholders(directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, body in EMPTY_SCANNER_REPORTS.items():
        path = directory / name
        path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
        paths[name] = str(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["simulated", "live"], default="simulated")
    parser.add_argument("--account-recording", type=Path, default=Path("data/recordings/ibkr_account_2026-11.json"))
    parser.add_argument("--fixture", type=Path, default=Path("data/simulation/fineco_usd_2026-11-06.json"))
    parser.add_argument("--gateway-recording", type=Path, help="replay a Gateway recording instead of the live Gateway")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--db", type=Path, default=Path("data/state/stage5_cio.db"))
    parser.add_argument("--sync-db", type=Path, default=Path("data/state/stage5_account_sync.db"))
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="claude-sonnet-5-5", help="claude-* model, or ollama:<model>")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-hypotheses", type=int)
    parser.add_argument("--cache-only", action="store_true", help="no Gateway/Yahoo/model calls in research")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    args.sync_db.parent.mkdir(parents=True, exist_ok=True)
    session = RecordedGatewaySession(str(args.gateway_recording)) if args.gateway_recording \
        else IbAsyncGatewaySession(port=args.port)
    try:
        transport, observed_at = latest_reading_transport(args.account_recording)
        client = ReadOnlyIbkrClient(transport)
        if args.mode == "simulated":
            source = SimulatedFinecoUsdSource(client, SimulationFixture.load(args.fixture),
                                              price_lookup=latest_close(session), clock=lambda: observed_at)
        else:
            source = LiveIbkrAccountSource(client, clock=lambda: observed_at)
        sync = AccountSyncService(source, SyncStore(args.sync_db)).sync()
        version = sync.version
        print(f"account: {version.version_id} {sync.outcome.value} positions={len(version.reading.positions)} "
              f"origin={version.origin.value}")

        analysis = analyze_account_version(version, session, db_path=str(args.db), now=now)
        print(f"portfolio: snapshot={analysis.snapshot.snapshot_id} risk={analysis.risk_state.risk_state_id} "
              f"analyzed={len(analysis.analyzed_positions)}/{len(analysis.positions)}")
        for symbol, reason in sorted(analysis.history_failures.items()):
            print(f"  no IBKR history for {symbol}: {reason}")
        for symbol, notes in sorted(analysis.history_notes.items()):
            for note in notes:
                print(f"  {symbol}: {note}")

        reports = write_scanner_placeholders(args.output_directory / "scanner_placeholder")
        configuration = Stage4RuntimeConfiguration(
            database_path=str(args.db),
            portfolio_snapshot_id=analysis.snapshot.snapshot_id,
            portfolio_risk_state_id=analysis.risk_state.risk_state_id,
            eligibility_report_path=reports["eligibility.json"],
            mapping_report_path=reports["mapping.json"],
            history_report_paths=(reports["history.json"],),
            watch_output_directory=str(args.output_directory / "watch_universe"),
            model_name=args.model,
            timeout_seconds=args.timeout_seconds,
            max_hypotheses=args.max_hypotheses,
            evidence_source="ibkr",
            ibkr_gateway_port=args.port,
            ibkr_gateway_recording_path=str(args.gateway_recording) if args.gateway_recording else None,
        )
        request = Stage4E2ERequest(
            portfolio_file=analysis.snapshot.source_file,
            portfolio_file_fingerprint=analysis.snapshot.source_file_hash,
            account_state_id=analysis.account_state_id,
            scanner_configuration_id="stage5-first-e2e-holdings-only-v1",
            scanner_configuration_fingerprint=configuration.fingerprint,
            as_of=now,
            mode=Stage4Mode.CACHE_ONLY if args.cache_only else Stage4Mode.PREFER_CACHE,
        )
        runtime = CanonicalStage4Runtime(
            configuration,
            portfolio_positions=analysis.positions,
            instrument_directory=directory_from_version(version),
            gateway_session=session,
        )
        store = Stage4E2EStore(args.db)
        run = Stage4E2EService(store=store, adapters=runtime.adapters()).run(request, now=datetime.now(timezone.utc))
        attempts = store.list_attempts(run.run_id)
        report = {
            "audit_id": "E2E-S5.0-7A",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "account_state_version": version.version_id,
            "state_origin": version.origin.value,
            "configuration": configuration.model_dump(mode="json"),
            "request": request.model_dump(mode="json"),
            "history_failures": analysis.history_failures,
            "history_notes": {k: list(v) for k, v in analysis.history_notes.items()},
            "run": run.model_dump(mode="json"),
            "stages": [value.model_dump(mode="json") for value in store.list_stages(run.run_id)],
            "stage_attempts": [value.model_dump(mode="json") for value in attempts],
        }
        args.output_directory.mkdir(parents=True, exist_ok=True)
        path = args.output_directory / f"stage5_first_e2e_{run.run_id}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    finally:
        if hasattr(session, "close"):
            session.close()

    print("run_id:", run.run_id)
    print("run_status:", run.status.value)
    print("terminal_reason:", run.terminal_reason.value if run.terminal_reason else None)
    print("completed_stages:", [value.value for value in run.completed_stages])
    print("research_run_id:", run.research_run_id)
    print("selectable_opportunities:", list(run.selectable_opportunity_ids))
    if attempts:
        latest = attempts[-1]
        print("latest_partial_stage:", latest.stage.value, latest.reason.value if latest.reason else None)
        print("latest_partial_diagnostics:", list(latest.diagnostics))
    print("broker_orders_submitted:", run.broker_orders_submitted)
    print("portfolio_mutations:", run.portfolio_mutations)
    print("automatic_executions:", run.automatic_executions)
    print("report:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
