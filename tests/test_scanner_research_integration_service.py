from datetime import datetime, timezone
from types import SimpleNamespace

from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceItem,
    EvidenceKind,
    EvidenceSource,
)
from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    OpportunityResearch,
    ResearchStatus,
)
from app.ai.research_service import ResearchEvidence
from app.scanner.research_integration_contracts import IntegrationRunStatus
from app.scanner.research_integration_service import ScannerResearchIntegrationService
from app.scanner.research_integration_store import ScannerResearchIntegrationStore
from tests.test_scanner_research_integration import NOW, universe


class MemoryStage3:
    def __init__(self):
        self.scans = []
        self.candidates = []
        self.research = []
        self.inferences = []
        self.opportunities = []

    def save_market_scan(self, value): self.scans.append(value)
    def save_scan_candidate(self, value): self.candidates.append(value)
    def save_opportunity_research(self, value): self.research.append(value)
    def save_ai_inference(self, value): self.inferences.append(value)
    def save_trade_opportunity(self, value): self.opportunities.append(value)


class EvidenceProviderStub:
    def __init__(self, provider, kind):
        self.provider_name = provider
        self.kind = kind
        self.calls = 0

    def fetch(self, request):
        self.calls += 1
        evidence_id = f"{self.provider_name}-{request.ticker}"
        evidence = ResearchEvidence(
            evidence_id=evidence_id,
            source_type=self.kind.value,
            text=f"Grounded {self.kind.value.lower()} evidence for {request.ticker}.",
            published_at=NOW,
        )
        item = EvidenceItem(
            evidence=evidence,
            source=EvidenceSource(
                source_id=f"source-{evidence_id}", provider=self.provider_name,
                source_type=self.kind.value, source_name=self.provider_name,
                source_url=f"https://example.test/{evidence_id}",
                retrieved_at=NOW, published_at=NOW,
            ),
            kind=self.kind,
            ticker=request.ticker,
        )
        return EvidenceFetchResult(
            provider=self.provider_name, ticker=request.ticker,
            status=EvidenceFetchStatus.SUCCESS, items=[item], fetched_at=NOW,
        )


class ResearchServiceStub:
    def research(self, candidate, evidence, **kwargs):
        value = OpportunityResearch(
            research_id=f"research-{candidate.candidate_id}",
            candidate_id=candidate.candidate_id, scan_id=candidate.scan_id,
            created_at=NOW, ticker=candidate.ticker,
            portfolio_snapshot_id=candidate.portfolio_snapshot_id,
            research_status=ResearchStatus.COMPLETE,
            market_context="Market evidence is constructive.",
            fundamental_context="Fundamental evidence is constructive.",
            technical_context="Technical evidence is constructive.",
            event_context="No immediate event dependency.",
            catalyst_assessment="Execution can re-rate the shares.",
            expectations_assessment=ExpectationsAssessment.PARTIALLY_PRICED_IN,
            bull_case="Execution can produce upside.",
            bear_case="Weak execution can produce downside.",
            key_risks=["Execution risk"], contradictory_evidence=[], unknowns=[],
            evidence_quality=EvidenceQuality.HIGH, research_confidence=.8,
            evidence_ids=[x.evidence_id for x in evidence], inference_ids=["ai-1"],
            requires_additional_research=False,
        )
        inference = SimpleNamespace(inference_id="ai-1")
        return SimpleNamespace(
            research=value, inference=inference, inferences=[inference],
            evidence_quality_report={"coverage_score": .8},
        )


class ScoringServiceStub:
    def score(self, research, **kwargs):
        item = lambda value: SimpleNamespace(
            score=value, rationale="grounded",
            supporting_evidence_ids=[research.evidence_ids[0]],
        )
        return SimpleNamespace(
            components=SimpleNamespace(
                thesis=item(75), catalyst=item(72), fundamental=item(70),
                technical=item(68), expectations=item(66),
                positive_factors=["Growth"], negative_factors=["Valuation"],
                uncertainty_factors=["Execution"],
            ),
            calculation=SimpleNamespace(
                raw_score=70, score_confidence=.6,
                confidence_adjusted_score=62,
            ),
            diagnostics=_stub_diagnostics(kwargs.get("direction")),
        )


def _stub_diagnostics(direction):
    diagnostics = {"stub": True}
    if direction is not None:
        diagnostics["direction"] = {
            "direction": direction.value,
            "direction_source": "HYPOTHESIS",
            "policy_version": "ai-8c3-directional-scoring-v3",
            "company_assessment": {"sources": {
                "fundamental": "SHARED_COMPANY_ASSESSMENT",
                "expectations": "SHARED_COMPANY_ASSESSMENT",
            }},
        }
    return diagnostics


def service(tmp_path):
    stage3 = MemoryStage3()
    market = EvidenceProviderStub("YAHOO_MARKET", EvidenceKind.MARKET)
    news = EvidenceProviderStub("YAHOO_NEWS", EvidenceKind.NEWS)
    value = ScannerResearchIntegrationService(
        stage3_store=stage3,
        integration_store=ScannerResearchIntegrationStore(tmp_path / "state.db"),
        research_service=ResearchServiceStub(),
        scoring_service=ScoringServiceStub(),
        market_provider=market,
        news_provider=news,
    )
    return value, stage3, market, news


def test_directional_pipeline_persists_two_opportunities_and_provenance(tmp_path, monkeypatch):
    # Exercises the SHORT path as it will run once AI-8C.3-R2.2 re-enables it.
    import app.scanner.research_integration as integration

    monkeypatch.setattr(integration, "SHORT_MATERIALIZATION_ENABLED", True)
    value, stage3, market, news = service(tmp_path)
    run = value.run(universe(), now=NOW)
    assert run.status is IntegrationRunStatus.COMPLETED
    assert len(run.opportunity_ids) == 2
    assert len(stage3.opportunities) == 2
    assert market.calls == 3 and news.calls == 3
    for opportunity_id in run.opportunity_ids:
        link = value.integration_store.get_provenance_link(opportunity_id)
        assert link is not None
        assert link.watch_universe_fingerprint == "a" * 64


def test_resume_skips_completed_hypotheses_and_provider_calls(tmp_path):
    value, _, market, news = service(tmp_path)
    first = value.run(universe(), now=NOW)
    first_calls = (market.calls, news.calls)
    second = value.run(universe(), now=NOW)
    assert second.opportunity_ids == first.opportunity_ids
    assert (market.calls, news.calls) == first_calls


def test_cache_only_miss_is_explicit_and_makes_no_network_calls(tmp_path):
    value, stage3, market, news = service(tmp_path)
    run = value.run(universe(), now=NOW, max_hypotheses=1, cache_only=True)
    assert run.status is IntegrationRunStatus.PARTIAL
    assert not run.opportunity_ids
    assert market.calls == 0 and news.calls == 0
    outcomes = value.integration_store.list_outcomes(run.run_id)
    pending_ids = {
        outcome.hypothesis_id
        for outcome in outcomes
        if outcome.status.value == "PENDING"
    }
    assert pending_ids
    assert pending_ids.isdisjoint(
        run.completed_hypothesis_ids
    )
    assert any(x.diagnostics == ("CACHE_ONLY_MISS",) for x in outcomes)
    assert not stage3.opportunities


def test_short_materialization_is_enabled_by_default():
    import app.scanner.research_integration as integration

    assert integration.SHORT_MATERIALIZATION_ENABLED is True


def test_short_materialization_can_be_suspended(tmp_path, monkeypatch):
    import app.scanner.research_integration as integration

    monkeypatch.setattr(integration, "SHORT_MATERIALIZATION_ENABLED", False)
    value, stage3, market, news = service(tmp_path)
    run = value.run(universe(), now=NOW)
    assert run.status is IntegrationRunStatus.COMPLETED
    assert len(run.opportunity_ids) == 1
    outcomes = value.integration_store.list_outcomes(run.run_id)
    by_kind = {outcome.kind.value: outcome for outcome in outcomes if outcome.kind.value in ("NEW_LONG", "NEW_SHORT")}
    assert by_kind["NEW_LONG"].reason.value == "OPPORTUNITY_CREATED"
    assert by_kind["NEW_SHORT"].reason.value == "SHORT_MATERIALIZATION_SUSPENDED"
    assert by_kind["NEW_SHORT"].opportunity_score_id is not None
