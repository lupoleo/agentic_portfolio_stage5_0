from datetime import datetime, timezone

import pandas as pd

from app.ai.analyst_evidence_provider import YahooAnalystEvidenceProvider
from app.ai.canonical_technical import CanonicalTechnicalInput
from app.ai.evidence_provider import EvidenceFetchStatus, EvidenceKind, EvidenceRequest
from app.ai.evidence_semantics import deterministic_semantic_dimensions
from app.ai.fundamental_evidence_provider import YahooFundamentalEvidenceProvider
from app.ai.technical_evidence_adapter import CanonicalTechnicalEvidenceAdapter


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def request():
    return EvidenceRequest(ticker="A2A.MI", as_of=NOW)


def technical():
    return CanonicalTechnicalInput(
        ticker="A2A.MI",
        current_price=2.15,
        return_1d_pct=1.2,
        return_5d_pct=3.4,
        return_20d_pct=-2.1,
        sma20=2.10,
        sma50=2.00,
        close_vs_sma20_pct=2.38,
        close_vs_sma50_pct=7.5,
        rsi14=58.0,
        rvol=1.3,
        trend="BULLISH",
    )


def test_canonical_technical_adapter_is_deterministic_and_uses_wave_clock():
    adapter = CanonicalTechnicalEvidenceAdapter()
    first = adapter.adapt(request(), technical())
    second = adapter.adapt(request(), technical())
    assert first == second
    assert first.status is EvidenceFetchStatus.SUCCESS
    item = first.items[0]
    assert item.kind is EvidenceKind.TECHNICAL
    assert item.source.retrieved_at == NOW
    assert item.evidence.published_at == NOW
    assert "SMA20" in item.evidence.text
    assert "RSI14" in item.evidence.text
    assert "PRICE_TECHNICAL" in {
        value.value for value in deterministic_semantic_dimensions(
            item.evidence.text
        )
    }


def test_fundamental_provider_is_grounded_and_has_explicit_semantics():
    provider = YahooFundamentalEvidenceProvider(lambda ticker: {
        "totalRevenue": 123_000_000,
        "trailingEps": 1.25,
        "operatingMargins": 0.18,
        "freeCashflow": 12_000_000,
        "totalDebt": 35_000_000,
        "currentRatio": 1.4,
        "forwardPE": 14.2,
        "mostRecentQuarter": int(NOW.timestamp()),
    })
    result = provider.fetch(request())
    assert result.status is EvidenceFetchStatus.SUCCESS
    assert result.fetched_at == NOW
    item = result.items[0]
    assert item.kind is EvidenceKind.FUNDAMENTAL
    assert item.source.retrieved_at == NOW
    assert "Revenue:" in item.evidence.text
    assert "Free cash flow:" in item.evidence.text
    assert "35 million" in item.evidence.text
    assert "exact raw units: 35,000,000" in item.evidence.text
    assert "FUNDAMENTAL" in {
        value.value for value in deterministic_semantic_dimensions(
            item.evidence.text
        )
    }


def test_analyst_provider_combines_consensus_targets_and_estimates():
    provider = YahooAnalystEvidenceProvider(lambda ticker: {
        "info": {
            "recommendationKey": "buy",
            "recommendationMean": 1.8,
            "numberOfAnalystOpinions": 12,
            "forwardEps": 1.6,
        },
        "analyst_price_targets": {
            "low": 2.0,
            "mean": 2.7,
            "high": 3.2,
            "current": 2.15,
        },
        "earnings_estimate": pd.DataFrame([
            {"avg": 1.6, "low": 1.4, "high": 1.8},
        ]),
        "warnings": [],
    })
    result = provider.fetch(request())
    assert result.status is EvidenceFetchStatus.SUCCESS
    item = result.items[0]
    assert item.kind is EvidenceKind.ANALYST
    assert item.source.retrieved_at == NOW
    assert "Analyst consensus" in item.evidence.text
    assert "price target mean" in item.evidence.text
    assert "implied return versus current price" in item.evidence.text
    assert item.evidence.metadata["expectations_scorable"] is True
    assert "ANALYST_EXPECTATIONS" in {
        value.value for value in deterministic_semantic_dimensions(
            item.evidence.text
        )
    }


def test_providers_return_no_data_without_inventing_values():
    fundamental = YahooFundamentalEvidenceProvider(lambda ticker: {})
    analyst = YahooAnalystEvidenceProvider(lambda ticker: {"warnings": []})
    for result in (fundamental.fetch(request()), analyst.fetch(request())):
        assert result.status is EvidenceFetchStatus.NO_DATA
        assert result.items == []
        assert result.warnings


def test_fundamental_provider_preserves_billion_scale_exactly():
    provider = YahooFundamentalEvidenceProvider(lambda ticker: {
        "financialCurrency": "USD",
        "totalDebt": 7_475_100_160,
    })
    text = provider.fetch(request()).items[0].evidence.text
    assert "USD 7.4751 billion" in text
    assert "exact raw units: 7,475,100,160" in text
    assert "74.751 billion" not in text


def test_analyst_provider_does_not_mark_targets_scorable_without_current_price():
    provider = YahooAnalystEvidenceProvider(lambda ticker: {
        "info": {"recommendationKey": "hold", "targetMeanPrice": 14.0},
        "warnings": [],
    })
    item = provider.fetch(request()).items[0]
    assert item.evidence.metadata["expectations_scorable"] is False
