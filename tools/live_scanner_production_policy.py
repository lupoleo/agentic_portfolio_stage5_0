from __future__ import annotations

import argparse
from collections import Counter
from datetime import timedelta
from pathlib import Path
from time import perf_counter

from app.scanner.cached_exchange_provider import (
    CacheRefreshMode,
    CachedExchangeSymbolProvider,
)
from app.scanner.eodhd_exchange_discovery import (
    EODHDExchangeDiscoveryProvider,
    ExchangeDiscoveryStatus,
)
from app.scanner.exchange_policy import scanner_v1_production_policy
from app.scanner.exchange_provider import (
    ExchangeSymbolProvider,
    ExchangeSymbolRequest,
)
from app.scanner.provider_cache import ExchangeSymbolFileCache
from app.scanner.provider_composition import (
    UniverseCompositionStatus,
    compose_exchange_universe,
)
from app.scanner.providers.borsa_italiana_ftsemib_adapter import (
    BorsaItalianaFTSEMIBExchangeProvider,
)
from app.scanner.providers.eodhd_exchange_symbols import (
    EODHDExchangeSymbolProvider,
)
from app.scanner.providers.eodhd_us_aggregate import (
    EODHDUSAggregateProvider,
)


DEFAULT_CACHE_DIR = Path("data/cache/scanner/exchange_symbols")
DISABLED_US_VENUES = {
    "NMFQS",
    "NYSE MKT",
    "PINK",
    "US",
}


class ProgressProvider(ExchangeSymbolProvider):
    """Print bounded progress while preserving the provider contract."""

    def __init__(
        self,
        provider: ExchangeSymbolProvider,
        *,
        ordinal: int,
        total: int,
    ) -> None:
        self._provider = provider
        self._ordinal = ordinal
        self._total = total

    @property
    def provider_id(self) -> str:
        return self._provider.provider_id

    @property
    def provider_version(self) -> str:
        return self._provider.provider_version

    def fetch(self, request: ExchangeSymbolRequest):
        label = f"{self._ordinal:02d}/{self._total:02d}"
        print(
            f"ACQUIRE {label} | {request.exchange_code} | started",
            flush=True,
        )
        started = perf_counter()
        result = self._provider.fetch(request)
        elapsed = perf_counter() - started
        source = (
            result.metadata.get("cache_source")
            or result.metadata.get("acquisition_source")
            or result.metadata.get("cache_status")
            or "UNSPECIFIED"
        )
        print(
            f"ACQUIRE {label} | {request.exchange_code} | "
            f"{result.status.value} | {len(result.listings)} listings | "
            f"{source} | {elapsed:.3f}s",
            flush=True,
        )
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the frozen Scanner V1 production exchange policy "
            "with cached live providers."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("prefer-cache", "force-refresh", "cache-only"),
        default="prefer-cache",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
    )
    parser.add_argument("--ttl-hours", type=float, default=24.0)
    parser.add_argument(
        "--allow-stale-on-error",
        action="store_true",
    )
    parser.add_argument("--max-stale-days", type=float, default=7.0)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--max-diagnostics",
        type=int,
        default=50,
    )
    return parser.parse_args()


def refresh_mode(value: str) -> CacheRefreshMode:
    return CacheRefreshMode(value.replace("-", "_").upper())


def build_live_registry(args, descriptors, acquisition_codes):
    descriptor_by_code = {
        descriptor.code: descriptor for descriptor in descriptors
    }
    cache = ExchangeSymbolFileCache(args.cache_dir)
    registry = {}
    total = len(acquisition_codes)

    for ordinal, code in enumerate(acquisition_codes, start=1):
        if code == "BIT":
            live_provider = BorsaItalianaFTSEMIBExchangeProvider()
        elif code == "US":
            live_provider = EODHDUSAggregateProvider(
                timeout_seconds=args.timeout_seconds,
            )
        else:
            descriptor = descriptor_by_code.get(code)
            if descriptor is None:
                raise ValueError(
                    f"discovery did not return enabled exchange {code}"
                )
            live_provider = EODHDExchangeSymbolProvider(
                descriptor=descriptor,
                timeout_seconds=args.timeout_seconds,
            )

        cached = CachedExchangeSymbolProvider(
            live_provider,
            cache,
            ttl=timedelta(hours=args.ttl_hours),
            refresh_mode=refresh_mode(args.mode),
            allow_stale_on_error=args.allow_stale_on_error,
            max_stale_age=timedelta(days=args.max_stale_days),
        )
        registry[code] = ProgressProvider(
            cached,
            ordinal=ordinal,
            total=total,
        )

    return registry


def validate_result(result, policy, acquisition_codes):
    enabled = set(policy.enabled_exchange_codes)
    universe_keys = [
        (item.exchange, item.symbol) for item in result.universe
    ]
    universe_exchanges = {item.exchange for item in result.universe}
    result_acquisitions = [
        item.request.exchange_code for item in result.provider_results
    ]

    checks = {
        "composition_not_failed": (
            result.status is not UniverseCompositionStatus.FAILED
        ),
        "all_enabled_venues_covered": not (
            enabled - universe_exchanges
        ),
        "no_disabled_venues_in_universe": not (
            universe_exchanges - enabled
        ),
        "disabled_us_venues_filtered": not (
            universe_exchanges & DISABLED_US_VENUES
        ),
        "unique_exchange_symbol_keys": (
            len(universe_keys) == len(set(universe_keys))
        ),
        "sorted_exchange_symbol_keys": (
            universe_keys == sorted(universe_keys)
        ),
        "all_acquisitions_returned_results": (
            set(result_acquisitions) == set(acquisition_codes)
        ),
        "each_acquisition_executed_once": (
            len(result_acquisitions)
            == len(set(result_acquisitions))
            == len(acquisition_codes)
        ),
        "us_aggregate_executed_once": (
            result_acquisitions.count("US") == 1
        ),
        "metadata_acquisition_count": (
            result.metadata.get("acquisition_count")
            == len(acquisition_codes)
        ),
        "metadata_enabled_exchange_count": (
            result.metadata.get("enabled_exchange_count")
            == len(enabled)
        ),
    }
    return checks, enabled - universe_exchanges, universe_exchanges - enabled


def main() -> int:
    args = parse_args()
    if args.ttl_hours <= 0:
        raise ValueError("ttl-hours must be > 0")
    if args.max_stale_days <= 0:
        raise ValueError("max-stale-days must be > 0")
    if args.timeout_seconds <= 0:
        raise ValueError("timeout-seconds must be > 0")
    if args.max_diagnostics < 0:
        raise ValueError("max-diagnostics must be >= 0")

    policy = scanner_v1_production_policy()
    acquisition_codes = sorted(
        {entry.acquisition_code for entry in policy.enabled_entries}
    )

    print("policy_id:", policy.policy_id)
    print("enabled_exchanges:", len(policy.enabled_entries))
    print("acquisitions:", len(acquisition_codes))
    print("mode:", args.mode)
    print("cache_dir:", args.cache_dir)
    print("discovery: started", flush=True)

    discovery = EODHDExchangeDiscoveryProvider(
        timeout_seconds=args.timeout_seconds,
    ).discover()
    print("discovery_status:", discovery.status.value, flush=True)

    if discovery.status is ExchangeDiscoveryStatus.FAILED:
        for item in discovery.diagnostics:
            print("DISCOVERY_DIAGNOSTIC:", item.code, item.message)
        print("LIVE_PRODUCTION_POLICY: FAIL")
        return 1

    try:
        registry = build_live_registry(
            args,
            discovery.exchanges,
            acquisition_codes,
        )
    except (TypeError, ValueError) as exc:
        print("REGISTRY_ERROR:", str(exc))
        print("LIVE_PRODUCTION_POLICY: FAIL")
        return 1

    started = perf_counter()
    result = compose_exchange_universe(
        policy=policy,
        providers=registry,
    )
    elapsed = perf_counter() - started

    counts = Counter(item.exchange for item in result.universe)
    checks, missing, unexpected = validate_result(
        result,
        policy,
        acquisition_codes,
    )

    print("\nCOMPOSITION:")
    print("status:", result.status.value)
    print("elapsed_seconds:", round(elapsed, 3))
    print("universe_count:", len(result.universe))
    print("provider_results:", len(result.provider_results))
    print("diagnostics:", len(result.diagnostics))

    print("\nUNIVERSE COUNTS:")
    for exchange in sorted(counts):
        print(f"{exchange}: {counts[exchange]}")

    print("\nINVARIANTS:")
    for name, passed in checks.items():
        print(f"{name}: {passed}")
    print("missing_enabled_venues:", sorted(missing))
    print("unexpected_venues:", sorted(unexpected))

    print("\nMETADATA:")
    for key in sorted(result.metadata):
        print(f"{key}: {result.metadata[key]}")

    print("\nDIAGNOSTICS:")
    shown = result.diagnostics[: args.max_diagnostics]
    for item in shown:
        print(
            f"{item.exchange_code} | {item.provider_id} | "
            f"{item.code} | {item.message}"
        )
    remaining = len(result.diagnostics) - len(shown)
    if remaining > 0:
        print(f"... {remaining} additional diagnostics omitted")

    passed = all(checks.values())
    print(
        "\nLIVE_PRODUCTION_POLICY:",
        "PASS" if passed else "FAIL",
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
