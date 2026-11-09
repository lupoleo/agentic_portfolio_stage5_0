"""Dry-run REHEARSAL on a copy of the Stage 5 state (S5.0-7B).

When no scored research passes the 60 threshold, part B has nothing to run
on. A rehearsal copies the databases, and in the COPY only promotes the best
below-threshold scored hypothesis of the part A run to a TradeOpportunity,
through the canonical ``materialize_trade_opportunity`` gates with the score
threshold lowered to that hypothesis' own score. Every other gate (COMPLETE
research, evidence quality, score confidence, direction, thesis) still
applies. The real databases and the policy threshold are never touched, no
model is called, and the opportunity is labelled REHEARSAL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from app.cio.models import TradeOpportunity
from app.cio.storage import Stage3Store
from app.scanner.research_integration import (
    build_research_hypotheses,
    load_watch_universe_report,
    materialize_trade_opportunity,
)
from app.scanner.research_integration_contracts import HypothesisOutcomeReason, HypothesisOutcomeStatus
from app.scanner.research_integration_store import ScannerResearchIntegrationStore

REHEARSAL_TAG = "REHEARSAL - not a real opportunity: promoted below the policy threshold on a copied database"


class RehearsalError(RuntimeError):
    pass


@dataclass(frozen=True)
class Promotion:
    opportunity_id: str
    ticker: str
    direction: str
    confidence_adjusted_score: float
    policy_threshold: float
    report_path: Path


def copy_state(paths: dict[str, Path], directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    copies = {}
    for name, source in paths.items():
        if not source.exists():
            raise RehearsalError(f"{source} does not exist")
        target = directory / source.name
        if target.resolve() == source.resolve():
            raise RehearsalError("the rehearsal directory must differ from the real state directory")
        # SQLite online backup: includes pages still in the WAL file, which a
        # plain file copy would miss; the source is opened read-only.
        target.unlink(missing_ok=True)
        src = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        copies[name] = target
    return copies


def _watch_report(report: dict) -> Path:
    run_id = report["run"]["watch_universe_run_id"]
    path = Path(report["configuration"]["watch_output_directory"]) / f"scanner_watch_universe_{run_id}.json"
    if not path.exists():
        raise RehearsalError(f"watch-universe report {path} not found")
    return path


def promote_best_below_threshold(db: Path, report: dict, *, now: datetime, ticker: str | None = None,
                                 output_directory: Path) -> Promotion:
    """In the COPIED database ``db``: materialize the best below-threshold
    scored hypothesis of the part A run and write a rehearsal part A report."""

    stage3, integration = Stage3Store(db), ScannerResearchIntegrationStore(db)
    run = integration.get_run(report["run"]["research_run_id"])
    if run is None:
        raise RehearsalError("part A research run not found in the copied database")
    below = []
    for outcome in integration.list_outcomes(run.run_id):
        if outcome.reason is not HypothesisOutcomeReason.SCORE_BELOW_THRESHOLD or not outcome.opportunity_score_id:
            continue
        score = integration.get_opportunity_score(outcome.opportunity_score_id)
        if score is None or score.confidence_adjusted_score is None:
            continue
        below.append((score.confidence_adjusted_score, outcome, score))
    if ticker:
        below = [item for item in below if item[2].ticker.upper() == ticker.upper()]
    if not below:
        raise RehearsalError("no below-threshold scored hypothesis to promote"
                             + (f" for {ticker}" if ticker else ""))
    adjusted, outcome, score = max(below, key=lambda item: item[0])

    universe = load_watch_universe_report(_watch_report(report))
    hypotheses = {h.hypothesis_id: h for h in build_research_hypotheses(universe, policy=run.policy, created_at=now)}
    hypothesis = hypotheses.get(outcome.hypothesis_id)
    if hypothesis is None or hypothesis.integration_run_id != run.run_id:
        raise RehearsalError("cannot rebuild the scored hypothesis from the watch-universe report")
    research = stage3.get_opportunity_research(outcome.research_id)
    if research is None:
        raise RehearsalError(f"research {outcome.research_id} not found")

    rehearsal_policy = run.policy.model_copy(update={"minimum_confidence_adjusted_score": adjusted})
    decision, opportunity, link = materialize_trade_opportunity(
        hypothesis, research, score, portfolio_snapshot_id=run.portfolio_snapshot_id, created_at=now,
        policy=rehearsal_policy,
    )
    if opportunity is None:
        raise RehearsalError(f"{score.ticker} fails a non-threshold gate: {decision.reason.value} ({decision.message})")
    opportunity = TradeOpportunity.model_validate(
        {**opportunity.model_dump(), "notes": f"{REHEARSAL_TAG}; {opportunity.notes}"})
    stage3.save_trade_opportunity(opportunity)
    integration.save_provenance_link(link)
    integration.save_outcome(outcome.model_copy(update={
        "status": HypothesisOutcomeStatus.OPPORTUNITY_CREATED,
        "reason": HypothesisOutcomeReason.OPPORTUNITY_CREATED,
        "opportunity_id": opportunity.opportunity_id,
        "updated_at": now,
        "diagnostics": (REHEARSAL_TAG,),
    }))
    integration.save_run(run.model_copy(update={
        "opportunity_ids": tuple(sorted({*run.opportunity_ids, opportunity.opportunity_id})),
    }))

    rehearsal = json.loads(json.dumps(report))
    rehearsal["audit_id"] = f"{report.get('audit_id', 'E2E-S5.0-7A')}-REHEARSAL"
    rehearsal["rehearsal"] = {
        "note": REHEARSAL_TAG,
        "opportunity_id": opportunity.opportunity_id,
        "confidence_adjusted_score": adjusted,
        "policy_threshold": run.policy.minimum_confidence_adjusted_score,
    }
    rehearsal["run"]["selectable_opportunity_ids"] = [opportunity.opportunity_id]
    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / f"stage5_first_e2e_rehearsal_{run.run_id}.json"
    path.write_text(json.dumps(rehearsal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return Promotion(opportunity.opportunity_id, opportunity.ticker, opportunity.direction.value, adjusted,
                     run.policy.minimum_confidence_adjusted_score, path)
