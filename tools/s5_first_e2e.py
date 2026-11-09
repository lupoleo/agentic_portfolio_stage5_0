"""First Stage 5 end-to-end run from the IBKR account (S5.0-7, part A).

Run on the operator's PC with IB Gateway logged in (Read-Only API, port 4001)
and ANTHROPIC_API_KEY set:

    python -m tools.s5_first_e2e [--gateway-recording data/recordings/gateway_fixtures_<ts>.json]

Steps, all read-only:

1. account state: the real IBKR account (summary, cash per currency,
   positions, open orders) read live through IB Gateway, versioned in the
   Stage 5 sync store;
2. portfolio analysis on IB Gateway history (4.0 quant engines unchanged):
   AccountState, PortfolioSnapshot and PortfolioRiskState bound to the
   ``ibkr://`` state version;
3. the IBKR scanner universe (S5.0-5): read-only IB market scanners, contract
   details and daily bars, gated by the 4.0 eligibility and history-quality
   rules (``--no-scanner`` keeps the holdings-only universe);
4. the 4.0 E2E service with IBKR evidence channels and Claude: watch universe
   (holdings + scanner candidates), research and opportunity scoring. The run stops at operator selection;
   ``python -m tools.s5_dry_run`` (part B) then dry-runs the selected opportunity on the same IBKR snapshot.

No order, alert or watchlist is ever created; the side-effect counters stay 0.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from app.broker.account_sync import AccountSyncService
from app.broker.gateway import IbAsyncGatewaySession, RecordedGatewaySession
from app.broker.sources import gateway_account_source
from app.broker.sync_store import SyncStore
from app.e2e.stage4_contracts import Stage4E2ERequest, Stage4Mode
from app.e2e.stage4_runtime import CanonicalStage4Runtime, Stage4RuntimeConfiguration
from app.e2e.stage4_service import Stage4E2EService
from app.e2e.stage4_store import Stage4E2EStore
from app.e2e.stage5_dry_run import NlvRiskPolicy
from app.e2e.stage5_portfolio import analyze_account_version, directory_from_version
from app.scanner.ibkr_universe import DEFAULT_PLAN, ScannerReports, acquire_universe, build_scanner_reports

DEFAULT_OUTPUT = Path("data/cache/e2e/stage5/first_e2e")

DEFAULT_SCANNER_MAX_HYPOTHESES = 10  # Claude research cost cap when scanner candidates are on

EMPTY_SCANNER_REPORTS = {
    # ``--no-scanner``: the watch universe is built from the account holdings only.
    "eligibility.json": {"audit_id": "E2E-S2.2B", "all_decisions": [], "decision_count": 0},
    "mapping.json": {"audit_id": "E2E-S2.2C-MAPPING", "provider_id": "NONE", "mapping_version": "stage5-none",
                     "mappings": [], "mapping_count": 0},
    "history.json": {"audit_id": "E2E-S2.2D-HISTORY-PILOT", "results": []},
}


def write_scanner_placeholders(directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, body in EMPTY_SCANNER_REPORTS.items():
        path = directory / name
        path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
        paths[name] = str(path)
    return paths


def merge_scanner_refs(directory, refs) -> list[str]:
    """Add scanner candidates to the instrument directory; a ticker already
    bound to another conid (a holding or reference ETF) keeps its binding."""
    skipped = []
    for ref in refs:
        existing = directory.get(ref.ticker)
        if existing is None:
            directory.add(ref)
        elif existing.conid != ref.conid:
            skipped.append(ref.ticker)
    return skipped


def run_scanner(session, version, output: Path, *, as_of, max_listings, max_candidates, require_uptrend=True):
    held = frozenset(p.conid for p in version.reading.positions if getattr(p, "conid", None))
    acquisition = acquire_universe(session, DEFAULT_PLAN, now=as_of)
    failed = [s for s in acquisition.scans if s.error]
    print(f"scanner: plan={DEFAULT_PLAN.plan_id} scans={len(acquisition.scans)} failed={len(failed)} "
          f"listings={len(acquisition.listings)}")
    for scan in failed:
        print(f"  scan {scan.query.location} {scan.query.scan_code}: {scan.error}")
    reports = build_scanner_reports(session, acquisition, as_of=as_of, max_listings=max_listings,
                                    max_candidates=max_candidates, require_uptrend=require_uptrend, exclude_conids=held, progress=lambda line: print("  " + line))
    paths = reports.write(output)
    print("scanner_counts:", reports.summary["counts"])
    if reports.summary["fx_errors"]:
        print("scanner_fx_errors:", reports.summary["fx_errors"])
    return reports, paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gateway-recording", type=Path, help="replay a Gateway recording instead of the live Gateway")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--db", type=Path, default=Path("data/state/stage5_cio.db"))
    parser.add_argument("--sync-db", type=Path, default=Path("data/state/stage5_account_sync.db"))
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="claude-sonnet-5-5", help="claude-* model, or ollama:<model>")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-hypotheses", type=int)
    parser.add_argument("--cache-only", action="store_true", help="no Gateway/Yahoo/model calls in research")
    parser.add_argument("--no-scanner", action="store_true", help="holdings-only watch universe")
    parser.add_argument("--scanner-any-trend", action="store_true",
                        help="also admit candidates whose close is below the 50-session average")
    parser.add_argument("--reuse-scanner", type=Path, nargs="?", const=DEFAULT_OUTPUT / "scanner",
                        help="reuse the scanner reports saved by a previous run (default: the last run's "
                             "<output>/scanner) instead of scanning again")
    parser.add_argument("--scanner-max-listings", type=int, default=60,
                        help="distinct scanner listings evaluated at most (each costs Gateway calls)")
    parser.add_argument("--scanner-candidates", type=int,
                        help="stop after this many STANDARD candidates, best scanner rank first "
                             "(default: half of --max-hypotheses, one long and one short hypothesis each)")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    args.sync_db.parent.mkdir(parents=True, exist_ok=True)
    session = RecordedGatewaySession(str(args.gateway_recording)) if args.gateway_recording \
        else IbAsyncGatewaySession(port=args.port)
    try:
        sync = AccountSyncService(gateway_account_source(session, clock=lambda: now), SyncStore(args.sync_db)).sync()
        version = sync.version
        print(f"account: {version.version_id} {sync.outcome.value} positions={len(version.reading.positions)} "
              f"origin={version.origin.value}")

        nlv = version.reading.metrics.net_liquidation
        constraints = NlvRiskPolicy().constraints(nlv)
        print(f"pf1: NLV {nlv:.2f} EUR -> max trade loss {constraints.max_trade_loss_eur} EUR, "
              f"max position weight {constraints.max_position_weight_pct}%")
        analysis = analyze_account_version(version, session, db_path=str(args.db), now=now, constraints=constraints)
        print(f"portfolio: snapshot={analysis.snapshot.snapshot_id} risk={analysis.risk_state.risk_state_id} "
              f"analyzed={len(analysis.analyzed_positions)}/{len(analysis.positions)}")
        for symbol, reason in sorted(analysis.history_failures.items()):
            print(f"  no IBKR history for {symbol}: {reason}")
        for symbol, notes in sorted(analysis.history_notes.items()):
            for note in notes:
                print(f"  {symbol}: {note}")

        directory = directory_from_version(version)
        scanner = None
        max_hypotheses = args.max_hypotheses
        if args.no_scanner:
            reports = write_scanner_placeholders(args.output_directory / "scanner_placeholder")
        else:
            if max_hypotheses is None:
                max_hypotheses = DEFAULT_SCANNER_MAX_HYPOTHESES
            if args.reuse_scanner is not None:
                scanner = ScannerReports.load(args.reuse_scanner)
                reports = {name: str(args.reuse_scanner / name)
                           for name in ("eligibility.json", "mapping.json", "history.json")}
                print(f"scanner: REUSED {args.reuse_scanner} (acquired_at={scanner.summary.get('acquired_at')}) "
                      f"candidates={[ref.ticker for ref in scanner.instrument_refs]}")
            else:
                candidates = args.scanner_candidates or max(1, max_hypotheses // 2)
                scanner, reports = run_scanner(session, version, args.output_directory / "scanner", as_of=now,
                                               max_listings=args.scanner_max_listings, max_candidates=candidates,
                                               require_uptrend=not args.scanner_any_trend)
            skipped = merge_scanner_refs(directory, scanner.instrument_refs)
            if skipped:
                print("scanner tickers already bound to another conid:", skipped)
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
            max_hypotheses=max_hypotheses,
            evidence_source="ibkr",
            ibkr_gateway_port=args.port,
            ibkr_gateway_recording_path=str(args.gateway_recording) if args.gateway_recording else None,
        )
        request = Stage4E2ERequest(
            portfolio_file=analysis.snapshot.source_file,
            portfolio_file_fingerprint=analysis.snapshot.source_file_hash,
            account_state_id=analysis.account_state_id,
            scanner_configuration_id=("stage5-first-e2e-holdings-only-v1" if scanner is None
                                      else f"stage5-{DEFAULT_PLAN.plan_id}-v{DEFAULT_PLAN.version}"),
            scanner_configuration_fingerprint=configuration.fingerprint,
            as_of=now,
            mode=Stage4Mode.CACHE_ONLY if args.cache_only else Stage4Mode.PREFER_CACHE,
        )
        runtime = CanonicalStage4Runtime(
            configuration,
            portfolio_positions=analysis.positions,
            instrument_directory=directory,
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
            "scanner": scanner.summary if scanner is not None else None,
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
