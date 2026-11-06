from __future__ import annotations

import argparse
import os
from datetime import timedelta
from pathlib import Path

from app.scanner.cached_exchange_provider import (
    CachedExchangeSymbolProvider,
    CacheRefreshMode,
)
from app.scanner.eodhd_exchange_discovery import (
    EODHDExchangeDiscoveryProvider,
    ExchangeDiscoveryStatus,
    ExchangeDescriptor,
)
from app.scanner.exchange_policy import (
    EnabledExchange,
    EnabledExchangePolicy,
    ExchangeCoverageScope,
)
from app.scanner.provider_cache import (
    ExchangeSymbolFileCache,
)
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


DEFAULT_CACHE_DIR = Path(
    "data/cache/scanner/exchange_symbols"
)

_MODE_BY_ARGUMENT = {
    "prefer-cache": CacheRefreshMode.PREFER_CACHE,
    "force-refresh": CacheRefreshMode.FORCE_REFRESH,
    "cache-only": CacheRefreshMode.CACHE_ONLY,
}


def exchange_code_argument(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise argparse.ArgumentTypeError(
            "exchange code must not be blank"
        )
    return normalized


def discover_exchange_descriptor(
    *,
    api_token: str,
    exchange_code: str,
    timeout_seconds: float,
) -> tuple[ExchangeDescriptor, ExchangeDiscoveryStatus]:
    discovery = EODHDExchangeDiscoveryProvider(
        api_token=api_token,
        timeout_seconds=timeout_seconds,
    ).discover()

    if discovery.status is ExchangeDiscoveryStatus.FAILED:
        raise RuntimeError("EODHD exchange discovery failed")

    descriptor = next(
        (
            item
            for item in discovery.exchanges
            if item.code == exchange_code
        ),
        None,
    )
    if descriptor is None:
        raise ValueError(
            "requested exchange is not present in "
            "EODHD discovery"
        )

    return descriptor, discovery.status


def live_policy(
    eodhd_exchange: str,
) -> EnabledExchangePolicy:
    return EnabledExchangePolicy(
        policy_id=(
            "scanner-live-cache-"
            f"{eodhd_exchange.lower()}"
        ),
        policy_version="1",
        entries=(
            EnabledExchange(
                exchange_code=eodhd_exchange,
                provider_id="eodhd-exchange-symbols",
                coverage_scope=(
                    ExchangeCoverageScope.FULL_EXCHANGE
                ),
            ),
            EnabledExchange(
                exchange_code="BIT",
                provider_id="borsa-italiana-ftse-mib",
                coverage_scope=(
                    ExchangeCoverageScope.INDEX_FALLBACK
                ),
            ),
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate scanner provider cache with one dynamically "
            "discovered EODHD exchange plus BIT."
        )
    )
    parser.add_argument(
        "--mode",
        choices=tuple(_MODE_BY_ARGUMENT),
        default="prefer-cache",
    )
    parser.add_argument(
        "--eodhd-exchange",
        type=exchange_code_argument,
        default="LU",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
    )
    parser.add_argument(
        "--ttl-hours",
        type=float,
        default=24.0,
    )
    parser.add_argument(
        "--allow-stale-on-error",
        action="store_true",
    )
    parser.add_argument(
        "--max-stale-days",
        type=float,
        default=7.0,
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=60.0,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.ttl_hours <= 0:
        raise SystemExit("--ttl-hours must be > 0")
    if args.max_stale_days <= 0:
        raise SystemExit("--max-stale-days must be > 0")
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be > 0")

    api_token = os.environ.get("EODHD_API_TOKEN")
    if not api_token or not api_token.strip():
        raise SystemExit(
            "EODHD_API_TOKEN is required but was not found"
        )

    try:
        descriptor, discovery_status = (
            discover_exchange_descriptor(
                api_token=api_token,
                exchange_code=args.eodhd_exchange,
                timeout_seconds=args.timeout_seconds,
            )
        )
    except Exception as exc:
        raise SystemExit(
            "Unable to resolve EODHD exchange descriptor: "
            f"{type(exc).__name__}"
        ) from None

    cache = ExchangeSymbolFileCache(args.cache_dir)
    common = {
        "cache": cache,
        "ttl": timedelta(hours=args.ttl_hours),
        "refresh_mode": _MODE_BY_ARGUMENT[args.mode],
        "allow_stale_on_error": (
            args.allow_stale_on_error
        ),
        "max_stale_age": timedelta(
            days=args.max_stale_days
        ),
    }

    eodhd_provider = CachedExchangeSymbolProvider(
        EODHDExchangeSymbolProvider(
            descriptor=descriptor,
            api_token=api_token,
            timeout_seconds=args.timeout_seconds,
        ),
        **common,
    )
    bit_provider = CachedExchangeSymbolProvider(
        BorsaItalianaFTSEMIBExchangeProvider(),
        **common,
    )

    result = compose_exchange_universe(
        policy=live_policy(args.eodhd_exchange),
        providers={
            args.eodhd_exchange: eodhd_provider,
            "BIT": bit_provider,
        },
    )

    print(f"mode: {args.mode}")
    print(f"eodhd_exchange: {args.eodhd_exchange}")
    print(f"discovery_status: {discovery_status.value}")
    print(f"cache_dir: {args.cache_dir}")
    print(f"status: {result.status}")
    print(f"universe_count: {len(result.universe)}")
    print(f"provider_results: {len(result.provider_results)}")
    print(f"diagnostics: {len(result.diagnostics)}")

    print("\nPROVIDERS:")
    for provider_result in result.provider_results:
        cache_metadata = provider_result.metadata.get(
            "cache",
            {},
        )
        print(
            " | ".join(
                (
                    provider_result.request.exchange_code,
                    provider_result.provider_id,
                    provider_result.status.value,
                    str(len(provider_result.listings)),
                    str(
                        cache_metadata.get(
                            "disposition",
                            "NONE",
                        )
                    ),
                    provider_result.fetched_at.isoformat(),
                )
            )
        )

    print("\nUNIVERSE COUNTS:")
    counts: dict[str, int] = {}
    for item in result.universe:
        counts[item.exchange] = (
            counts.get(item.exchange, 0) + 1
        )
    for exchange in sorted(counts):
        print(exchange, counts[exchange])

    print("\nDIAGNOSTICS:")
    for diagnostic in result.diagnostics:
        print(
            " | ".join(
                (
                    diagnostic.exchange_code,
                    diagnostic.provider_id,
                    diagnostic.code,
                    diagnostic.message,
                )
            )
        )

    if result.status is UniverseCompositionStatus.SUCCESS:
        return 0
    if result.status is UniverseCompositionStatus.PARTIAL:
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
