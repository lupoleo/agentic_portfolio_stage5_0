from datetime import datetime, timezone

from app.ai.opportunity_score_models import OpportunityScore, OpportunityScoringStatus
from app.ai.research_models import EvidenceQuality
from app.scanner.research_integration_contracts import (
    HypothesisOutcomeReason,
    HypothesisOutcomeStatus,
    IntegrationRunStatus,
    PersistedEvidenceBundle,
    ResearchHypothesisKind,
    ResearchHypothesisOutcome,
    ScannerResearchIntegrationPolicy,
    ScannerResearchRun,
    TradeOpportunityProvenanceLink,
)
from app.scanner.research_integration_store import ScannerResearchIntegrationStore


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def test_all_integration_records_roundtrip_and_upsert(tmp_path):
    store = ScannerResearchIntegrationStore(tmp_path / "state.db")
    run = ScannerResearchRun(
        run_id="run-1", watch_universe_run_id="watch-1",
        watch_universe_fingerprint="f" * 64, portfolio_snapshot_id="snap-1",
        policy=ScannerResearchIntegrationPolicy(), as_of=NOW, started_at=NOW,
        completed_at=NOW, status=IntegrationRunStatus.COMPLETED,
        hypothesis_ids=("cand-1",), completed_hypothesis_ids=("cand-1",),
        opportunity_ids=("opp-1",), fingerprint="r" * 64,
    )
    outcome = ResearchHypothesisOutcome(
        integration_run_id="run-1", hypothesis_id="cand-1",
        subject_namespace="YAHOO", subject_value="A2A.MI",
        kind=ResearchHypothesisKind.NEW_LONG,
        status=HypothesisOutcomeStatus.OPPORTUNITY_CREATED,
        reason=HypothesisOutcomeReason.OPPORTUNITY_CREATED,
        updated_at=NOW, evidence_bundle_id="bundle-1", research_id="research-1",
        opportunity_score_id="score-1", opportunity_id="opp-1",
    )
    bundle = PersistedEvidenceBundle(
        bundle_id="bundle-1", integration_run_id="run-1",
        hypothesis_id="cand-1", ticker="A2A.MI", created_at=NOW,
        evidence_ids=("e1",), source_ids=("s1",),
        provider_statuses=(("YAHOO_MARKET", "SUCCESS"),),
        items=({"evidence_id": "e1", "source_id": "s1"},),
        fingerprint="b" * 64,
    )
    score = OpportunityScore(
        opportunity_score_id="score-1", candidate_id="cand-1",
        research_id="research-1", scan_id="scan-1", created_at=NOW,
        ticker="A2A.MI", scoring_status=OpportunityScoringStatus.SCORED,
        thesis_score=70, catalyst_score=70, fundamental_score=70,
        technical_score=70, expectations_score=70, raw_score=70,
        score_confidence=.6, confidence_adjusted_score=62,
        evidence_quality=EvidenceQuality.HIGH, evidence_coverage_score=.8,
        research_confidence=.8, requires_additional_research=False,
    )
    link = TradeOpportunityProvenanceLink(
        opportunity_id="opp-1", integration_run_id="run-1",
        watch_universe_run_id="watch-1", watch_universe_fingerprint="f" * 64,
        subject_namespace="YAHOO", subject_value="A2A.MI",
        hypothesis_id="cand-1", candidate_id="cand-1",
        research_id="research-1", opportunity_score_id="score-1",
        portfolio_snapshot_id="snap-1", evidence_ids=("e1",), inference_ids=(),
        policy_id="policy", policy_version="1",
        invalidation_conditions=("Demand weakens",), created_at=NOW,
    )
    store.save_run(run)
    store.save_outcome(outcome)
    store.save_evidence_bundle(bundle)
    store.save_opportunity_score(score)
    store.save_provenance_link(link)
    store.save_outcome(outcome)
    assert store.get_run("run-1") == run
    assert store.get_outcome("run-1", "cand-1") == outcome
    assert store.list_outcomes("run-1") == [outcome]
    assert store.get_evidence_bundle("bundle-1") == bundle
    assert store.get_opportunity_score("score-1") == score
    assert store.get_provenance_link("opp-1") == link
