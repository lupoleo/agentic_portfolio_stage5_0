from __future__ import annotations

import argparse
import os
import time
from collections import Counter

from app.scanner.exchange_provider import (
    ExchangeProviderStatus,
    ExchangeSymbolRequest,
)
from app.scanner.providers.eodhd_us_aggregate import (
    EODHDUSAggregateProvider,
)


ENABLED_V1_VENUES = frozenset(
    {"AMEX", "BATS", "NASDAQ", "NYSE", "NYSE ARCA"}
)
EXPECTED_PRESERVED_VENUES = frozenset(
    {"NMFQS", "NYSE MKT", "PINK", "US"}
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the EODHD US aggregate provider live contract."
    )
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be > 0")

    token = os.getenv("EODHD_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("EODHD_API_TOKEN is missing")

    started = time.perf_counter()
    result = EODHDUSAggregateProvider(
        timeout_seconds=args.timeout_seconds,
    ).fetch(ExchangeSymbolRequest("US"))
    elapsed_seconds = time.perf_counter() - started

    print(f"status: {result.status.value}")
    print(f"provider_id: {result.provider_id}")
    print(f"provider_version: {result.provider_version}")
    print(f"source_url: {result.source_url}")
    print(f"elapsed_seconds: {elapsed_seconds:.3f}")
    print(f"listings: {len(result.listings)}")
    print(f"diagnostics: {len(result.diagnostics)}")

    if result.status is ExchangeProviderStatus.FAILED:
        print("\nDIAGNOSTICS:")
        for diagnostic in result.diagnostics:
            print(
                f"{diagnostic.code} | {diagnostic.symbol or '-'} | "
                f"{diagnostic.message}"
            )
        return 1

    keys = [(item.exchange, item.symbol) for item in result.listings]
    venue_counts = Counter(item.exchange for item in result.listings)
    duplicate_key_count = len(keys) - len(set(keys))
    sorted_output = keys == sorted(keys)
    token_safe = bool(result.source_url) and token not in result.source_url
    enabled_missing = sorted(ENABLED_V1_VENUES - set(venue_counts))
    preserved_missing = sorted(EXPECTED_PRESERVED_VENUES - set(venue_counts))

    print("\nINVARIANTS:")
    print(f"unique_exchange_symbol_keys: {duplicate_key_count == 0}")
    print(f"duplicate_exchange_symbol_keys: {duplicate_key_count}")
    print(f"sorted_by_exchange_symbol: {sorted_output}")
    print(f"token_safe_source_url: {token_safe}")
    print(f"missing_enabled_v1_venues: {enabled_missing}")
    print(f"missing_preserved_venues: {preserved_missing}")

    print("\nENABLED V1 VENUES:")
    for venue in sorted(ENABLED_V1_VENUES):
        print(f"{venue}: {venue_counts.get(venue, 0)}")

    print("\nPRESERVED BUT DISABLED/DEFERRED:")
    for venue in sorted(EXPECTED_PRESERVED_VENUES):
        print(f"{venue}: {venue_counts.get(venue, 0)}")

    print("\nMETADATA:")
    for key in (
        "request_scope",
        "raw_row_count",
        "listing_count",
        "rejected_row_count",
        "duplicate_row_count",
        "response_bytes",
        "venue_count",
    ):
        print(f"{key}: {result.metadata.get(key)}")

    print("\nDIAGNOSTICS:")
    for diagnostic in result.diagnostics:
        print(
            f"{diagnostic.code} | {diagnostic.symbol or '-'} | "
            f"{diagnostic.message}"
        )

    valid = all(
        (
            duplicate_key_count == 0,
            sorted_output,
            token_safe,
            not enabled_missing,
            not preserved_missing,
            result.metadata.get("request_scope") == "US",
            result.metadata.get("listing_count") == len(result.listings),
        )
    )
    print(f"\nLIVE_CONTRACT: {'PASS' if valid else 'FAIL'}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
