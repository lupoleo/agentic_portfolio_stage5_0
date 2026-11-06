"""IBKR evidence providers (architecture §5.3, §5D): earnings events and themes.

Both providers read the IBKR connector through the read-only client, so they
can never reach a write tool. They identify the instrument by IBKR contract
id: ``request.metadata["conid"]`` when the caller already has it (positions,
screener rows), else ``isin`` (+ optional ``exchange``) resolved through the
ISIN-keyed ``ContractResolver``. A ticker alone is never trusted.

* ``IbkrEventEvidenceProvider`` turns the newest earnings previews/recaps into
  ``EVENT`` evidence (estimates vs actuals, key metrics, guidance, risk
  updates). The insight's own ISIN/contract id must match the instrument;
  a mismatching insight is dropped, not reused.
* ``IbkrThemeEvidenceProvider`` returns one ``OTHER`` context item with the
  company's sectors/trends and ranked peers. It is research context only.

No data → ``NO_DATA`` with a warning; the channel stays unavailable and is
never filled with invented text.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceItem,
    EvidenceKind,
    EvidenceProvider,
    EvidenceProviderConnectionError,
    EvidenceProviderResponseError,
    EvidenceRequest,
    EvidenceSource,
)
from app.ai.research_service import ResearchEvidence
from app.broker.contract_resolver import ContractResolver
from app.broker.ibkr_client import BrokerClientError, BrokerNoDataError, BrokerResponseError, ReadOnlyIbkrClient

Clock = Callable[[], datetime]

EVENT_PROVIDER_NAME = "IBKR_EARNINGS_INSIGHTS"
THEME_PROVIDER_NAME = "IBKR_COMPANY_THEMES"
SOURCE_LABEL = "IBKR"
MAX_SECTION_LINES = 5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class _IbkrProviderBase(EvidenceProvider):
    def __init__(
        self,
        client: ReadOnlyIbkrClient,
        *,
        resolver: ContractResolver | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._client = client
        self._resolver = resolver or ContractResolver(client)
        self._clock = clock

    def _conid(self, request: EvidenceRequest) -> tuple[int | None, str | None]:
        meta = request.metadata
        conid = meta.get("conid")
        if isinstance(conid, int) and not isinstance(conid, bool) and conid > 0:
            return conid, meta.get("isin")
        isin = meta.get("isin")
        if isinstance(isin, str) and isin.strip():
            resolved = self._call(lambda: self._resolver.resolve(isin, exchange=meta.get("exchange"), symbol=meta.get("symbol")))
            if resolved is not None:
                return resolved.conid, resolved.isin
        return None, isin if isinstance(isin, str) else None

    def _call(self, fn, *, empty: Any = None):
        try:
            return fn()
        except BrokerNoDataError:
            if empty is None:
                raise
            return empty
        except BrokerResponseError as exc:
            raise EvidenceProviderResponseError(str(exc)) from exc
        except BrokerClientError as exc:
            raise EvidenceProviderConnectionError(str(exc)) from exc

    def _no_data(self, request: EvidenceRequest, fetched_at: datetime, warning: str, **meta) -> EvidenceFetchResult:
        return EvidenceFetchResult(
            provider=self.provider_name,
            ticker=request.ticker,
            status=EvidenceFetchStatus.NO_DATA,
            warnings=[warning],
            fetched_at=fetched_at,
            metadata={"source": SOURCE_LABEL, **meta},
        )


class IbkrEventEvidenceProvider(_IbkrProviderBase):
    """Earnings previews and recaps as EVENT evidence."""

    def __init__(self, client: ReadOnlyIbkrClient, *, max_insights: int = 2, **kwargs) -> None:
        if max_insights < 1:
            raise ValueError("max_insights must be at least 1")
        super().__init__(client, **kwargs)
        self._max_insights = max_insights

    @property
    def provider_name(self) -> str:
        return EVENT_PROVIDER_NAME

    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        fetched_at = self._clock()
        conid, isin = self._conid(request)
        if conid is None:
            return self._no_data(request, fetched_at, "instrument not resolved to an IBKR contract id")

        limit = min(self._max_insights, request.max_items)
        args: dict[str, Any] = {"contract_ids": [conid], "size": limit}
        if request.as_of is not None:
            args["end"] = request.as_of.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        listing = self._call(lambda: self._client.call("search_earnings_insights", args), empty={})
        summaries = [s for s in listing.get("insights") or [] if isinstance(s, dict) and s.get("id")]
        if not summaries:
            return self._no_data(request, fetched_at, "IBKR has no earnings insight for this instrument", conid=conid)

        items: list[EvidenceItem] = []
        warnings: list[str] = []
        for summary in summaries[:limit]:
            body = self._call(lambda: self._client.call("get_earnings_insight", {"id": summary["id"]}))
            if body.get("contract_id") not in (None, conid) or (isin and body.get("isin") not in (None, isin)):
                warnings.append(f"insight {summary['id']} belongs to another instrument; dropped")
                continue
            text = _insight_text(body)
            if not text:
                warnings.append(f"insight {summary['id']} has no usable content")
                continue
            items.append(self._item(request, conid, body, text, fetched_at))

        if not items:
            return self._no_data(request, fetched_at, "; ".join(warnings) or "no usable insight", conid=conid)
        return EvidenceFetchResult(
            provider=self.provider_name,
            ticker=request.ticker,
            status=EvidenceFetchStatus.PARTIAL if warnings else EvidenceFetchStatus.SUCCESS,
            items=items,
            warnings=warnings,
            fetched_at=fetched_at,
            metadata={"source": SOURCE_LABEL, "conid": conid},
        )

    def _item(self, request, conid, body, text, fetched_at) -> EvidenceItem:
        insight_id = str(body["id"])
        reporting = _parse_ts(body.get("reporting_date"))
        published = _parse_ts(body.get("created_at")) or reporting
        meta = {
            "source": SOURCE_LABEL,
            "conid": conid,
            "isin": body.get("isin"),
            "insight_id": insight_id,
            "insight_type": body.get("type"),
            "reporting_date": body.get("reporting_date"),
        }
        source_id = f"ibkr-insight:{insight_id}"
        return EvidenceItem(
            evidence=ResearchEvidence(
                evidence_id=source_id,
                source_type="EVENT",
                text=text,
                published_at=published,
                metadata={"provider": self.provider_name, "source_id": source_id, **meta},
            ),
            source=EvidenceSource(
                source_id=source_id,
                provider=self.provider_name,
                source_type="EVENT",
                source_name=f"IBKR earnings {body.get('type') or 'insight'}",
                retrieved_at=fetched_at,
                published_at=published,
                metadata=meta,
            ),
            kind=EvidenceKind.EVENT,
            ticker=request.ticker,
            metadata=meta,
        )


def _insight_text(body: dict[str, Any]) -> str:
    lines: list[str] = []
    kind = (body.get("type") or "insight").upper()
    if body.get("title"):
        lines.append(f"Earnings {kind}: {body['title']} (reporting date {body.get('reporting_date', 'n/a')}).")
    earnings = body.get("earnings") or {}
    if isinstance(earnings, dict) and earnings:
        period = " ".join(str(earnings[k]) for k in ("fiscal_period", "fiscal_year") if earnings.get(k))
        parts = []
        for label, est, act in (("EPS", "estimated_eps", "actual_eps"), ("revenue", "estimated_revenue", "actual_revenue")):
            if earnings.get(est) is not None or earnings.get(act) is not None:
                parts.append(f"{label} estimate {earnings.get(est, 'n/a')}, actual {earnings.get(act, 'n/a')}")
        if parts:
            lines.append(f"{period}: " + "; ".join(parts) + ".")
    for key, label in (("key_metrics", "Key metrics"), ("guidance_update", "Guidance")):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{label}: {value.strip()}")
    for key, label in (("risk_assessment_update", "Risk update"), ("peer_comparison_sector_context", "Peer context")):
        values = [v.strip() for v in body.get(key) or [] if isinstance(v, str) and v.strip()]
        lines.extend(f"{label}: {v}" for v in values[:MAX_SECTION_LINES])
    kpis = [k for k in body.get("kpis") or [] if isinstance(k, dict) and k.get("title")]
    for k in kpis[:MAX_SECTION_LINES]:
        lines.append(f"KPI {k['title']}: {k.get('value', 'n/a')} ({k.get('rating', 'unrated')}).")
    return "\n".join(lines)


class IbkrThemeEvidenceProvider(_IbkrProviderBase):
    """Sectors, trends and ranked peers as research context (never scored directly)."""

    def __init__(self, client: ReadOnlyIbkrClient, *, max_themes: int = 5, max_peers: int = 3, **kwargs) -> None:
        super().__init__(client, **kwargs)
        self._max_themes = max_themes
        self._max_peers = max_peers

    @property
    def provider_name(self) -> str:
        return THEME_PROVIDER_NAME

    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        fetched_at = self._clock()
        conid, _ = self._conid(request)
        if conid is None:
            return self._no_data(request, fetched_at, "instrument not resolved to an IBKR contract id")
        result = self._call(
            lambda: self._client.call(
                "get_company_themes",
                {"contract_id": conid, "max_themes": self._max_themes, "max_companies": self._max_peers},
            ),
            empty={},  # the connector answers funds/ETFs with "No data is available"
        )
        themes = [t for t in result.get("linked_themes") or [] if isinstance(t, dict) and t.get("name")]
        if not themes:
            return self._no_data(request, fetched_at, "IBKR has no theme data for this instrument", conid=conid)

        lines = []
        for t in themes:
            peers = [
                c.get("symbol") or c.get("name")
                for c in t.get("linked_companies") or []
                if isinstance(c, dict) and c.get("contract_id") != conid
            ]
            lines.append(f"Theme {t['name']}" + (f" — top peers: {', '.join(p for p in peers if p)}" if peers else ""))
        source_id = f"ibkr-themes:{conid}"
        meta = {"source": SOURCE_LABEL, "conid": conid, "themes": [t["name"] for t in themes]}
        item = EvidenceItem(
            evidence=ResearchEvidence(
                evidence_id=source_id,
                source_type="THEME",
                text="\n".join(lines),
                published_at=None,
                metadata={"provider": self.provider_name, "source_id": source_id, **meta},
            ),
            source=EvidenceSource(
                source_id=source_id,
                provider=self.provider_name,
                source_type="THEME",
                source_name="IBKR company themes",
                retrieved_at=fetched_at,
                metadata=meta,
            ),
            kind=EvidenceKind.OTHER,
            ticker=request.ticker,
            metadata=meta,
        )
        return EvidenceFetchResult(
            provider=self.provider_name,
            ticker=request.ticker,
            status=EvidenceFetchStatus.SUCCESS,
            items=[item],
            fetched_at=fetched_at,
            metadata={"source": SOURCE_LABEL, "conid": conid},
        )
