"""Bounded, resumable E2E-S2.2F Scanner-to-Research pilot."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from app.ai.company_assessment import CompanyAssessmentService
from app.ai.canonical_technical import build_canonical_technical_input
from app.ai.analyst_evidence_provider import YahooAnalystEvidenceProvider
from app.ai.fundamental_evidence_provider import YahooFundamentalEvidenceProvider
from app.ai.local_provider import LocalProvider
from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
from app.ai.news_evidence_provider import YahooNewsEvidenceProvider
from app.ai.opportunity_scoring_service import OpportunityScoringService
from app.ai.research_service import ResearchService
from app.cio.storage import Stage3Store
from app.scanner.research_integration import load_watch_universe_report
from app.scanner.research_integration_contracts import IntegrationRunStatus
from app.scanner.research_integration_service import ScannerResearchIntegrationService
from app.scanner.research_integration_store import ScannerResearchIntegrationStore


DEFAULT_DB = Path("data/state/portfolio_cio.db")
DEFAULT_OUTPUT = Path("data/cache/scanner/research_integration")


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch-universe-report", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-hypotheses", type=int)
    parser.add_argument("--cache-only", action="store_true")
    args = parser.parse_args()

    universe = load_watch_universe_report(args.watch_universe_report)
    now = datetime.now(timezone.utc)
    stage3_store = Stage3Store(args.db)
    integration_store = ScannerResearchIntegrationStore(args.db)
    if args.cache_only:
        research_service = scoring_service = market = news = None
        fundamental = analyst = None
        technical_loader = None
    else:
        provider = LocalProvider(
            model_name=args.model, timeout_seconds=args.timeout_seconds,
        )
        research_service = ResearchService(provider)
        scoring_service = OpportunityScoringService(
            provider,
            company_assessment_service=CompanyAssessmentService(provider),
        )
        market = YahooMarketEvidenceProvider()
        news = YahooNewsEvidenceProvider()
        fundamental = YahooFundamentalEvidenceProvider()
        analyst = YahooAnalystEvidenceProvider()
        technical_loader = build_canonical_technical_input
    service = ScannerResearchIntegrationService(
        stage3_store=stage3_store,
        integration_store=integration_store,
        research_service=research_service,
        scoring_service=scoring_service,
        market_provider=market,
        news_provider=news,
        fundamental_provider=fundamental,
        analyst_provider=analyst,
        canonical_technical_loader=technical_loader,
    )
    print("checkpoint: E2E-S2.2F")
    print("watch_universe_run_id:", universe.run_id)
    print("watch_universe_fingerprint:", universe.fingerprint)
    print("portfolio_snapshot_id:", universe.portfolio_snapshot_id)
    print("member_count:", len(universe.members))
    print("mode:", "cache-only" if args.cache_only else "live")
    run = service.run(
        universe,
        now=now,
        max_hypotheses=args.max_hypotheses,
        cache_only=args.cache_only,
    )
    outcomes = integration_store.list_outcomes(run.run_id)
    status_counts = Counter(value.status.value for value in outcomes)
    reason_counts = Counter(value.reason.value for value in outcomes)
    report = {
        "audit_id": "E2E-S2.2F",
        "schema": "scanner-research-integration-run-v1",
        "generated_at": now.isoformat(),
        "mode": "cache-only" if args.cache_only else "live",
        "run": run.model_dump(mode="json"),
        "summary": {
            "hypothesis_count": len(run.hypothesis_ids),
            "completed_count": len(run.completed_hypothesis_ids),
            "opportunity_count": len(run.opportunity_ids),
            "status_counts": dict(sorted(status_counts.items())),
            "reason_counts": dict(sorted(reason_counts.items())),
        },
        "outcomes": [value.model_dump(mode="json") for value in outcomes],
    }
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    path = args.output_directory / f"scanner_research_integration_{stamp}.json"
    _atomic_json(path, report)
    print("run_id:", run.run_id)
    print("run_status:", run.status.value)
    print("hypothesis_count:", len(run.hypothesis_ids))
    print("completed_count:", len(run.completed_hypothesis_ids))
    print("opportunity_count:", len(run.opportunity_ids))
    print("status_counts:", dict(sorted(status_counts.items())))
    print("reason_counts:", dict(sorted(reason_counts.items())))
    print("report:", path)
    if run.status is IntegrationRunStatus.COMPLETED:
        print("S2.2F_INTEGRATION: PASS")
        return 0
    print("S2.2F_INTEGRATION: PARTIAL")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
