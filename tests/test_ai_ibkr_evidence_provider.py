"""IBKR earnings-event and theme evidence providers (responses recorded 2026-11-06, trimmed)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.ai.evidence_provider import (
    EvidenceFetchStatus,
    EvidenceKind,
    EvidenceProviderConnectionError,
    EvidenceRequest,
)
from app.ai.ibkr_evidence_provider import IbkrEventEvidenceProvider, IbkrThemeEvidenceProvider
from app.broker.ibkr_client import BrokerClientError, BrokerNoDataError, ReadOnlyIbkrClient

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
MSFT = 272093

MSFT_RECAP = {
    "id": "ce6fa2dc-cac8-4991-85e8-3beea11aea5e",
    "type": "recap",
    "isin": "US5949181045",
    "contract_id": MSFT,
    "ticker": "MSFT",
    "title": "Azure Surges 43%, Cloud Revenue Hits $59.3B, EPS Beats on AI Execution and Anthropic Gain",
    "created_at": "2026-07-29T20:48:25Z",
    "reporting_date": "2026-07-29T20:05:22Z",
    "earnings": {
        "fiscal_period": "Q4",
        "fiscal_year": "2026",
        "estimated_eps": 4.23,
        "estimated_revenue": 87612420200,
        "actual_eps": 4.74,
        "actual_revenue": 90007000000,
    },
    "kpis": [{"value": "+43% YoY (Q4 FY2026)", "title": "Azure Revenue Growth", "rating": "positive"}],
    "key_metrics": "Q4 FY2026 GAAP EPS of $4.81 (+32% YoY) on revenue of $90.0B (+18% YoY).",
    "guidance_update": "Commercial remaining performance obligation surged 84% YoY to $678 billion.",
    "risk_assessment_update": ["Intelligent Cloud cost of revenue rose 44% in FY2026 vs. 30% revenue growth."],
    "peer_comparison_sector_context": ["Azure growth of 43% outpaced AWS (~17%) and Google Cloud (~28%)."],
}
MSFT_PREVIEW = {
    "id": "a99e40d5-ec72-4f30-86b3-836627b3476f",
    "type": "preview",
    "isin": "US5949181045",
    "contract_id": MSFT,
    "title": "AI Monetization Inflection, Azure Margin Resilience, CapEx Efficiency Test Ahead",
    "created_at": "2026-10-25T05:03:30Z",
    "reporting_date": "2026-10-28T20:05:00Z",
}


class Transport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, arguments):
        self.calls.append((tool, dict(arguments)))
        handler = self.responses[tool]
        result = handler(arguments) if callable(handler) else handler
        if isinstance(result, Exception):
            raise result
        return result


def insights_transport(summaries, bodies):
    return Transport(
        {
            "search_earnings_insights": {"insights": summaries},
            "get_earnings_insight": lambda a: bodies[a["id"]],
        }
    )


def provider(transport, cls=IbkrEventEvidenceProvider, **kwargs):
    return cls(ReadOnlyIbkrClient(transport), clock=lambda: NOW, **kwargs)


def req(**meta):
    return EvidenceRequest(ticker="MSFT", metadata=meta)


def test_newest_insights_become_event_evidence():
    t = insights_transport(
        [{"id": MSFT_PREVIEW["id"]}, {"id": MSFT_RECAP["id"]}],
        {MSFT_PREVIEW["id"]: MSFT_PREVIEW, MSFT_RECAP["id"]: MSFT_RECAP},
    )
    result = provider(t).fetch(req(conid=MSFT))
    assert result.status is EvidenceFetchStatus.SUCCESS
    assert [i.kind for i in result.items] == [EvidenceKind.EVENT, EvidenceKind.EVENT]
    recap = result.items[1]
    assert recap.source.source_id == f"ibkr-insight:{MSFT_RECAP['id']}"
    assert recap.metadata["source"] == "IBKR" and recap.metadata["insight_type"] == "recap"
    assert recap.evidence.published_at == datetime(2026, 7, 29, 20, 48, 25, tzinfo=timezone.utc)
    text = recap.evidence.text
    assert "EPS estimate 4.23, actual 4.74" in text
    assert "Guidance: Commercial remaining performance obligation" in text
    assert "KPI Azure Revenue Growth: +43% YoY (Q4 FY2026) (positive)." in text
    assert t.calls[0] == ("search_earnings_insights", {"contract_ids": [MSFT], "size": 2})


def test_insight_of_another_instrument_is_dropped():
    wrong = {**MSFT_RECAP, "id": "x", "contract_id": 4815747, "isin": "US67066G1040"}
    t = insights_transport([{"id": "x"}, {"id": MSFT_PREVIEW["id"]}], {"x": wrong, MSFT_PREVIEW["id"]: MSFT_PREVIEW})
    result = provider(t).fetch(req(conid=MSFT, isin="US5949181045"))
    assert result.status is EvidenceFetchStatus.PARTIAL
    assert [i.metadata["insight_id"] for i in result.items] == [MSFT_PREVIEW["id"]]
    assert "belongs to another instrument" in result.warnings[0]


def test_no_insight_is_no_data_never_invented():
    result = provider(insights_transport([], {})).fetch(req(conid=211008268))
    assert result.status is EvidenceFetchStatus.NO_DATA and result.items == []
    assert "no earnings insight" in result.warnings[0]


def test_unresolved_instrument_makes_no_insight_call():
    t = Transport({"search_contracts": {"results": []}})
    result = provider(t).fetch(req(isin="IT0003796171"))
    assert result.status is EvidenceFetchStatus.NO_DATA
    assert [c[0] for c in t.calls] == ["search_contracts"]


def test_conid_is_resolved_from_isin():
    t = insights_transport([{"id": MSFT_RECAP["id"]}], {MSFT_RECAP["id"]: MSFT_RECAP})
    t.responses["search_contracts"] = {
        "results": [
            {
                "underlying_contract_id": MSFT,
                "exchange": "NASDAQ",
                "symbol": "MSFT",
                "country_code": "US",
                "sections": [{"security_type": "STK"}],
            }
        ]
    }
    result = provider(t).fetch(req(isin="US5949181045", exchange="NASDAQ"))
    assert result.metadata["conid"] == MSFT and len(result.items) == 1


def test_as_of_bounds_the_search():
    t = insights_transport([], {})
    request = EvidenceRequest(ticker="MSFT", as_of=datetime(2026, 9, 1, tzinfo=timezone.utc), metadata={"conid": MSFT})
    provider(t).fetch(request)
    assert t.calls[0][1]["end"] == "2026-09-01T00:00:00Z"


def test_transport_failure_is_a_provider_error():
    t = Transport({"search_earnings_insights": BrokerClientError("connector unreachable")})
    with pytest.raises(EvidenceProviderConnectionError):
        provider(t).fetch(req(conid=MSFT))


THEMES = {
    "linked_themes": [
        {
            "name": "Satellite Communications",
            "linked_companies": [{"symbol": "IRDM", "contract_id": 69195736}, {"symbol": "VSAT", "contract_id": 6746554}],
        },
        {"name": "Space Exploration", "linked_companies": [{"symbol": "LUNR", "contract_id": 613910262}]},
    ]
}


def test_themes_become_one_context_item():
    t = Transport({"get_company_themes": THEMES})
    result = provider(t, IbkrThemeEvidenceProvider).fetch(EvidenceRequest(ticker="SPCX", metadata={"conid": 890493863}))
    assert result.status is EvidenceFetchStatus.SUCCESS
    (item,) = result.items
    assert item.kind is EvidenceKind.OTHER and item.evidence.source_type == "THEME"
    assert item.evidence.text.splitlines()[0] == "Theme Satellite Communications — top peers: IRDM, VSAT"
    assert item.metadata["themes"] == ["Satellite Communications", "Space Exploration"]


@pytest.mark.parametrize("answer", [{"linked_themes": []}, BrokerNoDataError("No data is available.")])
def test_etf_or_uncovered_company_has_no_theme_data(answer):
    t = Transport({"get_company_themes": answer})
    result = provider(t, IbkrThemeEvidenceProvider).fetch(EvidenceRequest(ticker="VHYL", metadata={"conid": 128831209}))
    assert result.status is EvidenceFetchStatus.NO_DATA and result.items == []


def test_providers_only_use_read_only_tools():
    t = insights_transport([{"id": MSFT_RECAP["id"]}], {MSFT_RECAP["id"]: MSFT_RECAP})
    t.responses["get_company_themes"] = THEMES
    provider(t).fetch(req(conid=MSFT))
    provider(t, IbkrThemeEvidenceProvider).fetch(req(conid=MSFT))
    from app.broker.ibkr_client import READ_ONLY_TOOLS

    assert {tool for tool, _ in t.calls} <= READ_ONLY_TOOLS
