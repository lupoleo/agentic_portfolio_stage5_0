import pytest

from app.ai.opportunity_score_calculator import OpportunityScoreCalculator
from app.ai.opportunity_score_models import OpportunityScoringProfile
from app.ai.research_models import EvidenceQuality


CALC = OpportunityScoreCalculator()


def components(**overrides):
    values = {
        "thesis_score": 80.0,
        "catalyst_score": 70.0,
        "fundamental_score": 60.0,
        "technical_score": 90.0,
        "expectations_score": 50.0,
    }
    values.update(overrides)
    return values


def test_standard_weighted_raw_score():
    result = CALC.calculate(
        component_scores=components(),
        evidence_quality=EvidenceQuality.HIGH,
        evidence_coverage_score=1.0,
        research_confidence=1.0,
    )
    # 80*.25 + 70*.25 + 60*.20 + 90*.15 + 50*.15 = 70.5
    assert result.raw_score == pytest.approx(70.5)
    assert result.available_weight == 1.0
    assert result.component_completeness == 1.0


def test_missing_component_is_renormalized_not_replaced_with_neutral():
    result = CALC.calculate(
        component_scores=components(fundamental_score=None),
        evidence_quality=EvidenceQuality.HIGH,
        evidence_coverage_score=1.0,
        research_confidence=1.0,
    )
    expected = (80*.25 + 70*.25 + 90*.15 + 50*.15) / .80
    assert result.raw_score == pytest.approx(expected)
    assert "fundamental_score" not in result.normalized_weights
    assert sum(result.normalized_weights.values()) == pytest.approx(1.0)


def test_missing_component_reduces_confidence_via_weighted_completeness():
    result = CALC.calculate(
        component_scores=components(fundamental_score=None),
        evidence_quality=EvidenceQuality.HIGH,
        evidence_coverage_score=1.0,
        research_confidence=1.0,
    )
    assert result.component_completeness == pytest.approx(.80)
    assert result.score_confidence == pytest.approx(.80)


def test_confidence_is_deterministic_product_of_governance_inputs():
    result = CALC.calculate(
        component_scores=components(),
        evidence_quality=EvidenceQuality.MEDIUM,
        evidence_coverage_score=.80,
        research_confidence=.70,
    )
    assert result.score_confidence == pytest.approx(.70 * .75 * .80)


def test_low_quality_has_stronger_confidence_penalty():
    high = CALC.calculate(
        component_scores=components(), evidence_quality=EvidenceQuality.HIGH,
        evidence_coverage_score=1, research_confidence=1)
    low = CALC.calculate(
        component_scores=components(), evidence_quality=EvidenceQuality.LOW,
        evidence_coverage_score=1, research_confidence=1)
    assert high.score_confidence == 1.0
    assert low.score_confidence == .5


def test_adjusted_score_regresses_positive_score_toward_50():
    result = CALC.calculate(
        component_scores=components(),
        evidence_quality=EvidenceQuality.HIGH,
        evidence_coverage_score=.5,
        research_confidence=1.0,
    )
    assert result.raw_score == 70.5
    assert result.confidence_adjusted_score == pytest.approx(60.25)


def test_adjusted_score_regresses_negative_score_toward_50():
    result = CALC.calculate(
        component_scores={k: 20.0 for k in components()},
        evidence_quality=EvidenceQuality.HIGH,
        evidence_coverage_score=.5,
        research_confidence=1.0,
    )
    assert result.raw_score == 20.0
    assert result.confidence_adjusted_score == pytest.approx(35.0)


def test_no_components_returns_not_calculable_values():
    result = CALC.calculate(
        component_scores={k: None for k in components()},
        evidence_quality=EvidenceQuality.HIGH,
        evidence_coverage_score=1.0,
        research_confidence=1.0,
    )
    assert result.raw_score is None
    assert result.score_confidence == 0.0
    assert result.confidence_adjusted_score is None
    assert result.normalized_weights == {}


def test_component_out_of_range_fails_closed():
    with pytest.raises(ValueError, match="technical_score"):
        CALC.calculate(
            component_scores=components(technical_score=101),
            evidence_quality=EvidenceQuality.HIGH,
            evidence_coverage_score=1,
            research_confidence=1,
        )


def test_unknown_component_fails_closed():
    bad = components()
    bad["portfolio_fit_score"] = 90
    with pytest.raises(ValueError, match="Unknown opportunity score components"):
        CALC.calculate(
            component_scores=bad,
            evidence_quality=EvidenceQuality.HIGH,
            evidence_coverage_score=1,
            research_confidence=1,
        )


@pytest.mark.parametrize("name,value", [
    ("evidence_coverage_score", -0.01),
    ("evidence_coverage_score", 1.01),
    ("research_confidence", -0.01),
    ("research_confidence", 1.01),
])
def test_confidence_inputs_are_bounded(name, value):
    kwargs = dict(
        component_scores=components(),
        evidence_quality=EvidenceQuality.HIGH,
        evidence_coverage_score=1.0,
        research_confidence=1.0,
    )
    kwargs[name] = value
    with pytest.raises(ValueError, match=name):
        CALC.calculate(**kwargs)


def test_event_driven_profile_is_supported_but_not_reweighted_prematurely():
    standard = CALC.calculate(
        component_scores=components(),
        evidence_quality=EvidenceQuality.HIGH,
        evidence_coverage_score=1,
        research_confidence=1,
        scoring_profile=OpportunityScoringProfile.STANDARD,
    )
    event = CALC.calculate(
        component_scores=components(),
        evidence_quality=EvidenceQuality.HIGH,
        evidence_coverage_score=1,
        research_confidence=1,
        scoring_profile=OpportunityScoringProfile.EVENT_DRIVEN,
    )
    assert event.raw_score == standard.raw_score
