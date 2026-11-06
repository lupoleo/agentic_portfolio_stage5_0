from app.ai.opportunity_scoring_service import OpportunityScoringService


def _payload():
    return {
        "thesis": {
            "score": 72.0,
            "rationale": "Grounded thesis.",
            "supporting_evidence_ids": ["E1"],
        },
        "catalyst": {
            "score": 65.0,
            "rationale": "Grounded catalyst.",
            "supporting_evidence_ids": ["E2"],
        },
        "fundamental": {
            "score": None,
            "rationale": "Insufficient fundamental evidence.",
            "supporting_evidence_ids": ["E1"],
        },
        "technical": {
            "score": 61.0,
            "rationale": "Grounded technical context.",
            "supporting_evidence_ids": ["E1"],
        },
        "expectations": {
            "score": None,
            "rationale": "Expectations remain unknown.",
            "supporting_evidence_ids": [],
        },
        "positive_factors": ["positive"],
        "negative_factors": ["negative"],
        "uncertainty_factors": ["uncertain"],
    }


def test_null_score_discards_rationale_and_evidence():
    normalized = OpportunityScoringService._normalize_component_null_invariants(
        _payload()
    )
    assert normalized["fundamental"] == {
        "score": None,
        "rationale": None,
        "supporting_evidence_ids": [],
    }
    assert normalized["expectations"] == {
        "score": None,
        "rationale": None,
        "supporting_evidence_ids": [],
    }


def test_scored_components_are_not_modified():
    payload = _payload()
    normalized = OpportunityScoringService._normalize_component_null_invariants(
        payload
    )
    assert normalized["thesis"] == payload["thesis"]
    assert normalized["technical"] == payload["technical"]


def test_normalizer_does_not_mutate_input_mapping():
    payload = _payload()
    OpportunityScoringService._normalize_component_null_invariants(payload)
    assert payload["fundamental"]["rationale"] == "Insufficient fundamental evidence."
    assert payload["fundamental"]["supporting_evidence_ids"] == ["E1"]


def test_factor_lists_are_preserved():
    payload = _payload()
    normalized = OpportunityScoringService._normalize_component_null_invariants(
        payload
    )
    assert normalized["positive_factors"] == ["positive"]
    assert normalized["negative_factors"] == ["negative"]
    assert normalized["uncertainty_factors"] == ["uncertain"]


def test_transport_accepts_null_score_with_explanatory_rationale():
    # Import private transport schema only to verify the provider boundary.
    from app.ai.opportunity_scoring_service import (
        _OpportunityComponentScoringTransport,
    )

    transport = _OpportunityComponentScoringTransport.model_validate(_payload())
    assert transport.fundamental.score is None
    assert transport.fundamental.rationale == "Insufficient fundamental evidence."


def test_canonical_contract_accepts_normalized_payload():
    from app.ai.opportunity_scoring_ai_models import (
        OpportunityComponentScoringOutput,
    )

    normalized = OpportunityScoringService._normalize_component_null_invariants(
        _payload()
    )
    canonical = OpportunityComponentScoringOutput.model_validate(normalized)
    assert canonical.fundamental.score is None
    assert canonical.fundamental.rationale is None
    assert canonical.expectations.score is None


def test_canonical_model_itself_remains_strict():
    import pytest
    from app.ai.opportunity_scoring_ai_models import ComponentAssessment

    with pytest.raises(ValueError, match="unscored component cannot contain a rationale"):
        ComponentAssessment.model_validate(
            {
                "score": None,
                "rationale": "LLM explanation",
                "supporting_evidence_ids": [],
            }
        )
