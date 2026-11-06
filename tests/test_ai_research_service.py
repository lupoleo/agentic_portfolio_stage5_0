from datetime import datetime, timezone

import pytest

from app.ai.models import AIResponse, DataSensitivity, ReasoningMode
from app.ai.provider import AIModelProvider
from app.ai.research_models import EvidenceQuality, ExpectationsAssessment, ResearchStatus
from app.ai.research_service import ResearchEvidence, ResearchModelOutput, ResearchService
from app.ai.scan_models import CandidateAction, CandidateOrigin, ScanCandidate, SignalType

NOW = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)


class FakeProvider(AIModelProvider):
    @property
    def provider_name(self):
        return "FAKE"

    @property
    def model_name(self):
        return "fake-model"

    def infer(self, ai_request):
        self.last_request = ai_request
        payload = ResearchModelOutput(
            research_status=ResearchStatus.COMPLETE,
            market_context="Sector context is constructive.",
            fundamental_context="Evidence supports growth and valuation context.",
            technical_context="No technical evidence supplied.",
            event_context="The supplied news reports improved revenue growth and expanded valuation.",
            catalyst_assessment="Improved revenue growth is the supported company development.",
            expectations_assessment=ExpectationsAssessment.PARTIALLY_PRICED_IN,
            bull_case="Execution could exceed expectations.",
            bear_case="Valuation may compress.",
            key_risks=["Valuation"],
            contradictory_evidence=["Revenue growth improved while valuation expanded."],
            unknowns=[],
            evidence_quality=EvidenceQuality.HIGH,
            research_confidence=0.83,
            requires_additional_research=False,
        ).model_dump(mode="json")
        return AIResponse(
            provider=self.provider_name,
            model=self.model_name,
            content="",
            structured_output=payload,
            latency_ms=12.5,
            usage={"prompt_tokens": 100, "completion_tokens": 80},
        )


def candidate():
    return ScanCandidate(
        candidate_id="CAND-001", scan_id="SCAN-001", created_at=NOW,
        ticker="path", origin=CandidateOrigin.EXTERNAL,
        action=CandidateAction.NEW_LONG, signal_type=SignalType.FUNDAMENTAL,
        raw_score=70, scanner_confidence=0.75,
        thesis_summary="Potential software opportunity.",
        portfolio_snapshot_id="SNAP-001", risk_state_id="RISK-001",
        requires_research=True,
    )


def evidence():
    return [ResearchEvidence(
        evidence_id="EVID-001", source_type="NEWS",
        text="Revenue growth improved, but valuation expanded.",
        published_at=NOW,
    )]


def test_research_builds_typed_result():
    result = ResearchService(FakeProvider()).research(candidate(), evidence(), now=NOW)
    assert result.research.ticker == "PATH"
    assert result.research.research_confidence == 0.83
    assert result.research.inference_ids == [result.inference.inference_id]
    assert result.repair_attempted is False
    assert result.coverage_report["is_valid"] is True


def test_request_is_research_json_typed():
    provider = FakeProvider()
    ResearchService(provider).research(candidate(), evidence(), now=NOW)
    request = provider.last_request
    assert request.response_format.value == "JSON"
    assert request.output_schema is ResearchModelOutput
    assert request.reasoning_mode == ReasoningMode.REASONING
    assert request.sensitivity == DataSensitivity.PUBLIC


def test_prompt_contains_candidate_evidence_and_decision_boundary():
    provider = FakeProvider()
    ResearchService(provider).research(candidate(), evidence(), now=NOW)
    prompt = provider.last_request.prompt
    assert "PATH" in prompt
    assert "EVID-001" in prompt
    assert "Revenue growth improved" in prompt
    assert "Do not recommend LONG, SHORT, BUY, SELL" in prompt
    assert "position size, entry, stop or" in prompt


def test_no_evidence_is_rejected():
    with pytest.raises(ValueError, match="evidence"):
        ResearchService(FakeProvider()).research(candidate(), [])


def test_snapshot_mismatch_rejected():
    with pytest.raises(ValueError, match="portfolio_snapshot_id"):
        ResearchService(FakeProvider()).research(
            candidate(), evidence(), portfolio_snapshot_id="SNAP-X"
        )


def test_risk_state_mismatch_rejected():
    with pytest.raises(ValueError, match="risk_state_id"):
        ResearchService(FakeProvider()).research(
            candidate(), evidence(), risk_state_id="RISK-X"
        )


def test_inference_provenance_is_linked():
    result = ResearchService(FakeProvider()).research(candidate(), evidence(), now=NOW)
    assert result.inference.evidence_ids == ["EVID-001"]
    assert result.inference.portfolio_snapshot_id == "SNAP-001"
    assert result.inference.risk_state_id == "RISK-001"
    assert result.inference.prompt_version == "opportunity-research-v1.5-context-gaps-confidence"
    assert len(result.inferences) == 1


def test_research_metadata_records_model_and_prompt_version():
    result = ResearchService(FakeProvider()).research(candidate(), evidence(), now=NOW)
    assert result.research.metadata["provider"] == "FAKE"
    assert result.research.metadata["model"] == "fake-model"
    assert result.research.metadata["prompt_version"] == "opportunity-research-v1.5-context-gaps-confidence"
    assert result.research.metadata["repair_attempted"] is False
    assert result.research.metadata["coverage_valid"] is True


# AI-8C.3b.6 — Research Context Presence Stabilization

class _Dimension:
    def __init__(self, value):
        self.value = value


class _SemanticAssessment:
    def __init__(self, evidence_id, *dimensions):
        self.evidence_id = evidence_id
        self.dimensions = [_Dimension(value) for value in dimensions]


def test_required_context_fields_are_deterministic_from_evidence_semantics():
    assessments = [
        _SemanticAssessment("E1", "FUNDAMENTAL"),
        _SemanticAssessment("E2", "CATALYST_EVENT"),
        _SemanticAssessment("E3", "PRICE_TECHNICAL"),
    ]
    assert ResearchService._required_context_fields(assessments) == {
        "fundamental_context",
        "event_context",
        "catalyst_assessment",
        "technical_context",
    }


def test_required_context_fields_empty_without_semantic_assessments():
    assert ResearchService._required_context_fields([]) == set()


def test_missing_required_context_fields_detects_only_null_targets():
    output = ResearchModelOutput(
        research_status=ResearchStatus.PARTIAL,
        market_context=None,
        fundamental_context="Supported fundamentals.",
        technical_context=None,
        event_context="Supported event.",
        catalyst_assessment=None,
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Bull.",
        bear_case="Bear.",
        key_risks=[],
        contradictory_evidence=[],
        unknowns=[],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=.7,
        requires_additional_research=True,
    )
    assert ResearchService._missing_required_context_fields(
        output,
        {
            "fundamental_context",
            "technical_context",
            "event_context",
            "catalyst_assessment",
        },
    ) == {"technical_context", "catalyst_assessment"}


def test_context_presence_repair_instruction_is_field_scoped():
    text = ResearchService._context_presence_repair_instructions(
        {"fundamental_context", "catalyst_assessment", "research_status"}
    )
    assert "fundamental_context" in text
    assert "catalyst_assessment" in text
    assert "research_status" not in text
    assert "rather than inventing content" in text


def test_research_result_exposes_presence_and_latency_diagnostics():
    result = ResearchService(FakeProvider()).research(
        candidate(), evidence(), now=NOW
    )
    diagnostics = result.diagnostics
    assert diagnostics["required_context_fields"] == []
    assert diagnostics["initial_missing_required_contexts"] == []
    assert diagnostics["final_missing_required_contexts"] == []
    assert diagnostics["context_presence_repair_attempted"] is False
    assert diagnostics["semantic_tagging_wall_ms"] is None
    assert diagnostics["evidence_quality_wall_ms"] is None
    assert diagnostics["initial_research_latency_ms"] == pytest.approx(12.5)
    assert diagnostics["repair_research_latency_ms"] is None
    assert diagnostics["research_provider_latency_total_ms"] == pytest.approx(12.5)
    assert diagnostics["research_inference_count"] == 1
    assert diagnostics["evidence_semantic_dimensions"] == {}


def test_research_metadata_mirrors_latency_diagnostics():
    result = ResearchService(FakeProvider()).research(
        candidate(), evidence(), now=NOW
    )
    metadata = result.research.metadata
    assert metadata["initial_research_latency_ms"] == pytest.approx(12.5)
    assert metadata["repair_research_latency_ms"] is None
    assert metadata["research_provider_latency_total_ms"] == pytest.approx(12.5)


# AI-8C.3d.2a — Research List Canonicalization Boundary

def test_research_list_canonicalization_strips_and_drops_blank_items():
    output = ResearchModelOutput(
        research_status=ResearchStatus.PARTIAL,
        market_context="Market.",
        fundamental_context="Fundamental.",
        technical_context="Technical.",
        event_context="Event.",
        catalyst_assessment="Catalyst.",
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case="Bull.",
        bear_case="Bear.",
        key_risks=[" Risk A ", "", "   ", "Risk B"],
        contradictory_evidence=[" Contradiction "],
        unknowns=["Unknown", "  "],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=.7,
        requires_additional_research=True,
    )

    canonical, report = ResearchService._canonicalize_research_list_fields(
        output
    )

    assert canonical.key_risks == ["Risk A", "Risk B"]
    assert canonical.contradictory_evidence == ["Contradiction"]
    assert canonical.unknowns == ["Unknown"]
    assert report["total_removed_blank_items"] == 3
    assert report["total_normalized_items"] == 2


def test_research_list_canonicalization_preserves_order_and_duplicates():
    output = ResearchModelOutput(
        research_status=ResearchStatus.PARTIAL,
        market_context=None,
        fundamental_context=None,
        technical_context=None,
        event_context=None,
        catalyst_assessment=None,
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
        bull_case=None,
        bear_case=None,
        key_risks=["Same", "Same", "Other"],
        contradictory_evidence=[],
        unknowns=[],
        evidence_quality=EvidenceQuality.MEDIUM,
        research_confidence=.5,
        requires_additional_research=True,
    )

    canonical, _ = ResearchService._canonicalize_research_list_fields(output)
    assert canonical.key_risks == ["Same", "Same", "Other"]


class BlankListProvider(FakeProvider):
    def infer(self, ai_request):
        self.last_request = ai_request
        payload = ResearchModelOutput(
            research_status=ResearchStatus.COMPLETE,
            market_context="Sector context is constructive.",
            fundamental_context="Evidence supports growth and valuation context.",
            technical_context="No technical evidence supplied.",
            event_context="The supplied news reports improved revenue growth and expanded valuation.",
            catalyst_assessment="Improved revenue growth is the supported company development.",
            expectations_assessment=ExpectationsAssessment.PARTIALLY_PRICED_IN,
            bull_case="Execution could exceed expectations.",
            bear_case="Valuation may compress.",
            key_risks=["Valuation", "", "   "],
            contradictory_evidence=[" Revenue improved while valuation expanded. "],
            unknowns=[""],
            evidence_quality=EvidenceQuality.HIGH,
            research_confidence=0.83,
            requires_additional_research=False,
        ).model_dump(mode="json")
        return AIResponse(
            provider=self.provider_name,
            model=self.model_name,
            content="",
            structured_output=payload,
            latency_ms=12.5,
            usage={"prompt_tokens": 100, "completion_tokens": 80},
        )


def test_blank_research_list_members_do_not_crash_opportunity_research():
    result = ResearchService(BlankListProvider()).research(
        candidate(), evidence(), now=NOW
    )

    assert result.research.key_risks == ["Valuation"]
    assert result.research.contradictory_evidence == [
        "Revenue improved while valuation expanded."
    ]
    assert result.research.unknowns == []
    diag = result.diagnostics["research_list_canonicalization"]
    assert diag["total_removed_blank_items"] == 3


def test_list_canonicalization_metadata_is_persisted():
    result = ResearchService(BlankListProvider()).research(
        candidate(), evidence(), now=NOW
    )
    metadata = result.research.metadata["research_list_canonicalization"]
    assert metadata["initial"]["total_removed_blank_items"] == 3
    assert metadata["total_removed_blank_items"] == 3
