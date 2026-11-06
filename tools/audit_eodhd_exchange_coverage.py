from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


DEFAULT_OUTPUT_DIRECTORY = Path(
    "data/cache/scanner/exchange_audit"
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
            "Collect quantitative EODHD exchange-coverage "
            "statistics without changing Scanner policy."
        )
    )
    parser.add_argument(
        "exchanges",
        nargs="+",
        type=exchange_code,
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
    )
    return parser.parse_args()


def sorted_counts(
    values,
) -> dict[str, int]:
    counts = Counter(
        value if value is not None else "<MISSING>"
        for value in values
    )
    return dict(
        sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    )


def descriptor_record(
    descriptor: ExchangeDescriptor,
) -> dict[str, Any]:
    return {
        "code": descriptor.code,
        "name": descriptor.name,
        "operating_mic": descriptor.operating_mic,
        "country": descriptor.country,
        "currency": descriptor.currency,
        "country_iso2": descriptor.country_iso2,
        "country_iso3": descriptor.country_iso3,
        "region": descriptor.region,
        "is_virtual": descriptor.is_virtual,
    }


def audit_exchange(
    *,
    descriptor: ExchangeDescriptor,
    api_token: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    provider = EODHDExchangeSymbolProvider(
        descriptor=descriptor,
        api_token=api_token,
        timeout_seconds=timeout_seconds,
    )

    started = time.perf_counter()
    result = provider.fetch(
        ExchangeSymbolRequest(descriptor.code)
    )
    elapsed = time.perf_counter() - started

    listings = result.listings
    isin_counts = Counter(
        item.isin
        for item in listings
        if item.isin is not None
    )
    symbol_counts = Counter(
        item.symbol
        for item in listings
    )

    duplicate_isins = sorted(
        value
        for value, count in isin_counts.items()
        if count > 1
    )
    duplicate_symbols = sorted(
        value
        for value, count in symbol_counts.items()
        if count > 1
    )

    return {
        "descriptor": descriptor_record(descriptor),
        "provider_status": result.status.value,
        "elapsed_seconds": round(elapsed, 3),
        "source_url": result.source_url,
        "raw_row_count": result.metadata.get(
            "raw_row_count"
        ),
        "valid_listing_count": len(listings),
        "rejected_row_count": result.metadata.get(
            "rejected_row_count"
        ),
        "duplicate_row_count": result.metadata.get(
            "duplicate_row_count"
        ),
        "response_bytes": result.metadata.get(
            "response_bytes"
        ),
        "missing_isin_count": sum(
            item.isin is None
            for item in listings
        ),
        "duplicate_isin_count": len(
            duplicate_isins
        ),
        "duplicate_symbol_count": len(
            duplicate_symbols
        ),
        "duplicate_isin_examples": duplicate_isins[:20],
        "duplicate_symbol_examples": (
            duplicate_symbols[:20]
        ),
        "instrument_types": sorted_counts(
            item.instrument_type
            for item in listings
        ),
        "currencies": sorted_counts(
            item.currency
            for item in listings
        ),
        "countries": sorted_counts(
            item.country
            for item in listings
        ),
        "diagnostics": [
            {
                "code": item.code,
                "message": item.message,
                "symbol": item.symbol,
            }
            for item in result.diagnostics
        ],
    }


def failed_audit(
    *,
    exchange: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "descriptor": {"code": exchange},
        "provider_status": (
            ExchangeProviderStatus.FAILED.value
        ),
        "diagnostics": [
            {
                "code": code,
                "message": message,
                "symbol": None,
            }
        ],
    }


def print_audit(audit: dict[str, Any]) -> None:
    descriptor = audit["descriptor"]

    print(
        "\n"
        + " | ".join(
            (
                descriptor.get("code", "<UNKNOWN>"),
                descriptor.get("name", "<UNKNOWN>"),
                audit["provider_status"],
            )
        )
    )

    if audit["provider_status"] == "FAILED":
        for item in audit["diagnostics"]:
            print(
                "diagnostic:",
                item["code"],
                item["message"],
            )
        return

    print(
        "rows:",
        audit["raw_row_count"],
        "valid:",
        audit["valid_listing_count"],
        "rejected:",
        audit["rejected_row_count"],
        "duplicates:",
        audit["duplicate_row_count"],
    )
    print(
        "elapsed_seconds:",
        audit["elapsed_seconds"],
        "response_bytes:",
        audit["response_bytes"],
        "missing_isin:",
        audit["missing_isin_count"],
    )
    print(
        "duplicate_isins:",
        audit["duplicate_isin_count"],
        "duplicate_symbols:",
        audit["duplicate_symbol_count"],
    )
    print(
        "instrument_types:",
        json.dumps(
            audit["instrument_types"],
            ensure_ascii=False,
        ),
    )
    print(
        "currencies:",
        json.dumps(
            audit["currencies"],
            ensure_ascii=False,
        ),
    )
    print(
        "countries:",
        json.dumps(
            audit["countries"],
            ensure_ascii=False,
        ),
    )

    diagnostics = audit["diagnostics"]

    for item in diagnostics[:10]:
        print(
            "diagnostic:",
            item["code"],
            item["message"],
        )

    if len(diagnostics) > 10:
        print(
            "diagnostics_omitted:",
            len(diagnostics) - 10,
        )


def main() -> int:
    args = parse_args()

    if args.timeout_seconds <= 0:
        raise SystemExit(
            "--timeout-seconds must be > 0"
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
        item.code: item
        for item in discovery.exchanges
    }

    audits = []
    for code in args.exchanges:
        descriptor = by_code.get(code)

        if descriptor is None:
            audit = failed_audit(
                exchange=code,
                code="EXCHANGE_NOT_DISCOVERED",
                message=(
                    "Requested exchange is not present "
                    "in EODHD discovery"
                ),
            )
        elif descriptor.is_virtual:
            audit = failed_audit(
                exchange=code,
                code="VIRTUAL_EXCHANGE_REJECTED",
                message=(
                    "Virtual exchanges are outside this "
                    "coverage audit"
                ),
            )
        else:
            try:
                audit = audit_exchange(
                    descriptor=descriptor,
                    api_token=api_token,
                    timeout_seconds=args.timeout_seconds,
                )
            except Exception as exc:
                audit = failed_audit(
                    exchange=code,
                    code="AUDIT_EXCEPTION",
                    message=type(exc).__name__,
                )

        audits.append(audit)
        print_audit(audit)

    generated_at = datetime.now(
        timezone.utc
    ).isoformat()

    report = {
        "report_version": "1",
        "generated_at": generated_at,
        "discovery_status": discovery.status.value,
        "requested_exchanges": args.exchanges,
        "audits": audits,
    }

    args.output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    filename = (
        "eodhd_exchange_coverage_"
        + datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        + ".json"
    )
    output_path = args.output_directory / filename
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
    print("discovery_status:", discovery.status.value)

    return (
        1
        if any(
            item["provider_status"] == "FAILED"
            for item in audits
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
