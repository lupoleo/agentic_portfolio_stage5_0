from __future__ import annotations

from typing import Protocol

from app.scanner.exchange_provider import (
    ExchangeProviderDiagnostic,
    ExchangeProviderStatus,
    ExchangeSymbolProvider,
    ExchangeSymbolRequest,
    ExchangeSymbolResult,
)
from app.scanner.providers.borsa_italiana_ftsemib import (
    BORSA_ITALIANA_BASE,
    FTSE_MIB_PAGE_1,
    PROVIDER_ID,
    PROVIDER_VERSION,
    SOURCE_NAME,
    BorsaItalianaFTSEMIBProvider,
    BorsaItalianaFTSEMIBResult,
)


CANONICAL_EXCHANGE = "BIT"
COVERAGE_SCOPE = "INDEX_FALLBACK"
INDEX_NAME = "FTSE_MIB"


class _BorsaItalianaProviderLike(Protocol):
    def fetch(self) -> BorsaItalianaFTSEMIBResult:
        ...


class BorsaItalianaFTSEMIBExchangeProvider(
    ExchangeSymbolProvider
):
    """
    Contract adapter from the fixed FTSE MIB provider to the generic
    exchange-symbol provider boundary.

    This adapter does not claim full Euronext Milan coverage.
    """

    provider_id = PROVIDER_ID
    provider_version = PROVIDER_VERSION

    def __init__(
        self,
        *,
        provider: _BorsaItalianaProviderLike | None = None,
    ) -> None:
        self._provider = (
            provider
            if provider is not None
            else BorsaItalianaFTSEMIBProvider()
        )

    def fetch(
        self,
        request: ExchangeSymbolRequest,
    ) -> ExchangeSymbolResult:
        if request.exchange_code != CANONICAL_EXCHANGE:
            return ExchangeSymbolResult(
                provider_id=self.provider_id,
                provider_version=self.provider_version,
                source_name=SOURCE_NAME,
                source_url=FTSE_MIB_PAGE_1,
                request=request,
                status=ExchangeProviderStatus.FAILED,
                diagnostics=(
                    ExchangeProviderDiagnostic(
                        code="UNSUPPORTED_EXCHANGE",
                        message=(
                            "Borsa Italiana FTSE MIB adapter supports "
                            f"only {CANONICAL_EXCHANGE}, got "
                            f"{request.exchange_code}"
                        ),
                    ),
                ),
                metadata=self._coverage_metadata(),
            )

        result = self._provider.fetch()

        status = ExchangeProviderStatus(result.status.value)

        diagnostics = tuple(
            ExchangeProviderDiagnostic(
                code=item.code,
                message=(
                    item.message
                    if item.isin is None
                    else f"{item.message} [isin={item.isin}]"
                ),
            )
            for item in result.diagnostics
        )

        metadata = {
            **result.metadata,
            **self._coverage_metadata(),
            "source_urls": result.source_urls,
            "source_diagnostics": tuple(
                {
                    "code": item.code,
                    "message": item.message,
                    "isin": item.isin,
                }
                for item in result.diagnostics
            ),
        }

        return ExchangeSymbolResult(
            provider_id=result.provider_id,
            provider_version=result.provider_version,
            source_name=result.source_name,
            source_url=result.source_urls[0],
            request=request,
            status=status,
            listings=result.listings,
            diagnostics=diagnostics,
            fetched_at=result.fetched_at,
            metadata=metadata,
        )

    @staticmethod
    def _coverage_metadata() -> dict[str, object]:
        return {
            "exchange_code": CANONICAL_EXCHANGE,
            "coverage_scope": COVERAGE_SCOPE,
            "index_name": INDEX_NAME,
            "full_exchange_coverage": False,
            "official_source": BORSA_ITALIANA_BASE,
        }
