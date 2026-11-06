"""IBKR evidence channels for the research pipeline (architecture §5D, S5.0-4).

The 4.0 ``ScannerResearchIntegrationService`` takes one provider per channel
and a canonical-technical loader, and sends only a ticker. This module:

* adds the IBKR identity of the ticker from the run's ``InstrumentDirectory``
  (``WithInstrumentContext``);
* merges the IBKR news-channel sources (IB Gateway headlines, earnings
  insights when a connector client is available) (``CompositeEvidenceProvider``);
* applies the §5D precedence: IBKR first, Yahoo only as a declared,
  marked secondary.

Market history: IBKR first; Yahoo history is the declared secondary only for
instruments the run cannot resolve to an IBKR listing it has data permission
for (scanner candidates until S5.0-5 puts screener rows in the directory).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.ai.evidence_precedence import PrecedenceEvidenceProvider
from app.ai.evidence_provider import (
    EvidenceFetchResult,
    EvidenceFetchStatus,
    EvidenceProvider,
    EvidenceProviderError,
    EvidenceRequest,
)
from app.ai.ibkr_market_context import MarketContextBuilder
from app.ai.ibkr_market import IbkrHistoryLoader, IbkrMarketEvidenceProvider, ibkr_canonical_technical_loader
from app.ai.ibkr_news_evidence_provider import IbkrGatewayNewsEvidenceProvider
from app.broker.gateway import GatewaySession
from app.broker.instrument_directory import InstrumentDirectory

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WithInstrumentContext(EvidenceProvider):
    """Adds the directory's IBKR identity to the request; unknown ticker → NO_DATA."""

    def __init__(self, inner: EvidenceProvider, directory: InstrumentDirectory, *, clock: Clock = _utc_now) -> None:
        self._inner = inner
        self._directory = directory
        self._clock = clock

    @property
    def provider_name(self) -> str:
        return self._inner.provider_name

    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        ref = self._directory.get(request.ticker)
        if ref is None:
            return EvidenceFetchResult(
                provider=self.provider_name, ticker=request.ticker, status=EvidenceFetchStatus.NO_DATA,
                warnings=[f"{request.ticker} is not resolved to an IBKR instrument in this run"],
                fetched_at=self._clock(),
            )
        return self._inner.fetch(request.model_copy(update={"metadata": {**request.metadata, **ref.as_metadata()}}))


class CompositeEvidenceProvider(EvidenceProvider):
    """Runs every provider and merges their items (one channel, several IBKR sources).

    SUCCESS when all sources answered and at least one has items; PARTIAL when
    one failed but another has items; NO_DATA when none has items; the first
    error is re-raised when no source has items and at least one failed, so a
    precedence wrapper can fall back to the secondary.
    """

    def __init__(self, name: str, providers: Sequence[EvidenceProvider], *, clock: Clock = _utc_now) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._name = name
        self._providers = tuple(providers)
        self._clock = clock

    @property
    def provider_name(self) -> str:
        return self._name

    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        items, warnings, errors, sources = [], [], [], {}
        for provider in self._providers:
            try:
                result = provider.fetch(request)
            except EvidenceProviderError as exc:
                errors.append(exc)
                warnings.append(f"{provider.provider_name} failed: {type(exc).__name__}: {exc}")
                sources[provider.provider_name] = "ERROR"
                continue
            items.extend(result.items)
            warnings.extend(f"{provider.provider_name}: {w}" for w in result.warnings)
            sources[provider.provider_name] = result.status.value
        if not items and errors:
            raise errors[0]
        if not items:
            status = EvidenceFetchStatus.NO_DATA
        elif errors or any(s == EvidenceFetchStatus.PARTIAL.value for s in sources.values()):
            status = EvidenceFetchStatus.PARTIAL
        else:
            status = EvidenceFetchStatus.SUCCESS
        return EvidenceFetchResult(
            provider=self._name, ticker=request.ticker, status=status, items=items[: request.max_items],
            warnings=warnings, fetched_at=self._clock(), metadata={"source": "IBKR", "sources": sources},
        )


class NoPrimaryChannel(EvidenceProvider):
    """IBKR has no source for this channel (§5D.2): always NO_DATA, so the
    precedence wrapper runs the declared secondary and marks its items."""

    def __init__(self, name: str, reason: str, *, clock: Clock = _utc_now) -> None:
        self._name = name
        self._reason = reason
        self._clock = clock

    @property
    def provider_name(self) -> str:
        return self._name

    def fetch(self, request: EvidenceRequest) -> EvidenceFetchResult:
        return EvidenceFetchResult(
            provider=self._name, ticker=request.ticker, status=EvidenceFetchStatus.NO_DATA,
            warnings=[self._reason], fetched_at=self._clock(),
        )


@dataclass(frozen=True)
class IbkrEvidenceChannels:
    market: EvidenceProvider
    news: EvidenceProvider
    fundamental: EvidenceProvider | None
    analyst: EvidenceProvider | None
    canonical_technical_loader: Callable[[str], Any]
    history_loader: IbkrHistoryLoader
    technical_secondary_tickers: list[str]  # tickers whose technical input came from the secondary


def _technical_with_fallback(primary: Callable[[str], Any], secondary: Callable[[str], Any] | None, used: list[str]):
    def load(ticker: str):
        try:
            return primary(ticker)
        except Exception:
            if secondary is None:
                raise
            used.append(ticker.strip().upper())
            return secondary(ticker)

    return load


def build_ibkr_evidence_channels(
    session: GatewaySession,
    directory: InstrumentDirectory,
    *,
    ibkr_client=None,
    secondary_market: EvidenceProvider | None = None,
    secondary_news: EvidenceProvider | None = None,
    secondary_fundamental: EvidenceProvider | None = None,
    secondary_analyst: EvidenceProvider | None = None,
    secondary_technical_loader: Callable[[str], Any] | None = None,
    history_duration: str = "1 Y",
    clock: Clock = _utc_now,
) -> IbkrEvidenceChannels:
    """All research channels for one run. ``ibkr_client`` is a ``ReadOnlyIbkrClient``
    for earnings insights; without it the news channel has IB Gateway headlines only."""

    loader = IbkrHistoryLoader(session, directory, duration=history_duration)
    market = PrecedenceEvidenceProvider(
        IbkrMarketEvidenceProvider(loader, MarketContextBuilder(session, directory, duration=history_duration)),
        secondary_market,
    )

    news_sources: list[EvidenceProvider] = [
        WithInstrumentContext(IbkrGatewayNewsEvidenceProvider(session, clock=clock), directory, clock=clock)
    ]
    if ibkr_client is not None:
        from app.ai.ibkr_evidence_provider import IbkrEventEvidenceProvider

        news_sources.append(WithInstrumentContext(IbkrEventEvidenceProvider(ibkr_client, clock=clock), directory, clock=clock))
    news = PrecedenceEvidenceProvider(CompositeEvidenceProvider("IBKR_NEWS_CHANNEL", news_sources, clock=clock), secondary_news)

    def declared_secondary(name: str, secondary: EvidenceProvider | None) -> EvidenceProvider | None:
        if secondary is None:
            return None
        return PrecedenceEvidenceProvider(
            NoPrimaryChannel(name, f"IBKR has no {name.split('_')[1].lower()} channel (§5D.2)", clock=clock), secondary
        )

    technical_secondary: list[str] = []
    return IbkrEvidenceChannels(
        market=market,
        news=news,
        fundamental=declared_secondary("IBKR_FUNDAMENTAL", secondary_fundamental),
        analyst=declared_secondary("IBKR_ANALYST", secondary_analyst),
        canonical_technical_loader=_technical_with_fallback(
            ibkr_canonical_technical_loader(loader), secondary_technical_loader, technical_secondary
        ),
        history_loader=loader,
        technical_secondary_tickers=technical_secondary,
    )
