"""Daily headlines from IB Gateway news providers as NEWS evidence (architecture §5D).

Verified 2026-11-06: the account has the free Dow Jones and Briefing.com
providers. US names get fresh, often sector-wide headlines (the 4.0 news
relevance step stays downstream); Italian names get few; ETFs/ETCs get none or
unrelated items from symbol collisions, so funds are excluded up front.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

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
from app.broker.gateway import GatewaySession
from app.broker.ibkr_client import BrokerClientError, BrokerNoDataError

PROVIDER_NAME = "IBKR_GATEWAY_NEWS"
FUND_KINDS = frozenset({"ETF", "ETC", "ETP", "FUND"})
ARTICLE_BODIES = 3  # newest headlines whose full text is fetched
ARTICLE_MAX_CHARS = 1500  # per article, to bound research prompt size


class IbkrGatewayNewsEvidenceProvider(EvidenceProvider):
    def __init__(
        self,
        session: GatewaySession,
        *,
        provider_codes: list[str] | None = None,
        article_bodies: int = ARTICLE_BODIES,
        article_max_chars: int = ARTICLE_MAX_CHARS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._session = session
        self._codes = provider_codes
        self._clock = clock
        self._article_bodies = article_bodies
        self._article_max_chars = article_max_chars

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAME

    def _provider_codes(self) -> list[str]:
        if self._codes is None:
            self._codes = self._session.news_providers()
        return self._codes

    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        fetched_at = self._clock()
        meta = request.metadata
        conid = meta.get("conid")
        if str(meta.get("asset_kind", "")).upper() in FUND_KINDS:
            return self._no_data(request, fetched_at, "no headline step for funds/ETCs (§5D)")
        if not isinstance(conid, int) or isinstance(conid, bool) or conid <= 0:
            return self._no_data(request, fetched_at, "instrument not resolved to an IBKR contract id")
        try:
            headlines = self._session.headlines(conid, self._provider_codes(), limit=request.max_items)
        except BrokerNoDataError:
            headlines = []
        except BrokerClientError as exc:
            raise EvidenceProviderConnectionError(str(exc)) from exc

        items, seen = [], set()
        for h in headlines:
            key = h.headline.casefold()
            if not h.headline or key in seen:
                continue
            seen.add(key)
            published = h.published_at if h.published_at.tzinfo else h.published_at.replace(tzinfo=timezone.utc)
            source_id = f"ibkr-news:{h.provider_code}:{h.article_id}"
            item_meta = {"source": "IBKR_NEWS", "conid": conid, "provider_code": h.provider_code, "article_id": h.article_id}
            text = h.headline
            if len(items) < self._article_bodies:
                body = self._body(h)
                if body:
                    text = f"{h.headline}\n\n{body}"
                    item_meta = {**item_meta, "article_body": True}
            items.append(
                EvidenceItem(
                    evidence=ResearchEvidence(
                        evidence_id=source_id, source_type="NEWS", text=text, published_at=published,
                        metadata={"provider": PROVIDER_NAME, "source_id": source_id, **item_meta},
                    ),
                    source=EvidenceSource(
                        source_id=source_id, provider=PROVIDER_NAME, source_type="NEWS",
                        source_name=f"IBKR news {h.provider_code}", retrieved_at=fetched_at,
                        published_at=published, metadata=item_meta,
                    ),
                    kind=EvidenceKind.NEWS,
                    ticker=request.ticker,
                    metadata=item_meta,
                )
            )
        if not items:
            return self._no_data(request, fetched_at, "IBKR news providers have no headline for this instrument")
        return EvidenceFetchResult(
            provider=PROVIDER_NAME, ticker=request.ticker, status=EvidenceFetchStatus.SUCCESS, items=items,
            fetched_at=fetched_at, metadata={"source": "IBKR_NEWS", "conid": conid, "providers": self._provider_codes()},
        )

    def _body(self, headline) -> str:
        """Full article text, truncated; an article that cannot be read keeps the headline only."""
        try:
            body = self._session.news_article(headline.provider_code, headline.article_id)
        except (BrokerClientError, BrokerNoDataError):
            return ""
        if not body or body.casefold() == headline.headline.casefold():
            return ""
        if len(body) > self._article_max_chars:
            body = body[: self._article_max_chars].rsplit(" ", 1)[0] + " [...]"
        return body

    def _no_data(self, request, fetched_at, warning) -> EvidenceFetchResult:
        return EvidenceFetchResult(
            provider=PROVIDER_NAME, ticker=request.ticker, status=EvidenceFetchStatus.NO_DATA,
            warnings=[warning], fetched_at=fetched_at, metadata={"source": "IBKR_NEWS"},
        )
