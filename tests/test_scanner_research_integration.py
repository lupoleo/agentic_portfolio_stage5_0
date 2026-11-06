from datetime import datetime, timezone
from types import SimpleNamespace

from app.ai.opportunity_score_models import (
    OpportunityScore,
    OpportunityScoringStatus,
)
from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    OpportunityResearch,
    ResearchStatus,
)
from app.ai.scan_models import CandidateAction, CandidateOrigin
from app.cio.models import Direction
from app.scanner.research_integration import (
    build_market_scan,
    build_opportunity_score,
    build_research_hypotheses,
    hypothesis_direction,
    integration_run_id,
    load_watch_universe_report,
    materialize_trade_opportunity,
    opportunity_materialization_decision,
)
from app.scanner.research_integration_contracts import (
    HypothesisOutcomeReason,
    ResearchHypothesisKind,
    ResearchWatchMemberInput,
    ResearchWatchUniverseInput,
    ScannerResearchIntegrationPolicy,
)


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def universe():
    return ResearchWatchUniverseInput(
        run_id="watch-001",
        fingerprint="a" * 64,
        as_of=NOW,
        portfolio_snapshot_id="snap-001",
        members=(
            ResearchWatchMemberInput(
                subject_namespace="YAHOO", subject_value="A2A.MI",
                provenances=("NEW_CANDIDATE",), yahoo_symbols=("A2A.MI",),
                candidate_keys=("BIT:A2A",), readiness="READY",
            ),
            ResearchWatchMemberInput(
                subject_namespace="YAHOO", subject_value="MSFT",
                provenances=("CURRENT_POSITION",), yahoo_symbols=("MSFT",),
                position_refs=("pos-1",), readiness="READY",
            ),
            ResearchWatchMemberInput(
                subject_namespace="ISIN", subject_value="US0000000001",
                provenances=("NEW_CANDIDATE",), yahoo_symbols=(),
                readiness="DEGRADED",
            ),
        ),
    )


def research(hypothesis, **overrides):
    data = dict(
        research_id=f"research-{hypothesis.hypothesis_id}",
        candidate_id=hypothesis.candidate.candidate_id,
        scan_id=hypothesis.candidate.scan_id,
        created_at=NOW,
        ticker=hypothesis.ticker,
        portfolio_snapshot_id="snap-001",
        risk_state_id=None,
        research_status=ResearchStatus.COMPLETE,
        market_context="Constructive market context.",
        fundamental_context="Fundamental evidence is supportive.",
        technical_context="Technical evidence is supportive.",
        event_context="No immediate event dependency.",
        catalyst_assessment="Execution can re-rate the shares.",
        expectations_assessment=ExpectationsAssessment.PARTIALLY_PRICED_IN,
        bull_case="Execution can produce upside.",
        bear_case="Weak execution can produce downside.",
        key_risks=["Execution risk"],
        contradictory_evidence=["Valuation is demanding"],
        unknowns=[],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=0.80,
        evidence_ids=["e1", "e2"],
        inference_ids=["i1"],
        requires_additional_research=False,
    )
    data.update(overrides)
    return OpportunityResearch(**data)


def score(hypothesis, research_value, **overrides):
    data = dict(
        opportunity_score_id=f"score-{hypothesis.hypothesis_id}",
        candidate_id=hypothesis.candidate.candidate_id,
        research_id=research_value.research_id,
        scan_id=hypothesis.candidate.scan_id,
        created_at=NOW,
        ticker=hypothesis.ticker,
        scoring_status=OpportunityScoringStatus.SCORED,
        thesis_score=75,
        catalyst_score=70,
        fundamental_score=72,
        technical_score=68,
        expectations_score=65,
        raw_score=70,
        score_confidence=0.60,
        confidence_adjusted_score=62,
        evidence_quality=research_value.evidence_quality,
        evidence_coverage_score=0.80,
        research_confidence=research_value.research_confidence,
        evidence_ids=research_value.evidence_ids,
        inference_ids=[],
        requires_additional_research=False,
        portfolio_snapshot_id="snap-001",
    )
    direction = hypothesis_direction(hypothesis.kind)
    if direction is not None:
        data["metadata"] = {"scoring_diagnostics": {"direction": {
            "direction": direction.value,
            "direction_source": "HYPOTHESIS",
            "policy_version": "ai-8c3-directional-scoring-v3",
            "company_assessment": {"sources": {
                "fundamental": "SHARED_COMPANY_ASSESSMENT",
                "expectations": "SHARED_COMPANY_ASSESSMENT",
            }},
        }}}
    data.update(overrides)
    return OpportunityScore(**data)


def test_hypothesis_generation_is_unbiased_and_monitor_only_for_portfolio():
    values = build_research_hypotheses(universe(), created_at=NOW)
    assert [value.kind for value in values] == [
        ResearchHypothesisKind.NEW_LONG,
        ResearchHypothesisKind.NEW_SHORT,
        ResearchHypothesisKind.PORTFOLIO_MONITOR,
    ]
    assert values[0].candidate.origin is CandidateOrigin.EXTERNAL
    assert values[0].candidate.action is CandidateAction.NEW_LONG
    assert values[1].candidate.action is CandidateAction.NEW_SHORT
    assert values[2].candidate.origin is CandidateOrigin.PORTFOLIO
    assert values[2].candidate.action is CandidateAction.NO_ACTION


def test_ids_are_deterministic_and_scan_preserves_watch_provenance():
    first = build_research_hypotheses(universe(), created_at=NOW)
    second = build_research_hypotheses(universe(), created_at=NOW)
    assert [x.hypothesis_id for x in first] == [x.hypothesis_id for x in second]
    scan = build_market_scan(universe(), first, created_at=NOW)
    assert scan.candidate_ids == [x.hypothesis_id for x in first]
    assert scan.metadata["watch_universe_fingerprint"] == "a" * 64


def test_policy_change_changes_integration_run_id():
    base = integration_run_id(universe(), ScannerResearchIntegrationPolicy())
    changed = integration_run_id(
        universe(), ScannerResearchIntegrationPolicy(minimum_score_confidence=0.5)
    )
    assert base != changed


def test_monitoring_research_never_materializes_opportunity():
    hypothesis = build_research_hypotheses(universe(), created_at=NOW)[2]
    research_value = research(hypothesis)
    decision, opportunity, link = materialize_trade_opportunity(
        hypothesis, research_value, score(hypothesis, research_value),
        portfolio_snapshot_id="snap-001", created_at=NOW,
    )
    assert decision.reason is HypothesisOutcomeReason.MONITOR_ONLY
    assert opportunity is None
    assert link is None


def test_long_and_short_materialization_preserve_direction_and_provenance(monkeypatch):
    # Exercises the SHORT path as it will run once AI-8C.3-R2.2 re-enables it.
    import app.scanner.research_integration as integration

    monkeypatch.setattr(integration, "SHORT_MATERIALIZATION_ENABLED", True)
    hypotheses = build_research_hypotheses(universe(), created_at=NOW)[:2]
    results = []
    for hypothesis in hypotheses:
        research_value = research(hypothesis)
        results.append(materialize_trade_opportunity(
            hypothesis, research_value, score(hypothesis, research_value),
            portfolio_snapshot_id="snap-001", created_at=NOW,
        ))
    assert results[0][1].direction is Direction.LONG
    assert results[1][1].direction is Direction.SHORT
    assert results[0][2].watch_universe_fingerprint == "a" * 64
    assert results[0][2].research_id.startswith("research-")


def test_gate_is_fail_closed_for_incomplete_research_and_weak_score():
    hypothesis = build_research_hypotheses(universe(), created_at=NOW)[0]
    partial = research(
        hypothesis,
        research_status=ResearchStatus.PARTIAL,
        requires_additional_research=True,
    )
    decision = opportunity_materialization_decision(
        hypothesis, partial, score(hypothesis, partial)
    )
    assert decision.reason is HypothesisOutcomeReason.RESEARCH_NOT_COMPLETE
    complete = research(hypothesis)
    weak = opportunity_materialization_decision(
        hypothesis, complete,
        score(hypothesis, complete, confidence_adjusted_score=59.999),
    )
    assert weak.reason is HypothesisOutcomeReason.SCORE_BELOW_THRESHOLD


def test_build_opportunity_score_maps_frozen_scoring_result():
    hypothesis = build_research_hypotheses(universe(), created_at=NOW)[0]
    research_value = research(hypothesis)
    component = lambda value: SimpleNamespace(
        score=value, rationale="grounded", supporting_evidence_ids=["e1"]
    )
    components = SimpleNamespace(
        thesis=component(75), catalyst=component(70), fundamental=component(72),
        technical=component(68), expectations=component(65),
        positive_factors=["Growth"], negative_factors=["Valuation"],
        uncertainty_factors=["Execution"],
    )
    calculation = SimpleNamespace(
        raw_score=70.0, score_confidence=0.60,
        confidence_adjusted_score=62.0,
    )
    result = SimpleNamespace(
        components=components, calculation=calculation, diagnostics={"ok": True},
    )
    value = build_opportunity_score(
        hypothesis, research_value, result,
        evidence_coverage_score=0.8, created_at=NOW,
    )
    assert value.scoring_status is OpportunityScoringStatus.SCORED
    assert value.confidence_adjusted_score == 62
    assert value.positive_factors == ["Growth"]


def test_watch_universe_report_loader_preserves_exact_boundary(tmp_path):
    path = tmp_path / "watch.json"
    path.write_text(
        """{
          "audit_id": "E2E-S2.2E",
          "schema": "scanner-research-watch-universe-v1",
          "universe": {
            "run_id": "watch-001",
            "fingerprint": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
            "as_of": "2026-09-24T12:00:00+00:00",
            "portfolio_snapshot_id": "snap-001",
            "members": [{
              "subject_key": {"namespace": "YAHOO", "value": "A2A.MI"},
              "provenances": ["NEW_CANDIDATE"],
              "candidate_keys": [{"exchange": "BIT", "symbol": "A2A"}],
              "position_refs": [],
              "yahoo_symbols": ["A2A.MI"],
              "isins": ["IT0001233417"],
              "history_routes": ["STANDARD"],
              "readiness": "READY",
              "diagnostics": []
            }]
          }
        }""",
        encoding="utf-8",
    )
    value = load_watch_universe_report(path)
    assert value.run_id == "watch-001"
    assert value.members[0].candidate_keys == ("BIT:A2A",)
    assert len(value.source_report_fingerprint) == 64
