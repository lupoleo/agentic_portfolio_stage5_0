"""Dry-run rehearsal on copied state: promote a below-threshold score in the copy only."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.cio.storage import Stage3Store
from app.scanner.research_integration import build_research_hypotheses, load_watch_universe_report
from app.scanner.research_integration_contracts import (
    HypothesisOutcomeReason, HypothesisOutcomeStatus, ResearchHypothesisKind, ResearchHypothesisOutcome,
)
from app.scanner.research_integration_store import ScannerResearchIntegrationStore
from tests.test_scanner_research_integration import research as make_research, score as make_score
from tests.test_stage5_first_e2e import test_scanner_candidates_join_the_watch_universe as run_part_a_with_scanner
from tools import s5_rehearsal


def seed_below_threshold(tmp_path, adjusted=59.4):
    """Part A scored NEWC long at ``adjusted`` (< 60): what the live research produced for UCG.MI."""
    (report_path,) = (tmp_path / "out").glob("stage5_first_e2e_*.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    db = tmp_path / "cio.db"
    stage3, integration = Stage3Store(db), ScannerResearchIntegrationStore(db)
    run = integration.get_run(report["run"]["research_run_id"])
    watch = tmp_path / "out" / "watch_universe" / f"scanner_watch_universe_{report['run']['watch_universe_run_id']}.json"
    universe = load_watch_universe_report(watch)
    (hypothesis,) = [h for h in build_research_hypotheses(universe, policy=run.policy, created_at=run.as_of)
                     if h.ticker == "NEWC" and h.kind is ResearchHypothesisKind.NEW_LONG]
    research = make_research(hypothesis, portfolio_snapshot_id=run.portfolio_snapshot_id)
    score = make_score(hypothesis, research, portfolio_snapshot_id=run.portfolio_snapshot_id,
                       confidence_adjusted_score=adjusted, score_confidence=0.68)
    stage3.save_opportunity_research(research)
    integration.save_opportunity_score(score)
    integration.save_outcome(ResearchHypothesisOutcome(
        integration_run_id=run.run_id, hypothesis_id=hypothesis.hypothesis_id,
        subject_namespace=hypothesis.subject_namespace, subject_value=hypothesis.subject_value,
        kind=hypothesis.kind, status=HypothesisOutcomeStatus.EXCLUDED,
        reason=HypothesisOutcomeReason.SCORE_BELOW_THRESHOLD, updated_at=run.as_of,
        research_id=research.research_id, opportunity_score_id=score.opportunity_score_id))
    return report_path


def counts(db):
    with sqlite3.connect(db) as c:
        return (c.execute("SELECT COUNT(*) FROM trade_opportunities").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM trade_opportunity_provenance").fetchone()[0])


def test_rehearsal_promotes_in_the_copy_and_reaches_an_execution_plan(tmp_path, capsys):
    run_part_a_with_scanner(tmp_path, capsys)
    report_path = seed_below_threshold(tmp_path)
    before = counts(tmp_path / "cio.db")
    code = s5_rehearsal.main([
        "--report", str(report_path), "--output-directory", str(tmp_path / "out"),
        "--db", str(tmp_path / "cio.db"), "--sync-db", str(tmp_path / "sync.db"),
        "--rehearsal-directory", str(tmp_path / "rehearsal"),
        "--gateway-recording", str(tmp_path / "gateway.json"), "--exposure-eur", "300",
    ])
    out = capsys.readouterr().out
    assert "promoted (REHEARSAL): NEWC LONG adjusted score 59.40 < policy threshold 60" in out
    assert counts(tmp_path / "cio.db") == before  # the real database is untouched
    assert code == 0, out
    assert "S5_DRY_RUN: PASS" in out and "broker_orders_submitted: 0" in out
    with sqlite3.connect(tmp_path / "rehearsal" / "cio.db") as c:
        (notes,) = c.execute("SELECT payload_json FROM trade_opportunities").fetchone()
    assert "REHEARSAL" in notes


def test_rehearsal_reports_when_nothing_was_scored(tmp_path, capsys):
    run_part_a_with_scanner(tmp_path, capsys)  # cache-only part A: no score at all
    code = s5_rehearsal.main([
        "--output-directory", str(tmp_path / "out"), "--db", str(tmp_path / "cio.db"),
        "--sync-db", str(tmp_path / "sync.db"), "--rehearsal-directory", str(tmp_path / "rehearsal"),
    ])
    out = capsys.readouterr().out
    assert code == 2 and "S5_REHEARSAL: NOT_POSSIBLE (no below-threshold scored hypothesis to promote)" in out
