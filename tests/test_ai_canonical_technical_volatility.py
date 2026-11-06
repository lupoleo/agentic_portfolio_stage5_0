"""AI-8C.3-R1: Stage-2 volatility in the canonical technical contract."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import inspect
import math

import numpy as np
import pandas as pd
import pytest

from app.ai.canonical_technical import (
    CANONICAL_TECHNICAL_CONTRACT_VERSION,
    CanonicalTechnicalInput,
    canonical_technical_from_history,
)
from app.ai.evidence_provider import EvidenceFetchStatus, EvidenceRequest
from app.ai.opportunity_scoring_service import OpportunityScoringService
from app.ai.research_validator import ResearchCoverageValidator
from app.ai.technical_evidence_adapter import (
    TECHNICAL_EVIDENCE_ADAPTER_VERSION,
    CanonicalTechnicalEvidenceAdapter,
)
from app.analysis.technical import analyze_technical


AS_OF = datetime(2026, 9, 29, 16, 0, tzinfo=timezone.utc)


def _history(sessions: int = 120) -> pd.DataFrame:
    index = pd.bdate_range("2026-03-02", periods=sessions)
    steps = np.array([0.012, -0.007, 0.004, -0.010, 0.009, 0.002, -0.003])
    returns = np.resize(steps, sessions)
    close = 100.0 * np.cumprod(1.0 + returns)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.resize(np.array([1_000_000.0, 1_200_000.0, 900_000.0]), sessions),
        },
        index=index,
    )


def _input(**overrides) -> CanonicalTechnicalInput:
    data = dict(
        ticker="DHL.DE",
        current_price=41.2,
        return_1d_pct=0.4,
        return_5d_pct=-1.1,
        return_20d_pct=2.3,
        sma20=40.8,
        sma50=40.1,
        close_vs_sma20_pct=0.98,
        close_vs_sma50_pct=2.74,
        rsi14=54.2,
        rvol=0.87,
        trend="BULLISH",
        volatility_20d_pct=23.4567,
    )
    data.update(overrides)
    return CanonicalTechnicalInput(**data)


def test_contract_version_is_explicit_and_bumped():
    assert CANONICAL_TECHNICAL_CONTRACT_VERSION == "ai-8c3-canonical-technical-v2"
    assert _input().contract_version == CANONICAL_TECHNICAL_CONTRACT_VERSION


def test_canonical_input_carries_stage2_volatility_exactly():
    history = _history()
    canonical = canonical_technical_from_history("dhl.de", history)
    stage2 = analyze_technical("DHL.DE", history)

    assert canonical.volatility_20d_pct == pytest.approx(stage2.volatility_20d_pct, abs=0.0)
    assert canonical.volatility_20d_pct > 0


def test_undefined_volatility_is_none_not_fabricated(monkeypatch):
    import app.ai.canonical_technical as module

    original = module.analyze_technical

    def nan_volatility(ticker, history):
        return replace(original(ticker, history), volatility_20d_pct=float("nan"))

    monkeypatch.setattr(module, "analyze_technical", nan_volatility)
    canonical = canonical_technical_from_history("DHL.DE", _history())
    assert canonical.volatility_20d_pct is None


@pytest.mark.parametrize("value", [-0.1, math.inf, math.nan])
def test_invalid_volatility_is_rejected_by_the_contract(value):
    with pytest.raises(ValueError):
        _input(volatility_20d_pct=value)


def test_v1_shaped_construction_remains_valid_with_unknown_volatility():
    value = _input(volatility_20d_pct=None)
    assert value.volatility_20d_pct is None


def _adapt(value: CanonicalTechnicalInput):
    request = EvidenceRequest(ticker="DHL.DE", as_of=AS_OF)
    return CanonicalTechnicalEvidenceAdapter().adapt(request, value)


def test_technical_evidence_states_volatility_and_versions():
    result = _adapt(_input())
    assert result.status is EvidenceFetchStatus.SUCCESS
    item = result.items[0]
    assert "20-session annualized volatility: 23.4567%." in item.evidence.text
    assert TECHNICAL_EVIDENCE_ADAPTER_VERSION == "stage4-research-technical-v2"
    assert item.metadata["adapter_version"] == TECHNICAL_EVIDENCE_ADAPTER_VERSION
    assert result.metadata["adapter_version"] == TECHNICAL_EVIDENCE_ADAPTER_VERSION
    assert (
        item.source.metadata["canonical_technical_contract"]
        == CANONICAL_TECHNICAL_CONTRACT_VERSION
    )


def test_technical_evidence_omits_unknown_volatility():
    text = _adapt(_input(volatility_20d_pct=None)).items[0].evidence.text
    assert "volatility" not in text.lower()


def test_technical_evidence_remains_deterministic():
    assert _adapt(_input()).items[0].evidence.evidence_id == _adapt(_input()).items[0].evidence.evidence_id


def test_technical_evidence_is_recognized_as_technical_coverage():
    text = _adapt(_input()).items[0].evidence.text.lower()
    assert "volatility" in ResearchCoverageValidator._TECHNICAL_TERMS
    assert "volatility" in text


class _Research:
    ticker = "DHL.DE"
    technical_context = None


def test_scoring_features_expose_volatility_and_contract():
    features = OpportunityScoringService._technical_features_for_scoring(_Research(), _input())
    assert features["volatility_20d_pct"] == pytest.approx(23.4567)
    assert features["technical_input_contract"] == CANONICAL_TECHNICAL_CONTRACT_VERSION

    lines = OpportunityScoringService._format_canonical_technical_features(features)
    assert "- volatility_20d_pct: 23.4567" in lines
    assert f"- technical_input_contract: {CANONICAL_TECHNICAL_CONTRACT_VERSION}" in lines


def test_volatility_does_not_change_the_deterministic_technical_base_score():
    with_volatility = OpportunityScoringService._technical_features_for_scoring(_Research(), _input())
    without = OpportunityScoringService._technical_features_for_scoring(
        _Research(), _input(volatility_20d_pct=None)
    )
    assert OpportunityScoringService._deterministic_technical_base_score(
        with_volatility
    ) == OpportunityScoringService._deterministic_technical_base_score(without)


def test_default_scoring_prompt_version_is_bumped():
    parameter = inspect.signature(OpportunityScoringService.__init__).parameters["prompt_version"]
    # R1 introduced v12; later revisions (R2: v13) must keep it superseded.
    version = int(parameter.default.split("-v", 1)[1].split("-", 1)[0])
    assert version >= 12
