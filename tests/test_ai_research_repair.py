from datetime import datetime, timezone

from app.ai.models import AIResponse
from app.ai.provider import AIModelProvider
from app.ai.research_models import EvidenceQuality, ExpectationsAssessment, ResearchStatus
from app.ai.research_repair import ResearchRepairService
from app.ai.research_service import ResearchEvidence, ResearchModelOutput

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)

class FakeProvider(AIModelProvider):
    def __init__(self, repaired):
        self.repaired = repaired
        self.calls = 0
        self.last_request = None
    @property
    def provider_name(self): return "FAKE"
    @property
    def model_name(self): return "fake"
    def infer(self, request):
        self.calls += 1
        self.last_request = request
        return AIResponse(provider="FAKE", model="fake", content="",
                          structured_output=self.repaired.model_dump(mode="json"),
                          latency_ms=1, usage={})

def evidence():
    return [
        ResearchEvidence(evidence_id="M1", source_type="MARKET", published_at=NOW,
                         text="20-session return 42%. RSI14 72.5. SMA20 and SMA50 supplied."),
        ResearchEvidence(evidence_id="N1", source_type="NEWS", published_at=NOW,
                         text="UiPath announced a customer deployment."),
    ]

def model(**overrides):
    data = dict(
        research_status=ResearchStatus.PARTIAL,
        market_context=None,
        fundamental_context=None,
        technical_context="Momentum strong; RSI elevated.",
        event_context="Customer deployment announced.",
        catalyst_assessment="Adoption could support expectations.",
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Adoption plus momentum.",
        bear_case="Extension risk.",
        key_risks=["Extension"],
        contradictory_evidence=[],
        unknowns=["Valuation"],
        evidence_quality=EvidenceQuality.MEDIUM,
        research_confidence=0.65,
        requires_additional_research=True,
    )
    data.update(overrides)
    return ResearchModelOutput(**data)

def test_no_repair_when_valid():
    provider = FakeProvider(model())
    r = ResearchRepairService(provider).validate_or_repair(
        ticker="PATH", candidate_context="scan hypothesis", evidence=evidence(), output=model())
    assert not r.repair_attempted
    assert provider.calls == 0
    assert r.final_report.is_valid

def test_one_repair_pass_when_invalid():
    provider = FakeProvider(model())
    broken = model(technical_context=None, event_context=None)
    r = ResearchRepairService(provider).validate_or_repair(
        ticker="PATH", candidate_context="scan hypothesis", evidence=evidence(), output=broken)
    assert r.repair_attempted
    assert provider.calls == 1
    assert r.final_report.is_valid
    assert provider.last_request.metadata["repair"] is True

def test_repair_prompt_contains_same_evidence_and_previous_output():
    provider = FakeProvider(model())
    ResearchRepairService(provider).validate_or_repair(
        ticker="PATH", candidate_context="scan hypothesis",
        evidence=evidence(), output=model(technical_context=None))
    prompt = provider.last_request.prompt
    assert "M1" in prompt and "N1" in prompt
    assert "20-session return 42%" in prompt
    assert "PREVIOUS STRUCTURED OUTPUT" in prompt

def test_second_invalid_result_fails_closed_in_report_without_second_call():
    provider = FakeProvider(model(technical_context=None))
    r = ResearchRepairService(provider).validate_or_repair(
        ticker="PATH", candidate_context="scan hypothesis",
        evidence=evidence(), output=model(technical_context=None))
    assert provider.calls == 1
    assert not r.final_report.is_valid
