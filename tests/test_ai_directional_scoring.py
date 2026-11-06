"""AI-8C.3-R2: opportunity scoring measures support for the hypothesis direction."""
from __future__ import annotations

import itertools

import pytest

from app.ai.opportunity_score_models import OpportunityScoringProfile
from app.ai.opportunity_scoring_service import (
    DIRECTIONAL_SCORING_POLICY,
    OpportunityScoringService,
)
from app.cio.models import Direction
from app.scanner.research_integration import (
    build_research_hypotheses,
    hypothesis_direction,
    opportunity_materialization_decision,
)
from app.scanner.research_integration_contracts import (
    HypothesisOutcomeReason,
    ResearchHypothesisKind,
)
from tests.test_ai_canonical_technical_volatility import _input as canonical_input
from tests.test_ai_opportunity_scoring_service import (
    FakeProvider,
    SequentialFakeProvider,
    output,
    research,
)
from tests.test_scanner_research_integration import (
    NOW,
    research as integration_research,
    score as integration_score,
    universe,
)


Service = OpportunityScoringService

MOMENTUM = ("POSITIVE", "NEGATIVE", "NEUTRAL", "UNKNOWN")
TREND = ("ABOVE_SUPPORTIVE", "BELOW_ADVERSE", "NEAR_NEUTRAL", "UNKNOWN")
RSI = ("OVERBOUGHT_RISK", "OVERSOLD", "NEUTRAL", "UNKNOWN")
VOLUME = ("ELEVATED", "NORMAL")
BIAS = ("POSITIVE", "POSITIVE_MIXED", "NEGATIVE", "NEGATIVE_MIXED", "MIXED_NEUTRAL")


def _legacy_long_base(features):
    """Verbatim pre-R2 mapping, kept here as the LONG regression oracle."""
    directional = (
        features.get("momentum_5d"), features.get("momentum_20d"),
        features.get("trend_vs_sma20"), features.get("trend_vs_sma50"),
    )
    known = [value for value in directional if value != "UNKNOWN"]
    if not known:
        return None
    score = 50.0
    weights = {
        "POSITIVE": 7.5, "NEGATIVE": -7.5, "NEUTRAL": 0.0,
        "ABOVE_SUPPORTIVE": 10.0, "BELOW_ADVERSE": -10.0, "NEAR_NEUTRAL": 0.0,
    }
    for value in known:
        score += weights.get(value, 0.0)
    if features.get("rsi_regime") in {"OVERBOUGHT_RISK", "OVERSOLD"}:
        score -= 2.5
    if features.get("volume_regime") == "ELEVATED":
        if features.get("technical_bias") in {"POSITIVE", "POSITIVE_MIXED"}:
            score += 5.0
        elif features.get("technical_bias") in {"NEGATIVE", "NEGATIVE_MIXED"}:
            score -= 5.0
    return max(0.0, min(100.0, round(score / 2.5) * 2.5))


def _all_features():
    for m5, m20, t20, t50, rsi, volume, bias in itertools.product(
        MOMENTUM, MOMENTUM, TREND, TREND, RSI, VOLUME, BIAS
    ):
        yield {
            "momentum_5d": m5, "momentum_20d": m20,
            "trend_vs_sma20": t20, "trend_vs_sma50": t50,
            "rsi_regime": rsi, "volume_regime": volume, "technical_bias": bias,
        }


def test_long_base_is_identical_to_the_pre_r2_mapping():
    for features in _all_features():
        assert Service._deterministic_technical_base_score(features) == _legacy_long_base(features)
        assert Service._deterministic_technical_base_score(
            features, direction=Direction.LONG
        ) == _legacy_long_base(features)


def test_short_base_mirrors_direction_and_keeps_rsi_risk():
    for features in _all_features():
        long_base = Service._deterministic_technical_base_score(features, Direction.LONG)
        short_base = Service._deterministic_technical_base_score(features, Direction.SHORT)
        if long_base is None:
            assert short_base is None
            continue
        risk = -2.5 if features["rsi_regime"] in {"OVERBOUGHT_RISK", "OVERSOLD"} else 0.0
        if 0.0 < long_base < 100.0 and 0.0 < short_base < 100.0:
            assert long_base + short_base == pytest.approx(100.0 + 2 * risk)


def test_bullish_technicals_favor_long_and_bearish_favor_short():
    bullish = {
        "momentum_5d": "POSITIVE", "momentum_20d": "POSITIVE",
        "trend_vs_sma20": "ABOVE_SUPPORTIVE", "trend_vs_sma50": "ABOVE_SUPPORTIVE",
        "rsi_regime": "NEUTRAL", "volume_regime": "NORMAL", "technical_bias": "POSITIVE",
    }
    bearish = {
        "momentum_5d": "NEGATIVE", "momentum_20d": "NEGATIVE",
        "trend_vs_sma20": "BELOW_ADVERSE", "trend_vs_sma50": "BELOW_ADVERSE",
        "rsi_regime": "NEUTRAL", "volume_regime": "NORMAL", "technical_bias": "NEGATIVE",
    }
    assert Service._deterministic_technical_base_score(bullish, Direction.LONG) == 85.0
    assert Service._deterministic_technical_base_score(bullish, Direction.SHORT) == 15.0
    assert Service._deterministic_technical_base_score(bearish, Direction.LONG) == 15.0
    assert Service._deterministic_technical_base_score(bearish, Direction.SHORT) == 85.0


def _score(direction, technical_opinion=78):
    provider = FakeProvider(output(technical={
        "score": technical_opinion, "rationale": "Technical context.",
        "supporting_evidence_ids": ["E3"],
    }))
    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80, direction=direction,
        canonical_technical_input=canonical_input(ticker="PATH"),
    )
    return provider.requests[0], result


def test_score_declares_direction_in_prompt_metadata_and_diagnostics():
    request, result = _score(Direction.SHORT)
    assert "HYPOTHESIS DIRECTION: SHORT" in request.prompt
    assert "support for a SHORT position" in request.prompt
    assert request.metadata["direction"] == "SHORT"
    assert request.metadata["prompt_version"] == "opportunity-scoring-v14-directional-company-frame"
    diagnostics = result.diagnostics["direction"]
    assert diagnostics["policy_version"] == DIRECTIONAL_SCORING_POLICY
    assert diagnostics["direction"] == "SHORT"
    assert diagnostics["direction_source"] == "HYPOTHESIS"
    assert result.diagnostics["score_rationale_consistency"]["direction"] == "SHORT"


def test_missing_direction_is_recorded_as_default_long():
    provider = FakeProvider(output())
    result = OpportunityScoringService(provider).score(research(), evidence_coverage_score=.80)
    assert "HYPOTHESIS DIRECTION: LONG" in provider.requests[0].prompt
    assert result.diagnostics["direction"]["direction_source"] == "DEFAULT_LONG"


def test_technical_component_is_computed_against_the_directional_base():
    _, long_result = _score(Direction.LONG, technical_opinion=50)
    _, short_result = _score(Direction.SHORT, technical_opinion=50)
    long_base = long_result.diagnostics["direction"]["technical_base_directional"]
    short_base = short_result.diagnostics["direction"]["technical_base_directional"]
    assert long_base is not None and short_base is not None
    assert long_base != short_base
    assert long_result.diagnostics["direction"]["technical_base_long_frame"] == long_base
    assert short_result.diagnostics["direction"]["technical_base_long_frame"] == long_base


def test_model_ignoring_direction_is_bounded_and_flagged():
    _, result = _score(Direction.SHORT, technical_opinion=95)
    diagnostics = result.diagnostics["direction"]
    base = diagnostics["technical_base_directional"]
    assert diagnostics["possible_direction_ignored"] is True
    assert result.components.technical.score <= base + 10.0


def test_short_factors_mirror_the_research_cases():
    value = research(bull_case="Upside case.", bear_case="Downside case.", key_risks=["Risk A"])
    long_factors = Service._canonical_factors_from_research(value, Direction.LONG)
    short_factors = Service._canonical_factors_from_research(value, Direction.SHORT)
    assert long_factors["positive_factors"] == ["Upside case."]
    assert short_factors["positive_factors"] == ["Downside case.", "Risk A"]
    assert short_factors["negative_factors"] == ["Upside case."]


def test_short_rationale_consistency_inverts_polarity():
    result = Service._classify_score_rationale_consistency(
        80.0, "Revenue decline and bearish momentum.", direction=Direction.SHORT
    )
    assert result["status"] == "ALIGNED"
    long_result = Service._classify_score_rationale_consistency(
        80.0, "Revenue decline and bearish momentum.", direction=Direction.LONG
    )
    assert long_result["status"] == "POSSIBLE_CONTRADICTION"


def test_repair_prompts_carry_the_direction():
    provider = SequentialFakeProvider([
        output(fundamental={"score": 65, "rationale": "Support.", "supporting_evidence_ids": []}),
        {"fundamental": {"score": 40, "rationale": "Repaired.", "supporting_evidence_ids": ["E1"]}},
    ])
    OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80, direction=Direction.SHORT,
    )
    assert len(provider.requests) == 2
    assert provider.requests[1].prompt.startswith("HYPOTHESIS DIRECTION: SHORT")


def test_short_prompt_stays_within_the_size_guard():
    huge = "Evidence sentence one. Evidence sentence two. " * 10000
    value = research(
        market_context=huge, fundamental_context=huge, technical_context=huge,
        event_context=huge, catalyst_assessment=huge, bull_case=huge, bear_case=huge,
        key_risks=[huge], contradictory_evidence=[huge], unknowns=[huge],
    )
    prompt = OpportunityScoringService(provider=object())._build_prompt(
        value, OpportunityScoringProfile.STANDARD, direction=Direction.SHORT
    )
    assert len(prompt) < 14000


@pytest.mark.parametrize("kind, expected", [
    (ResearchHypothesisKind.NEW_LONG, Direction.LONG),
    (ResearchHypothesisKind.NEW_SHORT, Direction.SHORT),
    (ResearchHypothesisKind.PORTFOLIO_MONITOR, None),
    (ResearchHypothesisKind.MEMBER_REVIEW, None),
])
def test_hypothesis_direction(kind, expected):
    assert hypothesis_direction(kind) is expected


@pytest.fixture
def short_enabled(monkeypatch):
    import app.scanner.research_integration as integration

    monkeypatch.setattr(integration, "SHORT_MATERIALIZATION_ENABLED", True)


def _directional_hypotheses():
    hypotheses = build_research_hypotheses(universe(), created_at=NOW)
    long = next(h for h in hypotheses if h.kind is ResearchHypothesisKind.NEW_LONG)
    short = next(h for h in hypotheses if h.kind is ResearchHypothesisKind.NEW_SHORT)
    return long, short


def _direction_metadata(value, source="HYPOTHESIS", policy=DIRECTIONAL_SCORING_POLICY):
    return {"scoring_diagnostics": {"direction": {
        "direction": value, "direction_source": source, "policy_version": policy,
    }}}


def test_materialization_rejects_score_without_direction(short_enabled):
    _, short = _directional_hypotheses()
    complete = integration_research(short)
    decision = opportunity_materialization_decision(
        short, complete, integration_score(short, complete, metadata={}),
    )
    assert decision.reason is HypothesisOutcomeReason.SCORE_DIRECTION_MISMATCH


def test_materialization_rejects_long_score_for_short_hypothesis(short_enabled):
    _, short = _directional_hypotheses()
    complete = integration_research(short)
    decision = opportunity_materialization_decision(
        short, complete,
        integration_score(short, complete, metadata=_direction_metadata("LONG")),
    )
    assert decision.reason is HypothesisOutcomeReason.SCORE_DIRECTION_MISMATCH


def test_materialization_rejects_default_long_score():
    long, _ = _directional_hypotheses()
    complete = integration_research(long)
    decision = opportunity_materialization_decision(
        long, complete,
        integration_score(long, complete, metadata=_direction_metadata("LONG", "DEFAULT_LONG")),
    )
    assert decision.reason is HypothesisOutcomeReason.SCORE_DIRECTION_MISMATCH


def test_materialization_accepts_matching_directional_scores(short_enabled):
    for hypothesis in _directional_hypotheses():
        complete = integration_research(hypothesis)
        decision = opportunity_materialization_decision(
            hypothesis, complete, integration_score(hypothesis, complete),
        )
        assert decision.reason is HypothesisOutcomeReason.OPPORTUNITY_CREATED


def test_direction_mismatch_is_a_replenishable_reason():
    from app.e2e.stage4_replenishment import REPLENISHABLE_REASONS

    assert "SCORE_DIRECTION_MISMATCH" in REPLENISHABLE_REASONS


# --- AI-8C.3-R2.1: company-frame fundamental and expectations -----------------


def test_policy_is_v3():
    assert DIRECTIONAL_SCORING_POLICY == "ai-8c3-directional-scoring-v3"


def _scores_for(direction):
    provider = FakeProvider(output(
        fundamental={"score": 80, "rationale": "Strong revenue growth.", "supporting_evidence_ids": ["E1"]},
        expectations={"score": 30, "rationale": "Demanding expectations.", "supporting_evidence_ids": ["E2"]},
    ))
    result = OpportunityScoringService(provider).score(
        research(), evidence_coverage_score=.80, direction=direction,
        canonical_technical_input=canonical_input(ticker="PATH"),
    )
    return provider.requests[0], result


def test_company_frame_components_are_mirrored_for_short_only():
    _, long_result = _scores_for(Direction.LONG)
    _, short_result = _scores_for(Direction.SHORT)
    for name in ("fundamental", "expectations"):
        long_score = getattr(long_result.components, name).score
        short_score = getattr(short_result.components, name).score
        assert long_score is not None and short_score is not None
        assert long_score + short_score == pytest.approx(100.0)
    frame = short_result.diagnostics["direction"]["company_frame_components"]
    assert frame["fundamental"] == long_result.components.fundamental.score
    assert short_result.diagnostics["direction"]["mirrored_components"] == ["fundamental", "expectations"]
    assert long_result.diagnostics["direction"]["mirrored_components"] == []


def test_directional_components_are_not_mirrored():
    _, long_result = _scores_for(Direction.LONG)
    _, short_result = _scores_for(Direction.SHORT)
    assert long_result.components.thesis.score == short_result.components.thesis.score
    assert long_result.components.catalyst.score == short_result.components.catalyst.score


def test_strong_company_fundamentals_count_against_a_short():
    _, result = _scores_for(Direction.SHORT)
    assert result.components.fundamental.score < 50.0


def test_null_company_frame_score_stays_null():
    payload = output(expectations={"score": None, "rationale": None, "supporting_evidence_ids": []})
    mirrored = OpportunityScoringService._mirror_company_frame_components(payload, Direction.SHORT)
    assert mirrored["expectations"]["score"] is None
    assert mirrored["fundamental"]["score"] == pytest.approx(35.0)


def test_prompt_asks_company_frame_for_bipolar_components():
    request, _ = _scores_for(Direction.SHORT)
    assert "FUNDAMENTAL and EXPECTATIONS are always scored from the company's" in request.prompt
    assert "do not invert them yourself" in request.prompt
    assert "- fundamental: company frame" in request.prompt


def test_materialization_rejects_v1_directional_scores(short_enabled):
    _, short = _directional_hypotheses()
    complete = integration_research(short)
    decision = opportunity_materialization_decision(
        short, complete,
        integration_score(short, complete, metadata=_direction_metadata(
            "SHORT", policy="ai-8c3-directional-scoring-v1"
        )),
    )
    assert decision.reason is HypothesisOutcomeReason.SCORE_DIRECTION_MISMATCH


def test_short_materialization_is_suspended_even_with_valid_short_score(monkeypatch):
    import app.scanner.research_integration as integration

    monkeypatch.setattr(integration, "SHORT_MATERIALIZATION_ENABLED", False)
    _, short = _directional_hypotheses()
    complete = integration_research(short)
    decision = opportunity_materialization_decision(
        short, complete, integration_score(short, complete),
    )
    assert decision.reason is HypothesisOutcomeReason.SHORT_MATERIALIZATION_SUSPENDED


def test_long_materialization_is_unaffected_by_short_suspension(monkeypatch):
    import app.scanner.research_integration as integration

    monkeypatch.setattr(integration, "SHORT_MATERIALIZATION_ENABLED", False)
    long, _ = _directional_hypotheses()
    complete = integration_research(long)
    decision = opportunity_materialization_decision(
        long, complete, integration_score(long, complete),
    )
    assert decision.reason is HypothesisOutcomeReason.OPPORTUNITY_CREATED


def test_short_suspension_is_replenishable():
    from app.e2e.stage4_replenishment import REPLENISHABLE_REASONS

    assert "SHORT_MATERIALIZATION_SUSPENDED" in REPLENISHABLE_REASONS
