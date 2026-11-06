from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.scanner.exchange_policy import (
    EnabledExchangePolicy,
    ExchangeCoverageScope,
)
from app.scanner.exchange_provider import (
    ExchangeProviderStatus,
    ExchangeSymbolProvider,
    ExchangeSymbolRequest,
    ExchangeSymbolResult,
)
from app.scanner.universe_models import ListingKey, MarketListing
from app.scanner.universe_provider import build_canonical_universe


class UniverseCompositionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass(frozen=True)
class UniverseCompositionDiagnostic:
    exchange_code: str
    provider_id: str
    code: str
    message: str

    def __post_init__(self) -> None:
        exchange_code = self.exchange_code.strip().upper()
        provider_id = self.provider_id.strip()
        code = self.code.strip().upper()
        message = self.message.strip()

        if not exchange_code:
            raise ValueError("exchange_code must not be blank")
        if not provider_id:
            raise ValueError("provider_id must not be blank")
        if not code:
            raise ValueError("code must not be blank")
        if not message:
            raise ValueError("message must not be blank")

        object.__setattr__(
            self,
            "exchange_code",
            exchange_code,
        )
        object.__setattr__(
            self,
            "provider_id",
            provider_id,
        )
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", message)


@dataclass(frozen=True)
class ComposedUniverseResult:
    policy_id: str
    policy_version: str
    status: UniverseCompositionStatus
    universe: tuple[MarketListing, ...] = ()
    provider_results: tuple[ExchangeSymbolResult, ...] = ()
    diagnostics: tuple[UniverseCompositionDiagnostic, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        policy_id = self.policy_id.strip()
        policy_version = self.policy_version.strip()

        if not policy_id:
            raise ValueError("policy_id must not be blank")
        if not policy_version:
            raise ValueError("policy_version must not be blank")

        if (
            self.status is UniverseCompositionStatus.SUCCESS
            and not self.universe
        ):
            raise ValueError("SUCCESS requires universe")

        if self.status is UniverseCompositionStatus.PARTIAL:
            if not self.universe:
                raise ValueError("PARTIAL requires universe")
            if not self.diagnostics:
                raise ValueError("PARTIAL requires diagnostics")

        if self.status is UniverseCompositionStatus.FAILED:
            if self.universe:
                raise ValueError(
                    "FAILED must not contain universe"
                )
            if not self.diagnostics:
                raise ValueError(
                    "FAILED requires diagnostics"
                )

        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(
            self,
            "policy_version",
            policy_version,
        )


def _canonical_provider_map(
    providers: Mapping[str, ExchangeSymbolProvider],
) -> dict[str, ExchangeSymbolProvider]:
    canonical: dict[str, ExchangeSymbolProvider] = {}

    for raw_exchange, provider in providers.items():
        exchange = raw_exchange.strip().upper()

        if not exchange:
            raise ValueError(
                "provider registry exchange must not be blank"
            )
        if exchange in canonical:
            raise ValueError(
                f"duplicate provider registry exchange: {exchange}"
            )
        if not isinstance(provider, ExchangeSymbolProvider):
            raise TypeError(
                f"provider for {exchange} must implement "
                "ExchangeSymbolProvider"
            )

        canonical[exchange] = provider

    return canonical


def compose_exchange_universe(
    *,
    policy: EnabledExchangePolicy,
    providers: Mapping[str, ExchangeSymbolProvider],
    exclusions: Iterable[ListingKey] = (),
) -> ComposedUniverseResult:
    registry = _canonical_provider_map(providers)

    diagnostics: list[UniverseCompositionDiagnostic] = []
    provider_results: list[ExchangeSymbolResult] = []
    raw_listings = []

    successful_provider_count = 0
    partial_provider_count = 0
    failed_provider_count = 0

    coverage_counts = {
        ExchangeCoverageScope.FULL_EXCHANGE.value: 0,
        ExchangeCoverageScope.INDEX_FALLBACK.value: 0,
        ExchangeCoverageScope.AGGREGATE_VENUES.value: 0,
    }

    acquisitions: dict[str, list] = {}
    for entry in policy.enabled_entries:
        acquisitions.setdefault(
            entry.acquisition_code,
            [],
        ).append(entry)
        coverage_counts[entry.coverage_scope.value] += 1

    for acquisition_code in sorted(acquisitions):
        entries = acquisitions[acquisition_code]
        entry = entries[0]

        provider = registry.get(acquisition_code)

        if provider is None:
            failed_provider_count += 1
            diagnostics.append(
                UniverseCompositionDiagnostic(
                    exchange_code=acquisition_code,
                    provider_id=entry.provider_id,
                    code="MISSING_PROVIDER",
                    message=(
                        "No provider registered for enabled exchange "
                        f"{acquisition_code}"
                    ),
                )
            )
            continue

        if provider.provider_id != entry.provider_id:
            failed_provider_count += 1
            diagnostics.append(
                UniverseCompositionDiagnostic(
                    exchange_code=acquisition_code,
                    provider_id=entry.provider_id,
                    code="PROVIDER_ID_MISMATCH",
                    message=(
                        "Registered provider identity mismatch: "
                        f"expected {entry.provider_id}, "
                        f"got {provider.provider_id}"
                    ),
                )
            )
            continue

        request = ExchangeSymbolRequest(
            acquisition_code
        )

        try:
            result = provider.fetch(request)
        except Exception as exc:
            failed_provider_count += 1
            diagnostics.append(
                UniverseCompositionDiagnostic(
                    exchange_code=acquisition_code,
                    provider_id=entry.provider_id,
                    code="PROVIDER_EXCEPTION",
                    message=(
                        "Provider raised unexpectedly: "
                        f"{type(exc).__name__}"
                    ),
                )
            )
            continue

        if not isinstance(result, ExchangeSymbolResult):
            failed_provider_count += 1
            diagnostics.append(
                UniverseCompositionDiagnostic(
                    exchange_code=acquisition_code,
                    provider_id=entry.provider_id,
                    code="INVALID_PROVIDER_RESULT",
                    message=(
                        "Provider did not return "
                        "ExchangeSymbolResult"
                    ),
                )
            )
            continue

        provider_results.append(result)

        if (
            result.provider_id != entry.provider_id
            or result.request.exchange_code
            != acquisition_code
        ):
            failed_provider_count += 1
            diagnostics.append(
                UniverseCompositionDiagnostic(
                    exchange_code=acquisition_code,
                    provider_id=entry.provider_id,
                    code="INVALID_PROVIDER_RESULT",
                    message=(
                        "Provider result identity does not match "
                        "the enabled policy entry"
                    ),
                )
            )
            continue

        if (
            entry.coverage_scope
            is ExchangeCoverageScope.INDEX_FALLBACK
            and (
                result.metadata.get("coverage_scope")
                != ExchangeCoverageScope.INDEX_FALLBACK.value
                or result.metadata.get(
                    "full_exchange_coverage"
                )
                is not False
            )
        ):
            failed_provider_count += 1
            diagnostics.append(
                UniverseCompositionDiagnostic(
                    exchange_code=acquisition_code,
                    provider_id=entry.provider_id,
                    code="COVERAGE_METADATA_MISMATCH",
                    message=(
                        "Index-fallback provider did not declare "
                        "the required limited coverage"
                    ),
                )
            )
            continue

        aggregate = (
            entry.coverage_scope
            is ExchangeCoverageScope.AGGREGATE_VENUES
        )
        enabled_outputs = {
            item.exchange_code for item in entries
        }
        mismatched_listing = (
            None
            if aggregate
            else next(
                (
                    listing
                    for listing in result.listings
                    if listing.exchange not in enabled_outputs
                ),
                None,
            )
        )

        if mismatched_listing is not None:
            failed_provider_count += 1
            diagnostics.append(
                UniverseCompositionDiagnostic(
                    exchange_code=acquisition_code,
                    provider_id=entry.provider_id,
                    code="LISTING_EXCHANGE_MISMATCH",
                    message=(
                        "Provider returned listing for exchange "
                        f"{mismatched_listing.exchange}"
                    ),
                )
            )
            continue

        for item in result.diagnostics:
            diagnostics.append(
                UniverseCompositionDiagnostic(
                    exchange_code=acquisition_code,
                    provider_id=entry.provider_id,
                    code=item.code,
                    message=item.message,
                )
            )

        if result.status is ExchangeProviderStatus.SUCCESS:
            successful_provider_count += 1
            raw_listings.extend(result.listings)

        elif result.status is ExchangeProviderStatus.PARTIAL:
            partial_provider_count += 1
            raw_listings.extend(result.listings)

        else:
            failed_provider_count += 1

    metadata = {
        "enabled_exchange_count": len(
            policy.enabled_entries
        ),
        "acquisition_count": len(acquisitions),
        "registered_provider_count": len(registry),
        "executed_provider_count": len(provider_results),
        "successful_provider_count": successful_provider_count,
        "partial_provider_count": partial_provider_count,
        "failed_provider_count": failed_provider_count,
        "raw_listing_count": len(raw_listings),
        "full_exchange_count": coverage_counts[
            ExchangeCoverageScope.FULL_EXCHANGE.value
        ],
        "index_fallback_count": coverage_counts[
            ExchangeCoverageScope.INDEX_FALLBACK.value
        ],
        "aggregate_venue_count": coverage_counts[
            ExchangeCoverageScope.AGGREGATE_VENUES.value
        ],
    }

    if not raw_listings:
        diagnostics.append(
            UniverseCompositionDiagnostic(
                exchange_code="ALL",
                provider_id="provider-composition",
                code="NO_VALID_LISTINGS",
                message=(
                    "Enabled providers produced no valid listings"
                ),
            )
        )

        return ComposedUniverseResult(
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            status=UniverseCompositionStatus.FAILED,
            provider_results=tuple(provider_results),
            diagnostics=tuple(diagnostics),
            metadata={
                **metadata,
                "canonical_listing_count": 0,
            },
        )

    try:
        universe = tuple(
            build_canonical_universe(
                raw_listings,
                exclusions=exclusions,
                enabled_exchanges=(
                    policy.enabled_exchange_codes
                ),
            )
        )
    except ValueError as exc:
        diagnostics.append(
            UniverseCompositionDiagnostic(
                exchange_code="ALL",
                provider_id="provider-composition",
                code="CANONICALIZATION_FAILED",
                message=str(exc),
            )
        )

        return ComposedUniverseResult(
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            status=UniverseCompositionStatus.FAILED,
            provider_results=tuple(provider_results),
            diagnostics=tuple(diagnostics),
            metadata={
                **metadata,
                "canonical_listing_count": 0,
            },
        )

    if not universe:
        diagnostics.append(
            UniverseCompositionDiagnostic(
                exchange_code="ALL",
                provider_id="provider-composition",
                code="EMPTY_CANONICAL_UNIVERSE",
                message=(
                    "Canonical universe is empty after exclusions"
                ),
            )
        )

        return ComposedUniverseResult(
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            status=UniverseCompositionStatus.FAILED,
            provider_results=tuple(provider_results),
            diagnostics=tuple(diagnostics),
            metadata={
                **metadata,
                "canonical_listing_count": 0,
            },
        )

    status = (
        UniverseCompositionStatus.PARTIAL
        if diagnostics
        or partial_provider_count
        or failed_provider_count
        else UniverseCompositionStatus.SUCCESS
    )

    return ComposedUniverseResult(
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        status=status,
        universe=universe,
        provider_results=tuple(provider_results),
        diagnostics=tuple(diagnostics),
        metadata={
            **metadata,
            "canonical_listing_count": len(universe),
        },
    )
