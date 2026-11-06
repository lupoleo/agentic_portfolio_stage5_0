from datetime import datetime, timezone

import pytest

from app.ai.canonical_technical import CanonicalTechnicalInput
from app.ai.models import AIResponse, AITask, ReasoningMode, ResponseFormat
from app.ai.opportunity_scoring_service import OpportunityScoringService
from app.ai.research_models import (
    EvidenceQuality,
    ExpectationsAssessment,
    OpportunityResearch,
    ResearchStatus,
)


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def infer(self, request):
        self.requests.append(request)
        return AIResponse(
            provider="FAKE",
            model="fake-model",
            structured_output=self.payload,
            latency_ms=1.0,
            usage={},
        )


class SequentialFakeProvider:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []

    def infer(self, request):
        self.requests.append(request)
        if not self.payloads:
            raise AssertionError("Unexpected provider call")
        return AIResponse(
            provider="FAKE",
            model="fake-model",
            structured_output=self.payloads.pop(0),
            latency_ms=1.0,
            usage={},
        )


def research(**overrides):
    data = dict(
        research_id="RES-1",
        candidate_id="CAND-1",
        scan_id="SCAN-1",
        created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        ticker="PATH",
        research_status=ResearchStatus.PARTIAL,
        market_context="Enterprise automation market.",
        fundamental_context="Revenue growth and profitability evidence.",
        technical_context="Close above SMA20; RSI 62.",
        event_context="Quarterly earnings approaching.",
        catalyst_assessment="Earnings and product adoption are catalysts.",
        expectations_assessment=ExpectationsAssessment.PARTIALLY_PRICED_IN,
        bull_case="Execution beats expectations.",
        bear_case="Competition and valuation pressure.",
        key_risks=["Competition"],
        contradictory_evidence=[],
        unknowns=["Exact consensus range"],
        evidence_quality=EvidenceQuality.HIGH,
        research_confidence=.70,
        evidence_ids=["E1", "E2", "E3"],
        inference_ids=["INF-1"],
        requires_additional_research=True,
    )
    data.update(overrides)
    return OpportunityResearch(**data)


def output(**overrides):
    data = {
        "thesis": {"score": 75, "rationale": "Coherent thesis.", "supporting_evidence_ids": ["E1"]},
        "catalyst": {"score": 80, "rationale": "Near-term catalyst.", "supporting_evidence_ids": ["E2"]},
        "fundamental": {"score": 65, "rationale": "Fundamental support.", "supporting_evidence_ids": ["E1"]},
        "technical": {"score": 78, "rationale": "Positive technical context.", "supporting_evidence_ids": ["E3"]},
        "expectations": {"score": 60, "rationale": "Partially priced in.", "supporting_evidence_ids": ["E2"]},
        "positive_factors": ["Catalyst"],
        "negative_factors": ["Competition"],
        "uncertainty_factors": ["Consensus range"],
    }
    data.update(overrides)
    return data


def test_service_requests_reasoning_typed_json_and_calculates_deterministically():
    provider = FakeProvider(output())
    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )
    request = provider.requests[0]
    assert request.task == AITask.REASONING
    assert request.reasoning_mode == ReasoningMode.REASONING
    assert request.response_format == ResponseFormat.JSON
    assert request.output_schema is not None
    assert result.calculation.raw_score is not None
    assert result.calculation.score_confidence == pytest.approx(.70 * .80)


def test_model_cannot_return_aggregate_or_portfolio_fields():
    payload = output()
    payload["raw_score"] = 99
    provider = FakeProvider(payload)
    with pytest.raises(Exception):
        OpportunityScoringService(provider).score(
            research(), evidence_coverage_score=.80
        )


def test_unknown_evidence_id_fails_closed():
    payload = output()
    payload["technical"]["supporting_evidence_ids"] = ["FAKE-EVIDENCE"]
    with pytest.raises(ValueError, match="technical cites evidence"):
        OpportunityScoringService(FakeProvider(payload)).score(
            research(), evidence_coverage_score=.80
        )


def test_missing_fundamental_context_forces_null_not_model_score():
    r = research(fundamental_context=None)
    result = OpportunityScoringService(FakeProvider(output())).score(
        r, evidence_coverage_score=.80
    )

    assert result.components.fundamental.score is None
    assert result.components.fundamental.rationale is None
    assert result.components.fundamental.supporting_evidence_ids == []
    assert result.calculation.component_completeness == pytest.approx(.80)


def test_context_absence_canonicalization_preserves_supported_components():
    result = OpportunityScoringService(FakeProvider(output())).score(
        research(fundamental_context=None),
        evidence_coverage_score=.80,
    )

    assert result.components.fundamental.score is None
    assert result.components.thesis.score == 75
    assert result.components.catalyst.score == 65.0
    assert result.components.technical.score == 78
    assert result.components.expectations.score == 60


def test_null_fundamental_is_allowed_and_calculator_renormalizes():
    payload = output(
        fundamental={
            "score": None,
            "rationale": None,
            "supporting_evidence_ids": [],
        }
    )
    r = research(fundamental_context=None)
    result = OpportunityScoringService(FakeProvider(payload)).score(
        r, evidence_coverage_score=.80
    )
    assert result.components.fundamental.score is None
    assert result.calculation.component_completeness == pytest.approx(.80)
    assert result.calculation.raw_score is not None


def test_missing_technical_context_forces_null_not_model_score():
    result = OpportunityScoringService(FakeProvider(output())).score(
        research(technical_context=None), evidence_coverage_score=.80
    )

    assert result.components.technical.score is None
    assert result.components.technical.rationale is None
    assert result.components.technical.supporting_evidence_ids == []


def test_missing_catalyst_and_event_context_forces_null_not_model_score():
    result = OpportunityScoringService(FakeProvider(output())).score(
        research(catalyst_assessment=None, event_context=None),
        evidence_coverage_score=.80,
    )

    assert result.components.catalyst.score is None
    assert result.components.catalyst.rationale is None
    assert result.components.catalyst.supporting_evidence_ids == []


def test_unscored_component_cannot_carry_rationale():
    payload = output(
        fundamental={
            "score": None,
            "rationale": "Still sounds positive",
            "supporting_evidence_ids": [],
        }
    )
    with pytest.raises(Exception):
        OpportunityScoringService(FakeProvider(payload)).score(
            research(), evidence_coverage_score=.80
        )


def test_scored_component_without_evidence_gets_targeted_repair():
    initial = output(
        fundamental={
            "score": 45,
            "rationale": "Mixed fundamentals.",
            "supporting_evidence_ids": [],
        }
    )
    repaired_fundamental = {
        "score": 45,
        "rationale": "Mixed fundamentals supported by revenue evidence.",
        "supporting_evidence_ids": ["E1"],
    }
    provider = SequentialFakeProvider(
        [
            initial,
            {"fundamental": repaired_fundamental},
        ]
    )

    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )

    assert len(provider.requests) == 2
    repair_request = provider.requests[1]
    assert repair_request.reasoning_mode == ReasoningMode.REASONING
    assert repair_request.response_format == ResponseFormat.JSON
    assert repair_request.metadata["repair_reason"] == (
        "SCORED_COMPONENT_WITHOUT_EVIDENCE"
    )
    assert repair_request.metadata["repair_components"] == ["fundamental"]
    assert result.components.fundamental.score == 47.5
    assert result.components.fundamental.supporting_evidence_ids == ["E1"]
    assert result.components.thesis.score == 75
    assert result.components.technical.score == 78


def test_grounding_repair_can_null_unsupported_component():
    initial = output(
        fundamental={
            "score": 45,
            "rationale": "Weakly inferred fundamentals.",
            "supporting_evidence_ids": [],
        }
    )
    provider = SequentialFakeProvider(
        [
            initial,
            {
                "fundamental": {
                    "score": None,
                    "rationale": None,
                    "supporting_evidence_ids": [],
                }
            },
        ]
    )

    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )

    assert result.components.fundamental.score is None
    assert result.calculation.component_completeness == pytest.approx(.80)


def test_grounding_repair_preserves_valid_original_components():
    initial = output(
        fundamental={
            "score": 45,
            "rationale": "Mixed fundamentals.",
            "supporting_evidence_ids": [],
        }
    )
    provider = SequentialFakeProvider(
        [
            initial,
            {
                "fundamental": {
                    "score": 50,
                    "rationale": "Repaired.",
                    "supporting_evidence_ids": ["E2"],
                },
                "technical": {
                    "score": 1,
                    "rationale": "Must be ignored.",
                    "supporting_evidence_ids": ["E1"],
                },
            },
        ]
    )

    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )

    assert result.components.fundamental.score == 50
    assert result.components.technical.score == 78
    assert result.components.technical.rationale == "Positive technical context."


def test_grounding_repair_fails_closed_at_component_level_if_still_unsupported():
    initial = output(
        thesis={
            "score": 75,
            "rationale": "Coherent thesis.",
            "supporting_evidence_ids": [],
        }
    )
    provider = SequentialFakeProvider(
        [
            initial,
            {
                "thesis": {
                    "score": 75,
                    "rationale": "Still unsupported.",
                    "supporting_evidence_ids": [],
                }
            },
        ]
    )

    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )

    assert result.components.thesis.score is None
    assert result.components.thesis.rationale is None
    assert result.components.thesis.supporting_evidence_ids == []
    assert result.components.catalyst.score == 65.0


def test_prompt_explicitly_forbids_portfolio_and_cio_decisions():
    provider = FakeProvider(output())
    OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )
    prompt = provider.requests[0].prompt
    assert "portfolio fit" in prompt
    assert "CIO decision" in prompt
    assert "Do NOT calculate raw_score" in prompt


def test_scorable_mask_is_deterministic_from_canonical_research():
    r = research(
        fundamental_context=None,
        technical_context="Close above SMA20.",
        catalyst_assessment=None,
        event_context=None,
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
    )

    mask = OpportunityScoringService._build_scorable_mask(r)

    assert mask == {
        "thesis": True,
        "catalyst": False,
        "fundamental": False,
        "technical": True,
        "expectations": False,
    }


def test_scorable_mask_requires_evidence_for_every_dimension():
    r = research(evidence_ids=[])

    mask = OpportunityScoringService._build_scorable_mask(r)

    assert mask == {
        "thesis": False,
        "catalyst": False,
        "fundamental": False,
        "technical": False,
        "expectations": False,
    }


def test_unscorable_components_are_canonicalized_to_null_before_validation():
    payload = output(
        fundamental={
            "score": 99,
            "rationale": "Model should not have scored this.",
            "supporting_evidence_ids": ["E1"],
        },
        expectations={
            "score": 80,
            "rationale": "Model should not have scored this either.",
            "supporting_evidence_ids": ["E2"],
        },
    )
    r = research(
        fundamental_context=None,
        expectations_assessment=ExpectationsAssessment.UNKNOWN,
    )

    result = OpportunityScoringService(FakeProvider(payload)).score(
        r,
        evidence_coverage_score=.80,
    )

    assert result.components.fundamental.score is None
    assert result.components.expectations.score is None
    assert result.components.technical.score == 78


def test_prompt_contains_explicit_deterministic_scorable_mask():
    provider = FakeProvider(output())
    OpportunityScoringService(provider).score(
        research(),
        evidence_coverage_score=.80,
    )

    prompt = provider.requests[0].prompt

    assert "DETERMINISTIC SCORABLE MASK" in prompt
    assert "- thesis: SCORABLE" in prompt
    assert "- catalyst: SCORABLE" in prompt
    assert "- fundamental: SCORABLE" in prompt
    assert "- technical: SCORABLE" in prompt
    assert "- expectations: SCORABLE" in prompt


def test_prompt_marks_unknown_expectations_as_must_be_null():
    payload = output(
        expectations={
            "score": None,
            "rationale": None,
            "supporting_evidence_ids": [],
        }
    )
    provider = FakeProvider(payload)
    OpportunityScoringService(provider).score(
        research(expectations_assessment=ExpectationsAssessment.UNKNOWN),
        evidence_coverage_score=.80,
    )

    prompt = provider.requests[0].prompt
    assert "- expectations: MUST_BE_NULL" in prompt


def test_prompt_contains_semantic_component_boundaries_and_score_anchors():
    provider = FakeProvider(output())
    OpportunityScoringService(provider).score(
        research(),
        evidence_coverage_score=.80,
    )

    prompt = provider.requests[0].prompt

    assert "SEMANTIC COMPONENT BOUNDARIES" in prompt
    assert "COMMON SCORE ANCHORS" in prompt
    assert "COMPONENT-SPECIFIC CALIBRATION" in prompt
    assert "Price returns, SMA, RSI, RVOL" in prompt
    assert "are NOT fundamental evidence" in prompt
    assert "Fundamentals, guidance and" in prompt
    assert "are NOT technical evidence" in prompt


def test_prompt_encodes_unambiguous_technical_indicator_interpretation():
    provider = FakeProvider(output())
    OpportunityScoringService(provider).score(
        research(),
        evidence_coverage_score=.80,
    )

    prompt = provider.requests[0].prompt

    assert "CANONICAL TECHNICAL FEATURES" in prompt
    assert "ABOVE_SUPPORTIVE" in prompt
    assert "BELOW_ADVERSE" in prompt
    assert "NEUTRAL RSI" in prompt
    assert "overbought/oversold regimes are risk/context modifiers" in prompt


def test_prompt_encodes_score_grid_and_extreme_score_reservation():
    provider = FakeProvider(output())
    OpportunityScoringService(provider).score(
        research(),
        evidence_coverage_score=.80,
    )

    prompt = provider.requests[0].prompt

    assert "increments of 5 whenever possible" in prompt
    assert "95-100: exceptional support" in prompt
    assert "80+ requires multiple strong fundamental signals" in prompt
    assert "80+ requires broad positive alignment plus meaningful confirming volume/momentum" in prompt


def test_default_prompt_version_marks_canonical_technical_input_v5():
    service = OpportunityScoringService(FakeProvider(output()))
    assert (
        service.prompt_version
        == "opportunity-scoring-v14-directional-company-frame"
    )


def test_canonical_technical_features_parse_known_context_deterministically():
    features = OpportunityScoringService._build_canonical_technical_features(
        "Latest close: 230.36. "
        "1-session return: 0.84%. "
        "5-session return: 5.89%. "
        "20-session return: 2.86%. "
        "SMA20: 220.08. Close vs SMA20: 4.67%. "
        "SMA50: 210.57. Close vs SMA50: 9.40%. "
        "RSI14: 53.68. RVOL: 1.03x."
    )

    assert features["return_1d_pct"] == pytest.approx(0.84)
    assert features["return_5d_pct"] == pytest.approx(5.89)
    assert features["return_20d_pct"] == pytest.approx(2.86)
    assert features["trend_vs_sma20"] == "ABOVE_SUPPORTIVE"
    assert features["trend_vs_sma50"] == "ABOVE_SUPPORTIVE"
    assert features["rsi_regime"] == "NEUTRAL"
    assert features["volume_regime"] == "NORMAL"
    assert features["technical_bias"] == "POSITIVE"


@pytest.mark.parametrize(
    ("rsi", "expected"),
    [
        (75.0, "OVERBOUGHT_RISK"),
        (65.0, "HIGH_NOT_OVERBOUGHT"),
        (53.68, "NEUTRAL"),
        (35.0, "LOW_NOT_OVERSOLD"),
        (25.0, "OVERSOLD"),
    ],
)
def test_rsi_regime_is_deterministic(rsi, expected):
    assert OpportunityScoringService._rsi_regime(rsi) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4.0, "ABOVE_SUPPORTIVE"),
        (0.5, "NEAR_NEUTRAL"),
        (-0.5, "NEAR_NEUTRAL"),
        (-4.0, "BELOW_ADVERSE"),
    ],
)
def test_moving_average_regime_is_deterministic(value, expected):
    assert OpportunityScoringService._moving_average_regime(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.35, "ELEVATED"),
        (1.03, "NORMAL"),
        (0.70, "DEPRESSED"),
    ],
)
def test_rvol_regime_is_deterministic(value, expected):
    assert OpportunityScoringService._rvol_regime(value) == expected


def test_canonical_technical_bias_handles_mixed_signals():
    features = OpportunityScoringService._build_canonical_technical_features(
        "1-session return: -1.5%. "
        "5-session return: 3.0%. "
        "20-session return: -4.0%. "
        "Close vs SMA20: 2.0%. "
        "Close vs SMA50: -2.0%. "
        "RSI14: 50.0. RVOL: 1.00x."
    )

    assert features["supportive_signal_count"] == 2
    assert features["adverse_signal_count"] == 2
    assert features["technical_bias"] == "MIXED_NEUTRAL"


def test_prompt_contains_authoritative_canonical_technical_features():
    provider = FakeProvider(output())
    OpportunityScoringService(provider).score(
        research(
            technical_context=(
                "1-session return: 0.84%. "
                "5-session return: 5.89%. "
                "20-session return: 2.86%. "
                "Close vs SMA20: 4.67%. "
                "Close vs SMA50: 9.40%. "
                "RSI14: 53.68. RVOL: 1.03x."
            )
        ),
        evidence_coverage_score=.80,
    )

    prompt = provider.requests[0].prompt

    assert "CANONICAL TECHNICAL FEATURES" in prompt
    assert "- trend_vs_sma20: ABOVE_SUPPORTIVE" in prompt
    assert "- trend_vs_sma50: ABOVE_SUPPORTIVE" in prompt
    assert "- rsi_regime: NEUTRAL" in prompt
    assert "- volume_regime: NORMAL" in prompt
    assert "- technical_bias: POSITIVE" in prompt
    assert "deterministic and authoritative" in prompt


def test_default_prompt_version_marks_canonical_technical_input_v5():
    service = OpportunityScoringService(FakeProvider(output()))
    assert (
        service.prompt_version
        == "opportunity-scoring-v14-directional-company-frame"
    )


def test_deterministic_technical_base_score_is_stable_for_positive_alignment():
    features = OpportunityScoringService._build_canonical_technical_features(
        "1-session return: 0.84%. "
        "5-session return: 5.89%. "
        "20-session return: 4.00%. "
        "Close vs SMA20: 4.67%. "
        "Close vs SMA50: 9.40%. "
        "RSI14: 53.68. RVOL: 1.03x."
    )

    assert OpportunityScoringService._deterministic_technical_base_score(
        features
    ) == pytest.approx(85.0)


@pytest.mark.parametrize(
    ("ai_score", "base_score", "expected"),
    [
        (90.0, 60.0, 10.0),
        (72.0, 60.0, 5.0),
        (64.0, 60.0, 0.0),
        (50.0, 60.0, -5.0),
        (35.0, 60.0, -10.0),
        (None, 60.0, 0.0),
    ],
)
def test_ai_technical_adjustment_is_bounded(ai_score, base_score, expected):
    assert OpportunityScoringService._technical_adjustment_from_ai_score(
        ai_score, base_score
    ) == expected


def test_bounded_technical_score_limits_model_variance():
    features = OpportunityScoringService._build_canonical_technical_features(
        "5-session return: 3.0%. "
        "20-session return: 4.0%. "
        "Close vs SMA20: 2.0%. "
        "Close vs SMA50: 3.0%. "
        "RSI14: 50.0. RVOL: 1.00x."
    )
    base = OpportunityScoringService._deterministic_technical_base_score(features)

    low = OpportunityScoringService._apply_bounded_technical_score(
        output(technical={
            "score": 20,
            "rationale": "Low model opinion.",
            "supporting_evidence_ids": ["E1"],
        }),
        features,
        True,
    )
    high = OpportunityScoringService._apply_bounded_technical_score(
        output(technical={
            "score": 95,
            "rationale": "High model opinion.",
            "supporting_evidence_ids": ["E1"],
        }),
        features,
        True,
    )

    assert low["technical"]["score"] == pytest.approx(base - 10)
    assert high["technical"]["score"] == pytest.approx(base + 5)
    assert high["technical"]["score"] - low["technical"]["score"] <= 15


def test_bounded_technical_score_can_reach_plus_ten_for_large_model_disagreement():
    features = OpportunityScoringService._build_canonical_technical_features(
        "5-session return: 0.0%. "
        "20-session return: 0.0%. "
        "Close vs SMA20: 0.0%. "
        "Close vs SMA50: 0.0%. "
        "RSI14: 50.0. RVOL: 1.00x."
    )
    base = OpportunityScoringService._deterministic_technical_base_score(features)

    payload = OpportunityScoringService._apply_bounded_technical_score(
        output(technical={
            "score": 80,
            "rationale": "Strong positive semantic disagreement.",
            "supporting_evidence_ids": ["E1"],
        }),
        features,
        True,
    )

    assert base == pytest.approx(50.0)
    assert payload["technical"]["score"] == pytest.approx(60.0)


def test_prompt_exposes_deterministic_technical_base_and_bounded_contract():
    provider = FakeProvider(output())
    OpportunityScoringService(provider).score(
        research(
            technical_context=(
                "5-session return: 3.0%. "
                "20-session return: 4.0%. "
                "Close vs SMA20: 2.0%. "
                "Close vs SMA50: 3.0%. "
                "RSI14: 50.0. RVOL: 1.00x."
            )
        ),
        evidence_coverage_score=.80,
    )

    prompt = provider.requests[0].prompt
    assert "Deterministic technical base score:" in prompt
    assert "bounded adjustment" in prompt
    assert "-10, -5, 0, +5 or +10" in prompt


def canonical_input(**overrides):
    data = dict(
        ticker="PATH",
        current_price=20.0,
        return_1d_pct=1.0,
        return_5d_pct=3.0,
        return_20d_pct=5.0,
        sma20=19.0,
        sma50=18.0,
        close_vs_sma20_pct=5.263,
        close_vs_sma50_pct=11.111,
        rsi14=55.0,
        rvol=1.05,
        trend="BULLISH",
    )
    data.update(overrides)
    return CanonicalTechnicalInput(**data)


def test_canonical_technical_input_overrides_research_text_numbers():
    r = research(
        technical_context=(
            "1-session return: -9.0%. "
            "5-session return: -12.0%. "
            "20-session return: -20.0%. "
            "Close vs SMA20: -15.0%. "
            "Close vs SMA50: -20.0%. "
            "RSI14: 25.0. RVOL: 2.0x."
        )
    )

    features = OpportunityScoringService._technical_features_for_scoring(
        r,
        canonical_input(),
    )

    assert features["return_5d_pct"] == pytest.approx(3.0)
    assert features["trend_vs_sma20"] == "ABOVE_SUPPORTIVE"
    assert features["rsi_regime"] == "NEUTRAL"
    assert features["technical_bias"] == "POSITIVE"
    assert features["technical_input_source"] == "STAGE2_YAHOO_TECHNICAL"


def test_canonical_technical_input_ticker_must_match_research():
    with pytest.raises(ValueError, match="ticker must match"):
        OpportunityScoringService._technical_features_for_scoring(
            research(ticker="PATH"),
            canonical_input(ticker="NVDA"),
        )


def test_score_prompt_uses_stage2_canonical_source_when_supplied():
    provider = FakeProvider(output())
    OpportunityScoringService(provider).score(
        research(),
        evidence_coverage_score=.80,
        canonical_technical_input=canonical_input(),
    )

    prompt = provider.requests[0].prompt
    assert "technical_input_source: STAGE2_YAHOO_TECHNICAL" in prompt
    assert "numerical source of truth" in prompt


def test_fallback_remains_available_for_non_live_callers():
    features = OpportunityScoringService._technical_features_for_scoring(
        research(
            technical_context=(
                "5-session return: 3.0%. "
                "20-session return: 4.0%. "
                "Close vs SMA20: 2.0%. "
                "Close vs SMA50: 3.0%. "
                "RSI14: 50.0. RVOL: 1.00x."
            )
        ),
        None,
    )
    assert features["technical_input_source"] == "RESEARCH_TEXT_FALLBACK"


def test_scorable_null_component_gets_targeted_scorability_repair():
    initial = output(fundamental={"score": None, "rationale": None, "supporting_evidence_ids": []})
    provider = SequentialFakeProvider([
        initial,
        {"fundamental": {"score": 45, "rationale": "Mixed but scorable fundamentals.", "supporting_evidence_ids": ["E1"]}},
    ])
    result = OpportunityScoringService(provider).score(research(), evidence_coverage_score=.80)
    assert len(provider.requests) == 2
    assert provider.requests[1].metadata["repair_reason"] == "SCORABLE_COMPONENT_RETURNED_NULL"
    assert provider.requests[1].metadata["repair_components"] == ["fundamental"]
    assert result.components.fundamental.score == 47.5
    assert result.components.thesis.score == 75


def test_scorability_repair_fails_closed_if_model_returns_null_again():
    initial = output(fundamental={"score": None, "rationale": None, "supporting_evidence_ids": []})
    provider = SequentialFakeProvider([
        initial,
        {"fundamental": {"score": None, "rationale": None, "supporting_evidence_ids": []}},
    ])
    with pytest.raises(ValueError, match="fundamental is SCORABLE"):
        OpportunityScoringService(provider).score(research(), evidence_coverage_score=.80)


def test_unscorable_null_does_not_trigger_scorability_repair():
    payload = output(fundamental={"score": None, "rationale": None, "supporting_evidence_ids": []})
    provider = FakeProvider(payload)
    result = OpportunityScoringService(provider).score(
        research(fundamental_context=None), evidence_coverage_score=.80
    )
    assert len(provider.requests) == 1
    assert result.components.fundamental.score is None


def test_prompt_makes_scorable_numeric_assessment_mandatory():
    provider = FakeProvider(output())
    OpportunityScoringService(provider).score(research(), evidence_coverage_score=.80)
    prompt = provider.requests[0].prompt
    assert "SCORABLE is a deterministic software decision" in prompt
    assert "Every SCORABLE component MUST return a numeric score" in prompt


def test_result_exposes_scorability_provenance_without_changing_scores():
    result = OpportunityScoringService(FakeProvider(output())).score(
        research(), evidence_coverage_score=.80
    )
    fundamental = result.diagnostics["components"]["fundamental"]
    assert fundamental["context_present"] is True
    assert fundamental["deterministic_scorable"] is True
    assert fundamental["initial_model_score"] == 65
    assert fundamental["scorability_repair_attempted"] is False
    assert fundamental["grounding_repair_attempted"] is False
    assert fundamental["final_score"] == 60.0


def test_provenance_records_scorability_repair_for_initial_null():
    initial = output(
        fundamental={
            "score": None,
            "rationale": None,
            "supporting_evidence_ids": [],
        }
    )
    provider = SequentialFakeProvider([
        initial,
        {
            "fundamental": {
                "score": 45,
                "rationale": "Mixed but scorable fundamentals.",
                "supporting_evidence_ids": ["E1"],
            }
        },
    ])
    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )
    fundamental = result.diagnostics["components"]["fundamental"]
    assert fundamental["initial_model_null"] is True
    assert fundamental["scorability_repair_attempted"] is True
    assert fundamental["final_score"] == 47.5


def test_provenance_distinguishes_upstream_unscorable_from_model_null():
    payload = output(
        fundamental={
            "score": None,
            "rationale": None,
            "supporting_evidence_ids": [],
        }
    )
    result = OpportunityScoringService(FakeProvider(payload)).score(
        research(fundamental_context=None),
        evidence_coverage_score=.80,
    )
    fundamental = result.diagnostics["components"]["fundamental"]
    assert fundamental["context_present"] is False
    assert fundamental["deterministic_scorable"] is False
    assert fundamental["scorability_repair_attempted"] is False
    assert fundamental["final_null"] is True


def test_provenance_records_grounding_repair_separately():
    initial = output(
        fundamental={
            "score": 45,
            "rationale": "Mixed fundamentals.",
            "supporting_evidence_ids": [],
        }
    )
    provider = SequentialFakeProvider([
        initial,
        {
            "fundamental": {
                "score": 45,
                "rationale": "Grounded fundamentals.",
                "supporting_evidence_ids": ["E1"],
            }
        },
    ])
    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )
    fundamental = result.diagnostics["components"]["fundamental"]
    assert fundamental["scorability_repair_attempted"] is False
    assert fundamental["grounding_repair_attempted"] is True
    assert fundamental["final_score"] == 47.5


@pytest.mark.parametrize(
    ("raw_score", "expected"),
    [
        (25.0, 37.5),
        (45.0, 47.5),
        (50.0, 50.0),
        (60.0, 55.0),
        (70.0, 60.0),
        (95.0, 72.5),
        (None, None),
    ],
)
def test_catalyst_semantic_shrinkage_is_deterministic(raw_score, expected):
    result = OpportunityScoringService._shrink_semantic_score_toward_neutral(
        raw_score,
        reliability=.50,
    )
    assert result == expected


def test_catalyst_calibration_preserves_rationale_and_evidence():
    payload = output(
        catalyst={
            "score": 70,
            "rationale": "Credible catalyst with meaningful limitations.",
            "supporting_evidence_ids": ["E2"],
        }
    )
    calibrated = OpportunityScoringService._apply_nontechnical_semantic_calibration(
        payload,
        {"thesis": True, "catalyst": True, "fundamental": True,
         "technical": True, "expectations": True},
    )

    assert calibrated["catalyst"]["score"] == 60.0
    assert calibrated["catalyst"]["rationale"] == (
        "Credible catalyst with meaningful limitations."
    )
    assert calibrated["catalyst"]["supporting_evidence_ids"] == ["E2"]


def test_nontechnical_calibration_preserves_thesis_and_calibrates_fundamental():
    payload = output(
        thesis={
            "score": 80,
            "rationale": "Strong thesis.",
            "supporting_evidence_ids": ["E1"],
        },
        fundamental={
            "score": 75,
            "rationale": "Strong fundamentals.",
            "supporting_evidence_ids": ["E1"],
        },
        catalyst={
            "score": 70,
            "rationale": "Catalyst.",
            "supporting_evidence_ids": ["E2"],
        },
    )
    calibrated = OpportunityScoringService._apply_nontechnical_semantic_calibration(
        payload,
        {"thesis": True, "catalyst": True, "fundamental": True,
         "technical": True, "expectations": True},
    )

    assert calibrated["thesis"]["score"] == 80
    assert calibrated["fundamental"]["score"] == 65.0
    assert calibrated["catalyst"]["score"] == 60


def test_unscorable_catalyst_remains_null_and_uncalibrated():
    payload = output(
        catalyst={
            "score": None,
            "rationale": None,
            "supporting_evidence_ids": [],
        }
    )
    calibrated = OpportunityScoringService._apply_nontechnical_semantic_calibration(
        payload,
        {"thesis": True, "catalyst": False, "fundamental": True,
         "technical": True, "expectations": False},
    )
    assert calibrated["catalyst"]["score"] is None


def test_service_returns_calibrated_catalyst_score():
    provider = FakeProvider(
        output(
            catalyst={
                "score": 70,
                "rationale": "Near-term catalyst.",
                "supporting_evidence_ids": ["E2"],
            }
        )
    )
    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )

    assert result.components.catalyst.score == 60.0
    assert (
        result.diagnostics["components"]["catalyst"]["semantic_calibration"]
        == "CATALYST_SHRINK_0.50"
    )
    # Provider/raw semantic opinion remains visible for provenance.
    assert (
        result.diagnostics["components"]["catalyst"]["initial_model_score"]
        == 70
    )


def test_catalyst_calibration_rejects_invalid_reliability():
    with pytest.raises(ValueError, match="reliability"):
        OpportunityScoringService._shrink_semantic_score_toward_neutral(
            60,
            reliability=1.1,
        )


# AI-8C.3c — Factor Extraction Completeness

def test_canonical_factor_extraction_populates_empty_model_factor_lists():
    payload = output(
        positive_factors=[],
        negative_factors=[],
        uncertainty_factors=[],
    )
    result = OpportunityScoringService(FakeProvider(payload)).score(
        research(), evidence_coverage_score=.80
    )

    assert result.components.positive_factors == [
        "Execution beats expectations."
    ]
    assert result.components.negative_factors == [
        "Competition",
        "Competition and valuation pressure.",
    ]
    assert result.components.uncertainty_factors == [
        "Exact consensus range"
    ]


def test_canonical_factor_extraction_ignores_free_form_model_factors():
    payload = output(
        positive_factors=["Invented positive factor"],
        negative_factors=["Invented negative factor"],
        uncertainty_factors=["Invented uncertainty"],
    )
    result = OpportunityScoringService(FakeProvider(payload)).score(
        research(), evidence_coverage_score=.80
    )

    assert "Invented positive factor" not in result.components.positive_factors
    assert "Invented negative factor" not in result.components.negative_factors
    assert "Invented uncertainty" not in result.components.uncertainty_factors


def test_uncertainty_factors_include_unknowns_and_contradictions():
    result = OpportunityScoringService(FakeProvider(output())).score(
        research(
            unknowns=["Consensus missing", "Timing uncertain"],
            contradictory_evidence=[
                "Revenue accelerated while margins contracted."
            ],
        ),
        evidence_coverage_score=.80,
    )

    assert result.components.uncertainty_factors == [
        "Consensus missing",
        "Timing uncertain",
        "Revenue accelerated while margins contracted.",
    ]


def test_factor_extraction_deduplicates_and_bounds_lists():
    result = OpportunityScoringService(FakeProvider(output())).score(
        research(
            key_risks=[
                "Competition",
                "competition",
                "Valuation",
                "Execution",
                "Liquidity",
                "Macro",
            ],
            bear_case="Execution",
        ),
        evidence_coverage_score=.80,
    )

    assert result.components.negative_factors == [
        "Competition",
        "Valuation",
        "Execution",
        "Liquidity",
        "Macro",
    ]


def test_factor_extraction_records_provenance():
    result = OpportunityScoringService(FakeProvider(output())).score(
        research(), evidence_coverage_score=.80
    )
    diagnostics = result.diagnostics["factor_extraction"]

    assert diagnostics["applied"] is True
    assert diagnostics["policy_version"] == (
        "factor-extraction-v2-grounded-components"
    )
    assert diagnostics["final_factor_counts"] == {
        "positive_factors": 1,
        "negative_factors": 2,
        "uncertainty_factors": 1,
    }


def test_transport_normalization_compatibility_bypasses_factor_extraction():
    payload = output(
        positive_factors=["Provider positive"],
        negative_factors=["Provider negative"],
        uncertainty_factors=["Provider uncertainty"],
    )
    result = OpportunityScoringService(
        FakeProvider(payload),
        normalize_provider_transport=True,
    ).score(research(), evidence_coverage_score=.80)

    assert result.components.positive_factors == ["Provider positive"]
    assert result.components.negative_factors == ["Provider negative"]
    assert result.components.uncertainty_factors == ["Provider uncertainty"]
    assert result.diagnostics["factor_extraction"]["applied"] is False


def test_factor_text_is_compacted_and_bounded():
    long_bull = "  " + ("Strong execution   " * 30)
    factors = OpportunityScoringService._canonical_factors_from_research(
        research(bull_case=long_bull)
    )
    assert len(factors["positive_factors"]) == 1
    assert len(factors["positive_factors"][0]) <= 240
    assert "  " not in factors["positive_factors"][0]


def test_factor_extraction_falls_back_to_grounded_component_rationales():
    r = research(
        bull_case=None,
        bear_case=None,
        key_risks=[],
        unknowns=[],
        contradictory_evidence=[],
    )
    payload = output(
        thesis={
            "score": 60,
            "rationale": "Thesis has moderate positive support.",
            "supporting_evidence_ids": ["E1"],
        },
        catalyst={
            "score": 50,
            "rationale": "Catalyst evidence is mixed.",
            "supporting_evidence_ids": ["E2"],
        },
        fundamental={
            "score": 40,
            "rationale": "Fundamental evidence is adverse.",
            "supporting_evidence_ids": ["E1"],
        },
        technical={
            "score": 50,
            "rationale": "Technical evidence is mixed.",
            "supporting_evidence_ids": ["E3"],
        },
        positive_factors=[],
        negative_factors=[],
        uncertainty_factors=[],
    )
    result = OpportunityScoringService(FakeProvider(payload)).score(
        r, evidence_coverage_score=.80
    )

    assert "Thesis has moderate positive support." in result.components.positive_factors
    assert "Fundamental evidence is adverse." in result.components.negative_factors
    assert "Catalyst evidence is mixed." in result.components.uncertainty_factors
    assert "Technical evidence is mixed." in result.components.uncertainty_factors


def test_factor_component_fallback_uses_final_calibrated_scores():
    r = research(
        bull_case=None,
        bear_case=None,
        key_risks=[],
        unknowns=[],
        contradictory_evidence=[],
    )
    payload = output(
        catalyst={
            "score": 70,
            "rationale": "Catalyst is favorable but not exceptional.",
            "supporting_evidence_ids": ["E2"],
        },
        positive_factors=[],
        negative_factors=[],
        uncertainty_factors=[],
    )
    result = OpportunityScoringService(FakeProvider(payload)).score(
        r, evidence_coverage_score=.80
    )

    # 70 raw Catalyst is calibrated to 60, which remains positive.
    assert result.components.catalyst.score == 60.0
    assert "Catalyst is favorable but not exceptional." in result.components.positive_factors


def test_factor_extraction_v2_provenance_records_component_fallbacks():
    r = research(
        bull_case=None,
        bear_case=None,
        key_risks=[],
        unknowns=[],
        contradictory_evidence=[],
    )
    result = OpportunityScoringService(FakeProvider(output())).score(
        r, evidence_coverage_score=.80
    )
    diag = result.diagnostics["factor_extraction"]
    assert diag["policy_version"] == "factor-extraction-v2-grounded-components"
    assert diag["applied"] is True
    assert isinstance(diag["component_fallback_sources"], list)


# AI-8C.3d.2 — Fundamental Score Variance Calibration

@pytest.mark.parametrize(
    ("raw_score", "expected"),
    [
        (40.0, 42.5),
        (50.0, 50.0),
        (55.0, 52.5),
        (60.0, 57.5),
        (65.0, 60.0),
        (70.0, 62.5),
        (80.0, 70.0),
        (None, None),
    ],
)
def test_fundamental_semantic_shrinkage_is_deterministic(raw_score, expected):
    result = OpportunityScoringService._shrink_semantic_score_toward_neutral(
        raw_score,
        reliability=.65,
    )
    assert result == expected


def test_fundamental_calibration_preserves_rationale_and_evidence():
    payload = output(
        fundamental={
            "score": 70,
            "rationale": "Strong growth with valuation constraints.",
            "supporting_evidence_ids": ["E1"],
        }
    )
    calibrated = OpportunityScoringService._apply_nontechnical_semantic_calibration(
        payload,
        {
            "thesis": True,
            "catalyst": True,
            "fundamental": True,
            "technical": True,
            "expectations": False,
        },
    )
    assert calibrated["fundamental"]["score"] == 62.5
    assert calibrated["fundamental"]["rationale"] == (
        "Strong growth with valuation constraints."
    )
    assert calibrated["fundamental"]["supporting_evidence_ids"] == ["E1"]


def test_unscorable_fundamental_is_not_calibrated():
    payload = output(
        fundamental={
            "score": None,
            "rationale": None,
            "supporting_evidence_ids": [],
        }
    )
    calibrated = OpportunityScoringService._apply_nontechnical_semantic_calibration(
        payload,
        {
            "thesis": True,
            "catalyst": True,
            "fundamental": False,
            "technical": True,
            "expectations": False,
        },
    )
    assert calibrated["fundamental"]["score"] is None


def test_service_returns_calibrated_fundamental_with_raw_provenance():
    provider = FakeProvider(
        output(
            fundamental={
                "score": 70,
                "rationale": "Grounded fundamental assessment.",
                "supporting_evidence_ids": ["E1"],
            }
        )
    )
    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )

    assert result.components.fundamental.score == 62.5
    fundamental_diag = result.diagnostics["components"]["fundamental"]
    assert fundamental_diag["initial_model_score"] == 70
    assert fundamental_diag["final_score"] == 62.5
    assert fundamental_diag["semantic_calibration"] == (
        "FUNDAMENTAL_SHRINK_0.65"
    )


def test_fundamental_calibration_does_not_change_thesis():
    payload = output(
        thesis={
            "score": 75,
            "rationale": "Thesis.",
            "supporting_evidence_ids": ["E1"],
        },
        fundamental={
            "score": 70,
            "rationale": "Fundamentals.",
            "supporting_evidence_ids": ["E1"],
        },
    )
    calibrated = OpportunityScoringService._apply_nontechnical_semantic_calibration(
        payload,
        {
            "thesis": True,
            "catalyst": True,
            "fundamental": True,
            "technical": True,
            "expectations": False,
        },
    )
    assert calibrated["thesis"]["score"] == 75
    assert calibrated["fundamental"]["score"] == 62.5


def test_transport_compatibility_bypasses_fundamental_calibration():
    payload = output(
        fundamental={
            "score": 70,
            "rationale": "Provider fundamental.",
            "supporting_evidence_ids": ["E1"],
        }
    )
    result = OpportunityScoringService(
        FakeProvider(payload),
        normalize_provider_transport=True,
    ).score(research(), evidence_coverage_score=.80)

    assert result.components.fundamental.score == 70
    assert (
        result.diagnostics["components"]["fundamental"]["semantic_calibration"]
        == "NONE"
    )


# AI-8C.3f.1 — Score–Rationale Consistency Diagnostics

def test_high_score_with_supportive_only_rationale_is_aligned():
    result = OpportunityScoringService._classify_score_rationale_consistency(
        70,
        "Strong revenue growth supports a favorable fundamental outlook.",
    )
    assert result["status"] == "ALIGNED"


def test_high_score_with_adverse_only_rationale_is_possible_contradiction():
    result = OpportunityScoringService._classify_score_rationale_consistency(
        70,
        "Revenue declined and downside risk remains elevated.",
    )
    assert result["status"] == "POSSIBLE_CONTRADICTION"


def test_low_score_with_adverse_only_rationale_is_aligned():
    result = OpportunityScoringService._classify_score_rationale_consistency(
        35,
        "Negative momentum and downside pressure remain material.",
    )
    assert result["status"] == "ALIGNED"


def test_low_score_with_supportive_only_rationale_is_possible_contradiction():
    result = OpportunityScoringService._classify_score_rationale_consistency(
        35,
        "Strong growth and positive execution support upside.",
    )
    assert result["status"] == "POSSIBLE_CONTRADICTION"


def test_mixed_rationale_is_not_called_contradiction():
    result = OpportunityScoringService._classify_score_rationale_consistency(
        65,
        "Strong growth supports the thesis, but valuation risk remains.",
    )
    assert result["status"] == "MIXED"


def test_neutral_band_is_neutral_even_with_mixed_language():
    result = OpportunityScoringService._classify_score_rationale_consistency(
        50,
        "Positive growth is offset by downside risk.",
    )
    assert result["status"] == "NEUTRAL_BAND"


def test_score_rationale_diagnostics_are_attached_without_score_change():
    provider = FakeProvider(
        output(
            thesis={
                "score": 70,
                "rationale": "Revenue declined and downside risk remains.",
                "supporting_evidence_ids": ["E1"],
            }
        )
    )
    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80
    )

    # Diagnostics-only: numeric score remains governed by existing frozen logic.
    assert result.components.thesis.score == 70
    diag = result.diagnostics["score_rationale_consistency"]
    assert diag["diagnostics_only"] is True
    assert "thesis" in diag["possible_contradictions"]


def test_null_component_is_unassessable_not_contradiction():
    diag = OpportunityScoringService._classify_score_rationale_consistency(
        None, None
    )
    assert diag["status"] == "UNASSESSABLE"


def test_credible_catalyst_with_risk_language_is_mixed_not_contradiction():
    result = OpportunityScoringService._classify_score_rationale_consistency(
        60,
        "The September earnings report and product event are credible catalysts "
        "with plausible near-term impact, though the settlement introduces risks.",
    )
    assert result["status"] == "MIXED"
