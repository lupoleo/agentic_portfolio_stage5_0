from app.ai.canonical_technical import CanonicalTechnicalInput
from app.ai.evidence_provider import EvidenceKind
from app.scanner.research_integration_service import ScannerResearchIntegrationService
from app.scanner.research_integration_store import ScannerResearchIntegrationStore
from tests.test_scanner_research_integration import NOW, universe
from tests.test_scanner_research_integration_service import (
    EvidenceProviderStub,
    MemoryStage3,
    ResearchServiceStub,
    ScoringServiceStub,
)


def technical(ticker):
    return CanonicalTechnicalInput(
        ticker=ticker,
        current_price=100.0,
        return_1d_pct=1.0,
        return_5d_pct=2.0,
        return_20d_pct=3.0,
        sma20=98.0,
        sma50=95.0,
        close_vs_sma20_pct=2.04,
        close_vs_sma50_pct=5.26,
        rsi14=57.0,
        rvol=1.2,
        trend="BULLISH",
    )


class CapturingScoringService(ScoringServiceStub):
    def __init__(self):
        self.technical_inputs = []

    def score(self, research, **kwargs):
        self.technical_inputs.append(kwargs["canonical_technical_input"])
        return super().score(research, **kwargs)


class FailingOptionalProvider:
    provider_name = "OPTIONAL_FAILURE"

    def fetch(self, request):
        raise RuntimeError("controlled provider failure")


def service(tmp_path, *, analyst_provider):
    store = ScannerResearchIntegrationStore(tmp_path / "state.db")
    scoring = CapturingScoringService()
    value = ScannerResearchIntegrationService(
        stage3_store=MemoryStage3(),
        integration_store=store,
        research_service=ResearchServiceStub(),
        scoring_service=scoring,
        market_provider=EvidenceProviderStub("YAHOO_MARKET", EvidenceKind.MARKET),
        news_provider=EvidenceProviderStub("YAHOO_NEWS", EvidenceKind.NEWS),
        fundamental_provider=EvidenceProviderStub(
            "YAHOO_FUNDAMENTAL", EvidenceKind.FUNDAMENTAL,
        ),
        analyst_provider=analyst_provider,
        canonical_technical_loader=technical,
    )
    return value, store, scoring


def test_bridge_persists_five_evidence_kinds_and_reuses_technical_input(tmp_path):
    value, store, scoring = service(
        tmp_path,
        analyst_provider=EvidenceProviderStub(
            "YAHOO_ANALYST", EvidenceKind.ANALYST,
        ),
    )
    run = value.run(universe(), now=NOW, max_hypotheses=1)
    outcome = store.list_outcomes(run.run_id)[0]
    bundle = store.get_evidence_bundle(outcome.evidence_bundle_id)
    kinds = {item["kind"] for item in bundle.items}
    assert kinds == {"MARKET", "NEWS", "FUNDAMENTAL", "ANALYST", "TECHNICAL"}
    assert dict(bundle.provider_statuses) == {
        "CANONICAL_TECHNICAL": "SUCCESS",
        "YAHOO_ANALYST": "SUCCESS",
        "YAHOO_FUNDAMENTAL": "SUCCESS",
        "YAHOO_MARKET": "SUCCESS",
        "YAHOO_NEWS": "SUCCESS",
    }
    assert len(scoring.technical_inputs) == 1
    assert scoring.technical_inputs[0].ticker == "A2A.MI"


def test_optional_provider_failure_is_persisted_and_does_not_abort(tmp_path):
    value, store, _ = service(
        tmp_path,
        analyst_provider=FailingOptionalProvider(),
    )
    run = value.run(universe(), now=NOW, max_hypotheses=1)
    outcome = store.list_outcomes(run.run_id)[0]
    bundle = store.get_evidence_bundle(outcome.evidence_bundle_id)
    assert dict(bundle.provider_statuses)["OPTIONAL_FAILURE"] == "PARTIAL"
    assert any("controlled provider failure" in value for value in bundle.warnings)
    assert run.opportunity_ids


def test_cache_only_bridge_makes_zero_provider_or_technical_calls(tmp_path):
    calls = {"technical": 0}

    def counted_technical(ticker):
        calls["technical"] += 1
        return technical(ticker)

    providers = [
        EvidenceProviderStub("YAHOO_MARKET", EvidenceKind.MARKET),
        EvidenceProviderStub("YAHOO_NEWS", EvidenceKind.NEWS),
        EvidenceProviderStub("YAHOO_FUNDAMENTAL", EvidenceKind.FUNDAMENTAL),
        EvidenceProviderStub("YAHOO_ANALYST", EvidenceKind.ANALYST),
    ]
    value = ScannerResearchIntegrationService(
        stage3_store=MemoryStage3(),
        integration_store=ScannerResearchIntegrationStore(tmp_path / "state.db"),
        research_service=ResearchServiceStub(),
        scoring_service=CapturingScoringService(),
        market_provider=providers[0],
        news_provider=providers[1],
        fundamental_provider=providers[2],
        analyst_provider=providers[3],
        canonical_technical_loader=counted_technical,
    )
    run = value.run(
        universe(), now=NOW, max_hypotheses=1, cache_only=True,
    )
    assert not run.opportunity_ids
    assert [provider.calls for provider in providers] == [0, 0, 0, 0]
    assert calls["technical"] == 0
