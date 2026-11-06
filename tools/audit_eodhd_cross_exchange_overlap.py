from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

from app.scanner.eodhd_exchange_discovery import (
    EODHDExchangeDiscoveryProvider,
    ExchangeDescriptor,
    ExchangeDiscoveryStatus,
)
from app.scanner.exchange_provider import (
    ExchangeProviderStatus,
    ExchangeSymbolRequest,
)
from app.scanner.providers.eodhd_exchange_symbols import (
    EODHDExchangeSymbolProvider,
)
from app.scanner.universe_models import RawMarketListing


DEFAULT_EXCHANGES = (
    "XETRA",
    "F",
    "STU",
    "MU",
    "DU",
    "HM",
    "HA",
)
DEFAULT_OUTPUT_DIRECTORY = Path(
    "data/cache/scanner/exchange_audit"
)
SCOPES = (
    "ALL",
    "COMMON STOCK",
    "ETF",
)


def exchange_code(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise argparse.ArgumentTypeError(
            "exchange code must not be blank"
        )
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure ISIN and symbol overlap across EODHD exchanges "
            "without changing Scanner policy."
        )
    )
    parser.add_argument(
        "exchanges",
        nargs="*",
        type=exchange_code,
        default=list(DEFAULT_EXCHANGES),
        help=(
            "Ordered exchange codes used for pairwise and incremental "
            "coverage analysis. Defaults to the German-market set."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
    )
    return parser.parse_args()


def descriptor_record(
    descriptor: ExchangeDescriptor,
) -> dict[str, Any]:
    return {
        "code": descriptor.code,
        "name": descriptor.name,
        "operating_mic": descriptor.operating_mic,
        "country": descriptor.country,
        "currency": descriptor.currency,
        "region": descriptor.region,
    }


def scope_listings(
    listings: Iterable[RawMarketListing],
    scope: str,
) -> tuple[RawMarketListing, ...]:
    if scope == "ALL":
        return tuple(listings)
    return tuple(
        item
        for item in listings
        if item.instrument_type == scope
    )


def isin_set(
    listings: Iterable[RawMarketListing],
) -> set[str]:
    return {
        item.isin
        for item in listings
        if item.isin is not None
    }


def symbol_map(
    listings: Iterable[RawMarketListing],
) -> dict[str, str | None]:
    return {
        item.symbol: item.isin
        for item in listings
    }


def percent(
    numerator: int,
    denominator: int,
) -> float | None:
    if denominator == 0:
        return None
    return round(100.0 * numerator / denominator, 3)


def pairwise_record(
    *,
    left_code: str,
    left: tuple[RawMarketListing, ...],
    right_code: str,
    right: tuple[RawMarketListing, ...],
) -> dict[str, Any]:
    left_isins = isin_set(left)
    right_isins = isin_set(right)
    shared_isins = left_isins & right_isins
    union_isins = left_isins | right_isins

    left_symbols = symbol_map(left)
    right_symbols = symbol_map(right)
    shared_symbols = set(left_symbols) & set(right_symbols)
    same_symbol_same_isin = {
        symbol
        for symbol in shared_symbols
        if left_symbols[symbol] is not None
        and left_symbols[symbol] == right_symbols[symbol]
    }
    same_symbol_different_isin = {
        symbol
        for symbol in shared_symbols
        if left_symbols[symbol] is not None
        and right_symbols[symbol] is not None
        and left_symbols[symbol] != right_symbols[symbol]
    }
    same_symbol_incomplete_isin = {
        symbol
        for symbol in shared_symbols
        if left_symbols[symbol] is None
        or right_symbols[symbol] is None
    }

    return {
        "left_exchange": left_code,
        "right_exchange": right_code,
        "left_listing_count": len(left),
        "right_listing_count": len(right),
        "left_isin_count": len(left_isins),
        "right_isin_count": len(right_isins),
        "shared_isin_count": len(shared_isins),
        "left_exclusive_isin_count": len(left_isins - right_isins),
        "right_exclusive_isin_count": len(right_isins - left_isins),
        "shared_over_left_percent": percent(
            len(shared_isins),
            len(left_isins),
        ),
        "shared_over_right_percent": percent(
            len(shared_isins),
            len(right_isins),
        ),
        "isin_jaccard_percent": percent(
            len(shared_isins),
            len(union_isins),
        ),
        "shared_symbol_count": len(shared_symbols),
        "same_symbol_same_isin_count": len(
            same_symbol_same_isin
        ),
        "same_symbol_different_isin_count": len(
            same_symbol_different_isin
        ),
        "same_symbol_incomplete_isin_count": len(
            same_symbol_incomplete_isin
        ),
        "same_symbol_different_isin_examples": sorted(
            same_symbol_different_isin
        )[:20],
    }


def incremental_records(
    *,
    exchange_order: list[str],
    scoped: dict[str, tuple[RawMarketListing, ...]],
) -> list[dict[str, Any]]:
    accumulated: set[str] = set()
    records: list[dict[str, Any]] = []

    for code in exchange_order:
        current = isin_set(scoped[code])
        added = current - accumulated
        already_covered = current & accumulated
        records.append(
            {
                "exchange": code,
                "listing_count": len(scoped[code]),
                "isin_count": len(current),
                "new_isin_count": len(added),
                "already_covered_isin_count": len(already_covered),
                "incremental_over_exchange_percent": percent(
                    len(added),
                    len(current),
                ),
                "cumulative_isin_count": len(
                    accumulated | current
                ),
            }
        )
        accumulated.update(current)

    return records


def prevalence_record(
    *,
    exchange_order: list[str],
    scoped: dict[str, tuple[RawMarketListing, ...]],
) -> dict[str, Any]:
    occurrence = Counter(
        isin
        for code in exchange_order
        for isin in isin_set(scoped[code])
    )
    distribution = Counter(occurrence.values())
    return {
        "unique_isin_count": len(occurrence),
        "exchange_occurrence_distribution": {
            str(count): distribution[count]
            for count in sorted(distribution)
        },
        "isins_on_multiple_exchanges": sum(
            value
            for count, value in distribution.items()
            if count > 1
        ),
        "maximum_exchange_occurrence": max(
            occurrence.values(),
            default=0,
        ),
    }


def print_scope(
    scope: str,
    analysis: dict[str, Any],
) -> None:
    print(f"\n===== {scope} =====")
    prevalence = analysis["prevalence"]
    print(
        "unique_isins:",
        prevalence["unique_isin_count"],
        "multi_exchange_isins:",
        prevalence["isins_on_multiple_exchanges"],
        "max_exchange_occurrence:",
        prevalence["maximum_exchange_occurrence"],
    )
    print(
        "occurrence_distribution:",
        json.dumps(
            prevalence["exchange_occurrence_distribution"],
            sort_keys=True,
        ),
    )

    print("\nINCREMENTAL COVERAGE:")
    for item in analysis["incremental"]:
        print(
            item["exchange"],
            "| listings=",
            item["listing_count"],
            "| isins=",
            item["isin_count"],
            "| new=",
            item["new_isin_count"],
            "| already_covered=",
            item["already_covered_isin_count"],
            "| incremental_percent=",
            item["incremental_over_exchange_percent"],
            "| cumulative=",
            item["cumulative_isin_count"],
            sep="",
        )

    print("\nPAIRWISE ISIN OVERLAP:")
    for item in analysis["pairwise"]:
        print(
            item["left_exchange"],
            "vs",
            item["right_exchange"],
            "| shared=",
            item["shared_isin_count"],
            "| left%=",
            item["shared_over_left_percent"],
            "| right%=",
            item["shared_over_right_percent"],
            "| jaccard%=",
            item["isin_jaccard_percent"],
            "| symbol_conflicts=",
            item["same_symbol_different_isin_count"],
            sep="",
        )


def main() -> int:
    args = parse_args()

    if args.timeout_seconds <= 0:
        raise SystemExit(
            "--timeout-seconds must be > 0"
        )

    exchange_order = list(dict.fromkeys(args.exchanges))
    if len(exchange_order) < 2:
        raise SystemExit(
            "at least two distinct exchange codes are required"
        )

    api_token = os.environ.get("EODHD_API_TOKEN")
    if not api_token or not api_token.strip():
        raise SystemExit(
            "EODHD_API_TOKEN is required but was not found"
        )

    discovery = EODHDExchangeDiscoveryProvider(
        api_token=api_token,
        timeout_seconds=args.timeout_seconds,
    ).discover()
    if discovery.status is ExchangeDiscoveryStatus.FAILED:
        raise SystemExit("EODHD exchange discovery failed")

    by_code = {
        descriptor.code: descriptor
        for descriptor in discovery.exchanges
    }
    missing = [
        code
        for code in exchange_order
        if code not in by_code
    ]
    if missing:
        raise SystemExit(
            "exchanges not present in discovery: "
            + ", ".join(missing)
        )

    descriptors = {
        code: by_code[code]
        for code in exchange_order
    }
    unusable = [
        code
        for code, descriptor in descriptors.items()
        if descriptor.is_virtual or descriptor.region is None
    ]
    if unusable:
        raise SystemExit(
            "exchanges are not physical classified exchanges: "
            + ", ".join(unusable)
        )

    listings_by_exchange: dict[
        str,
        tuple[RawMarketListing, ...],
    ] = {}
    acquisitions: list[dict[str, Any]] = []

    for code in exchange_order:
        descriptor = descriptors[code]
        provider = EODHDExchangeSymbolProvider(
            descriptor=descriptor,
            api_token=api_token,
            timeout_seconds=args.timeout_seconds,
        )
        started = time.perf_counter()
        result = provider.fetch(
            ExchangeSymbolRequest(code)
        )
        elapsed = time.perf_counter() - started

        print(
            code,
            "|",
            result.status.value,
            "| listings:",
            len(result.listings),
            "| diagnostics:",
            len(result.diagnostics),
            "| elapsed_seconds:",
            round(elapsed, 3),
        )

        if result.status is ExchangeProviderStatus.FAILED:
            codes = ", ".join(
                item.code
                for item in result.diagnostics
            )
            raise SystemExit(
                f"{code} acquisition failed: {codes}"
            )

        listings_by_exchange[code] = result.listings
        acquisitions.append(
            {
                "exchange": code,
                "descriptor": descriptor_record(descriptor),
                "provider_status": result.status.value,
                "listing_count": len(result.listings),
                "diagnostic_count": len(result.diagnostics),
                "diagnostic_codes": dict(
                    sorted(
                        Counter(
                            item.code
                            for item in result.diagnostics
                        ).items()
                    )
                ),
                "elapsed_seconds": round(elapsed, 3),
                "response_bytes": result.metadata.get(
                    "response_bytes"
                ),
                "source_url": result.source_url,
            }
        )

    analyses: dict[str, Any] = {}
    for scope in SCOPES:
        scoped = {
            code: scope_listings(
                listings_by_exchange[code],
                scope,
            )
            for code in exchange_order
        }
        pairwise = [
            pairwise_record(
                left_code=left_code,
                left=scoped[left_code],
                right_code=right_code,
                right=scoped[right_code],
            )
            for left_code, right_code in combinations(
                exchange_order,
                2,
            )
        ]
        analyses[scope] = {
            "prevalence": prevalence_record(
                exchange_order=exchange_order,
                scoped=scoped,
            ),
            "incremental": incremental_records(
                exchange_order=exchange_order,
                scoped=scoped,
            ),
            "pairwise": pairwise,
        }
        print_scope(scope, analyses[scope])

    generated_at = datetime.now(timezone.utc)
    report = {
        "report_version": "1",
        "generated_at": generated_at.isoformat(),
        "discovery_status": discovery.status.value,
        "exchange_order": exchange_order,
        "acquisitions": acquisitions,
        "analyses": analyses,
        "security": {
            "api_token_persisted": False,
            "raw_listings_persisted": False,
        },
    }

    args.output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    output_path = args.output_directory / (
        "eodhd_cross_exchange_overlap_"
        + generated_at.strftime("%Y%m%dT%H%M%SZ")
        + ".json"
    )
    output_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print("\nreport:", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
