from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceItem,
    EvidenceKind,
    EvidenceProvider,
    EvidenceProviderConnectionError,
    EvidenceProviderError,
    EvidenceProviderResponseError,
    EvidenceProviderTimeoutError,
    EvidenceRequest,
    EvidenceSource,
)
from app.ai.research_service import ResearchEvidence


NOW = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)


def source():
    return EvidenceSource(
        source_id="SRC-001",
        provider="TEST",
        source_type="MARKET_DATA",
        source_name="Synthetic Source",
        source_url="https://example.invalid/path",
        retrieved_at=NOW,
        published_at=NOW,
    )


def research_evidence():
    return ResearchEvidence(
        evidence_id="EVID-001",
        source_type="MARKET_DATA",
        text="Synthetic market evidence.",
        published_at=NOW,
    )


def test_request_normalizes_ticker():
    request = EvidenceRequest(ticker=" path ")
    assert request.ticker == "PATH"


def test_request_rejects_blank_ticker():
    with pytest.raises(ValidationError):
        EvidenceRequest(ticker="   ")


def test_request_defaults_are_safe():
    request = EvidenceRequest(ticker="PATH")
    assert request.kinds == []
    assert request.max_items == 20
    assert request.metadata == {}


def test_request_max_items_is_bounded():
    with pytest.raises(ValidationError):
        EvidenceRequest(ticker="PATH", max_items=0)
    with pytest.raises(ValidationError):
        EvidenceRequest(ticker="PATH", max_items=201)


def test_source_requires_identity_fields():
    with pytest.raises(ValidationError):
        EvidenceSource(
            source_id="",
            provider="TEST",
            source_type="NEWS",
            source_name="Source",
            retrieved_at=NOW,
        )


def test_evidence_item_links_normalized_evidence_and_source():
    item = EvidenceItem(
        evidence=research_evidence(),
        source=source(),
        kind=EvidenceKind.MARKET,
        ticker="path",
    )
    assert item.ticker == "PATH"
    assert item.evidence.evidence_id == "EVID-001"
    assert item.source.source_id == "SRC-001"


def test_fetch_result_can_represent_success():
    item = EvidenceItem(
        evidence=research_evidence(),
        source=source(),
        kind=EvidenceKind.MARKET,
        ticker="PATH",
    )
    result = EvidenceFetchResult(
        provider="TEST",
        ticker="path",
        status=EvidenceFetchStatus.SUCCESS,
        items=[item],
        fetched_at=NOW,
    )
    assert result.ticker == "PATH"
    assert len(result.items) == 1


def test_fetch_result_can_represent_no_data_without_fake_evidence():
    result = EvidenceFetchResult(
        provider="TEST",
        ticker="PATH",
        status=EvidenceFetchStatus.NO_DATA,
        items=[],
        warnings=["No usable evidence returned"],
        fetched_at=NOW,
    )
    assert result.items == []
    assert result.status == EvidenceFetchStatus.NO_DATA


def test_provider_is_abstract():
    with pytest.raises(TypeError):
        EvidenceProvider()


def test_provider_contract_is_implementable():
    class FakeProvider(EvidenceProvider):
        @property
        def provider_name(self):
            return "FAKE"

        def fetch(self, request):
            return EvidenceFetchResult(
                provider=self.provider_name,
                ticker=request.ticker,
                status=EvidenceFetchStatus.NO_DATA,
                items=[],
                fetched_at=NOW,
            )

    provider = FakeProvider()
    result = provider.fetch(EvidenceRequest(ticker="path"))
    assert provider.provider_name == "FAKE"
    assert result.ticker == "PATH"


def test_error_hierarchy():
    assert issubclass(EvidenceProviderConnectionError, EvidenceProviderError)
    assert issubclass(EvidenceProviderTimeoutError, EvidenceProviderError)
    assert issubclass(EvidenceProviderResponseError, EvidenceProviderError)


def test_extra_fields_are_forbidden():
    with pytest.raises(ValidationError):
        EvidenceRequest(ticker="PATH", unexpected=True)
