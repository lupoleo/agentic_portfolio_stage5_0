"""Primary/secondary precedence: IBKR first, Yahoo only when IBKR has nothing."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.ai.evidence_precedence import PrecedenceEvidenceProvider
from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceItem,
    EvidenceKind,
    EvidenceProvider,
    EvidenceProviderConnectionError,
    EvidenceRequest,
    EvidenceSource,
)
from app.ai.research_service import ResearchEvidence

NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)


def result(provider, status, n=1):
    items = [
        EvidenceItem(
            evidence=ResearchEvidence(evidence_id=f"{provider}-{i}", source_type="NEWS", text="t"),
            source=EvidenceSource(source_id=f"{provider}-{i}", provider=provider, source_type="NEWS", source_name=provider, retrieved_at=NOW),
            kind=EvidenceKind.NEWS,
            ticker="PST",
        )
        for i in range(n if status is not EvidenceFetchStatus.NO_DATA else 0)
    ]
    return EvidenceFetchResult(provider=provider, ticker="PST", status=status, items=items, fetched_at=NOW,
                               warnings=["IBKR has no data"] if status is EvidenceFetchStatus.NO_DATA else [])


class Fixed(EvidenceProvider):
    def __init__(self, name, answer):
        self.name, self.answer, self.calls = name, answer, 0

    @property
    def provider_name(self):
        return self.name

    def fetch(self, request):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


REQ = EvidenceRequest(ticker="PST")


def test_primary_with_data_is_used_alone():
    secondary = Fixed("YAHOO", result("YAHOO", EvidenceFetchStatus.SUCCESS))
    got = PrecedenceEvidenceProvider(Fixed("IBKR", result("IBKR", EvidenceFetchStatus.SUCCESS)), secondary).fetch(REQ)
    assert got.provider == "IBKR" and secondary.calls == 0
    assert "secondary_source" not in got.items[0].metadata


@pytest.mark.parametrize(
    "primary_answer",
    [result("IBKR", EvidenceFetchStatus.NO_DATA), EvidenceProviderConnectionError("connector down")],
)
def test_secondary_fills_and_is_declared(primary_answer):
    got = PrecedenceEvidenceProvider(Fixed("IBKR", primary_answer), Fixed("YAHOO", result("YAHOO", EvidenceFetchStatus.SUCCESS))).fetch(REQ)
    assert got.provider == "YAHOO"
    assert got.items[0].metadata["secondary_source"] is True
    assert got.items[0].metadata["primary_provider"] == "IBKR"
    assert got.metadata["secondary_source"] is True
    assert got.warnings[0].startswith("primary IBKR unavailable")


def test_without_secondary_the_primary_answer_stands():
    no_data = result("IBKR", EvidenceFetchStatus.NO_DATA)
    assert PrecedenceEvidenceProvider(Fixed("IBKR", no_data), None).fetch(REQ) is no_data
    with pytest.raises(EvidenceProviderConnectionError):
        PrecedenceEvidenceProvider(Fixed("IBKR", EvidenceProviderConnectionError("down")), None).fetch(REQ)
