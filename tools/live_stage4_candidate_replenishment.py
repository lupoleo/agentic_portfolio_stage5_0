"""Run E2E-S4.0A.1 until a selectable TradeOpportunity or safety stop."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from app.e2e.stage4_contracts import Stage4Mode
from app.e2e.stage4_frontier_priority import NewsSensitiveFrontierRanker
from app.e2e.stage4_replenishment import (
    candidate_frontier_evidence,
    create_replenishment_session,
    portfolio_watch_yahoo_symbols,
)
from app.e2e.stage4_replenishment_contracts import (
    CandidateReplenishmentPolicy,
    ReplenishmentStopReason,
)
from app.e2e.stage4_replenishment_runtime import (
    CanonicalStage4CandidateWaveExecutor,
    Stage4ReplenishmentRuntimeConfiguration,
)
from app.e2e.stage4_replenishment_service import (
    Stage4CandidateReplenishmentService,
)
from app.e2e.stage4_replenishment_store import (
    Stage4CandidateReplenishmentStore,
)
from app.e2e.stage4_store import Stage4E2EStore
from app.scanner.research_integration import load_watch_universe_report
from app.scanner.research_integration_store import ScannerResearchIntegrationStore


DEFAULT_DB = Path("data/state/portfolio_cio.db")
DEFAULT_OUTPUT = Path("data/cache/e2e/stage4/candidate_replenishment")


def _aware(value: str) -> datetime:
    normalized = value.strip()
    if normalized[-1:] in {"Z", "z"}:
        normalized = normalized[:-1] + "+00:00"
    normalized = re.sub(
        r"(\.\d{6})\d+(?=[+-]\d\d:\d\d$)",
        r"\1",
        normalized,
    )
    result = datetime.fromisoformat(normalized)
    if result.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return result


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-run-id", required=True)
    parser.add_argument("--portfolio-file", type=Path, required=True)
    parser.add_argument("--eligibility-report", type=Path, required=True)
    parser.add_argument("--mapping-report", type=Path, required=True)
    parser.add_argument("--as-of", type=_aware, required=True)
    parser.add_argument(
        "--mode",
        choices=(Stage4Mode.LIVE.value, Stage4Mode.CACHE_ONLY.value),
        default=Stage4Mode.LIVE.value,
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--listing-references", type=Path)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--history-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--pause-seconds", type=float, default=2.0)
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--max-hypotheses", type=int, default=4)
    parser.add_argument("--max-waves", type=int, default=5)
    args = parser.parse_args()

    stage4_store = Stage4E2EStore(args.db)
    root = stage4_store.get_run(args.root_run_id)
    if root is None:
        raise SystemExit("ROOT_RUN_NOT_FOUND")
    if not root.portfolio_snapshot_id or not root.portfolio_risk_state_id:
        raise SystemExit("ROOT_RUN_PORTFOLIO_LINEAGE_INCOMPLETE")
    if root.execution_plan_id:
        raise SystemExit("ROOT_RUN_ALREADY_HAS_EXECUTION_PLAN")
    if any((
        root.broker_orders_submitted,
        root.portfolio_mutations,
        root.automatic_executions,
    )):
        raise SystemExit("ROOT_RUN_SIDE_EFFECT_INVARIANT_VIOLATED")

    integration_store = ScannerResearchIntegrationStore(args.db)
    prior_outcomes = (
        tuple(integration_store.list_outcomes(root.research_run_id))
        if root.research_run_id else ()
    )
    attempted = set()
    portfolio_yahoo_symbols = ()
    if root.watch_universe_report_path:
        universe = load_watch_universe_report(
            root.watch_universe_report_path
        )
        portfolio_yahoo_symbols = portfolio_watch_yahoo_symbols(
            universe
        )
        for member in universe.members:
            if "NEW_CANDIDATE" in member.provenances:
                attempted.update(member.candidate_keys)

    mode = Stage4Mode(args.mode)
    policy = CandidateReplenishmentPolicy(max_waves=args.max_waves)
    session = create_replenishment_session(
        root_run_id=root.run_id,
        portfolio_snapshot_id=root.portfolio_snapshot_id,
        portfolio_risk_state_id=root.portfolio_risk_state_id,
        as_of=args.as_of,
        mode=mode,
        policy=policy,
    )
    configuration = Stage4ReplenishmentRuntimeConfiguration(
        database_path=str(args.db),
        portfolio_file=str(args.portfolio_file),
        account_state_id=root.account_state_id,
        portfolio_snapshot_id=root.portfolio_snapshot_id,
        portfolio_risk_state_id=root.portfolio_risk_state_id,
        eligibility_report_path=str(args.eligibility_report),
        mapping_report_path=str(args.mapping_report),
        history_output_directory=str(args.output_directory / "history"),
        child_output_directory=str(args.output_directory / "children"),
        listing_references_path=(
            str(args.listing_references)
            if args.listing_references else None
        ),
        model_name=args.model,
        timeout_seconds=args.timeout_seconds,
        history_timeout_seconds=args.history_timeout_seconds,
        pause_seconds=args.pause_seconds,
        lookback_days=args.lookback_days,
        max_hypotheses=args.max_hypotheses,
    )
    eligibility_data = _load(args.eligibility_report)
    mapping_data = _load(args.mapping_report)
    initial_frontier_evidence = candidate_frontier_evidence(
        eligibility_data,
        mapping_data,
        attempted_listing_keys=attempted,
        excluded_yahoo_symbols=portfolio_yahoo_symbols,
        policy=policy,
    )
    executor = CanonicalStage4CandidateWaveExecutor(configuration)
    store = Stage4CandidateReplenishmentStore(args.db)
    frontier_ranker = NewsSensitiveFrontierRanker(store=store)
    result = Stage4CandidateReplenishmentService(
        store=store,
        executor=executor,
        frontier_ranker=frontier_ranker,
    ).run(
        session=session,
        eligibility=eligibility_data,
        mapping=mapping_data,
        prior_outcomes=prior_outcomes,
        attempted_listing_keys=attempted,
        excluded_yahoo_symbols=portfolio_yahoo_symbols,
        now=datetime.now(timezone.utc),
    )
    final_frontier_evidence = candidate_frontier_evidence(
        eligibility_data,
        mapping_data,
        attempted_listing_keys=result.attempted_listing_keys,
        excluded_yahoo_symbols=portfolio_yahoo_symbols,
        policy=policy,
    )
    waves = store.list_waves(session.session_id)
    frontier_ranking = store.get_frontier_ranking(session.session_id)
    report = {
        "audit_id": "E2E-S4.0A.1",
        "ranking_checkpoint": "E2E-S4.0A.2",
        "schema": "stage4-bounded-candidate-replenishment-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": configuration.model_dump(mode="json"),
        "session": session.model_dump(mode="json"),
        "result": {
            "session_id": result.session_id,
            "root_run_id": result.root_run_id,
            "terminal_reason": result.terminal_reason.value,
            "opportunity_ids": list(result.opportunity_ids),
            "attempted_listing_keys": list(
                result.attempted_listing_keys
            ),
            "wave_record_ids": list(result.wave_record_ids),
            "retry_exhausted_outcome_count": (
                result.retry_exhausted_outcome_count
            ),
            "quarantined_listing_keys": list(
                result.quarantined_listing_keys
            ),
            "quarantined_hypothesis_ids": list(
                result.quarantined_hypothesis_ids
            ),
            "quarantine_reason_counts": list(
                result.quarantine_reason_counts
            ),
            "source_research_run_ids": list(
                result.source_research_run_ids
            ),
            "frontier_ranking_snapshot_id": (
                result.frontier_ranking_snapshot_id
            ),
            "frontier_ranking_mode": result.frontier_ranking_mode,
            "frontier_ranking_iteration_count": (
                result.frontier_ranking_iteration_count
            ),
            "frontier_ranking_screened_count": (
                result.frontier_ranking_screened_count
            ),
            "frontier_ranking_qualified_count": (
                result.frontier_ranking_qualified_count
            ),
            "frontier_ranking_fallback_count": (
                result.frontier_ranking_fallback_count
            ),
            "frontier_ranking_network_calls": (
                result.frontier_ranking_network_calls
            ),
            "frontier_ranking_provider_circuit_open": (
                result.frontier_ranking_provider_circuit_open
            ),
            "broker_orders_submitted": 0,
            "portfolio_mutations": 0,
            "automatic_executions": 0,
        },
        "frontier_evidence": {
            "initial": initial_frontier_evidence,
            "final": final_frontier_evidence,
            "portfolio_watch_yahoo_symbols": list(
                portfolio_yahoo_symbols
            ),
        },
        "frontier_ranking": (
            frontier_ranking.model_dump(mode="json")
            if frontier_ranking is not None else None
        ),
        "diagnostics": [
            ("initial_acquirable_frontier_count="
             + str(initial_frontier_evidence[
                 "acquirable_frontier_count"
             ])),
            ("final_acquirable_frontier_count="
             + str(final_frontier_evidence[
                 "acquirable_frontier_count"
             ])),
            ("portfolio_overlap_excluded_count="
             + str(initial_frontier_evidence[
                 "portfolio_overlap_supported_eligible_count"
             ])),
        ],
        "waves": [value.model_dump(mode="json") for value in waves],
    }
    path = args.output_directory / (
        f"stage4_candidate_replenishment_{session.session_id}.json"
    )
    _atomic(path, report)

    print("checkpoint: E2E-S4.0A.1")
    print("mode:", mode.value)
    print("session_id:", session.session_id)
    print("root_run_id:", root.run_id)
    print("terminal_reason:", result.terminal_reason.value)
    print("wave_count:", len(waves))
    print(
        "supported_eligible_count:",
        initial_frontier_evidence["supported_eligible_count"],
    )
    print(
        "resolved_supported_eligible_count:",
        initial_frontier_evidence[
            "resolved_supported_eligible_count"
        ],
    )
    print(
        "initial_acquirable_frontier_count:",
        initial_frontier_evidence[
            "acquirable_frontier_count"
        ],
    )
    print(
        "portfolio_watch_yahoo_symbol_count:",
        len(portfolio_yahoo_symbols),
    )
    print(
        "portfolio_overlap_excluded_count:",
        initial_frontier_evidence[
            "portfolio_overlap_supported_eligible_count"
        ],
    )
    print("attempted_listing_count:", len(result.attempted_listing_keys))
    print(
        "final_acquirable_frontier_count:",
        final_frontier_evidence["acquirable_frontier_count"],
    )
    print("opportunity_ids:", list(result.opportunity_ids))
    print(
        "retry_exhausted_outcome_count:",
        result.retry_exhausted_outcome_count,
    )
    print(
        "quarantined_listing_keys:",
        list(result.quarantined_listing_keys),
    )
    print(
        "quarantined_hypothesis_ids:",
        list(result.quarantined_hypothesis_ids),
    )
    print(
        "quarantine_reason_counts:",
        list(result.quarantine_reason_counts),
    )
    print(
        "source_research_run_ids:",
        list(result.source_research_run_ids),
    )
    print(
        "frontier_ranking_snapshot_id:",
        result.frontier_ranking_snapshot_id,
    )
    print("frontier_ranking_mode:", result.frontier_ranking_mode)
    print(
        "frontier_ranking_iterations:",
        result.frontier_ranking_iteration_count,
    )
    print(
        "frontier_ranking_screened_count:",
        result.frontier_ranking_screened_count,
    )
    print(
        "frontier_ranking_qualified_count:",
        result.frontier_ranking_qualified_count,
    )
    print(
        "frontier_ranking_fallback_count:",
        result.frontier_ranking_fallback_count,
    )
    print(
        "frontier_ranking_network_calls:",
        result.frontier_ranking_network_calls,
    )
    print(
        "frontier_ranking_provider_circuit_open:",
        result.frontier_ranking_provider_circuit_open,
    )
    for wave in waves:
        print(
            f"wave {wave.wave_index}: "
            f"{','.join(wave.listing_keys)} | "
            f"{wave.status.value} | "
            f"opportunities={len(wave.opportunity_ids)}"
        )
    print("broker_orders_submitted: 0")
    print("portfolio_mutations: 0")
    print("automatic_executions: 0")
    print("report:", path)

    if result.opportunity_found:
        print("S4A1_REPLENISHMENT: OPPORTUNITY_FOUND")
        return 0
    if result.terminal_reason in {
        ReplenishmentStopReason.PENDING_DIRECTIONAL_WORK,
        ReplenishmentStopReason.UNIVERSE_EXHAUSTED,
        ReplenishmentStopReason.REPLENISHMENT_BUDGET_EXHAUSTED,
        ReplenishmentStopReason.SAFETY_BLOCK,
    }:
        print("S4A1_REPLENISHMENT: VALID_NON_COMPLETE")
        return 2
    print("S4A1_REPLENISHMENT: FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
