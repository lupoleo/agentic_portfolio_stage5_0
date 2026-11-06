from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.scanner.eodhd_exchange_discovery import (
    EODHDExchangeDiscoveryProvider,
    ExchangeDiscoveryStatus,
)
from app.scanner.exchange_policy import scanner_v1_production_policy
from app.scanner.instrument_taxonomy import (
    RAW_TYPE_MAP,
    normalize_raw_instrument_type,
    resolve_instrument_type,
)
from app.scanner.provider_composition import compose_exchange_universe
from tools.live_scanner_production_policy import build_live_registry


DEFAULT_CACHE_DIR = Path("data/cache/scanner/exchange_symbols")
DEFAULT_OUTPUT_DIR = Path("data/cache/scanner/instrument_audit")
MISSING = "<MISSING>"
SECURITY_FORM_PATTERNS = (
    ("WARRANT", re.compile(r"\bWARRANTS?\b", re.IGNORECASE)),
    ("RIGHT", re.compile(r"\bRIGHTS?\b", re.IGNORECASE)),
    ("UNIT", re.compile(r"\bUNITS?\b", re.IGNORECASE)),
    ("NOTE", re.compile(r"\bNOTES?\b", re.IGNORECASE)),
    (
        "BOND",
        re.compile(r"\b(?:BONDS?|DEBENTURES?)\b", re.IGNORECASE),
    ),
    ("ETF_ETP", re.compile(r"\b(?:ETF|ETP)\b", re.IGNORECASE)),
)
COLLECTIVE_VEHICLE_PATTERNS = (
    ("FUND", re.compile(r"\bFUND\b", re.IGNORECASE)),
    ("TRUST", re.compile(r"\bTRUST\b", re.IGNORECASE)),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit instrument taxonomy in the cached Scanner V1 "
            "production universe without applying eligibility filters."
        )
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--samples-per-type", type=int, default=5)
    parser.add_argument("--review-samples", type=int, default=100)
    return parser.parse_args()


def raw_type(value: str | None) -> str:
    return normalize_raw_instrument_type(value) or MISSING


def normalized_type(value: str | None) -> str:
    return resolve_instrument_type(value).canonical_type.value


def normalized_value(value: str | None) -> str:
    return value.strip().upper() if value and value.strip() else MISSING


def percentage(count: int, total: int) -> float:
    return round(100.0 * count / total, 3) if total else 0.0


def sorted_counter(values) -> dict[str, int]:
    counts = Counter(values)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def listing_record(item) -> dict[str, object]:
    return {
        "exchange": item.exchange,
        "symbol": item.symbol,
        "name": item.name,
        "instrument_type": normalized_type(item.instrument_type),
        "isin": item.isin,
        "currency": item.currency,
        "country": item.country,
    }


def type_statistics(listings) -> list[dict[str, object]]:
    grouped = defaultdict(list)
    for item in listings:
        grouped[normalized_type(item.instrument_type)].append(item)

    total = len(listings)
    result = []
    for instrument_type, items in grouped.items():
        result.append(
            {
                "instrument_type": instrument_type,
                "count": len(items),
                "percent": percentage(len(items), total),
                "missing_isin": sum(item.isin is None for item in items),
                "missing_name": sum(item.name is None for item in items),
                "currencies": sorted_counter(
                    normalized_value(item.currency) for item in items
                ),
                "venues": sorted_counter(item.exchange for item in items),
            }
        )
    return sorted(
        result,
        key=lambda item: (-item["count"], item["instrument_type"]),
    )


def venue_statistics(listings) -> list[dict[str, object]]:
    grouped = defaultdict(list)
    for item in listings:
        grouped[item.exchange].append(item)

    result = []
    for exchange, items in grouped.items():
        isin_counts = Counter(
            item.isin for item in items if item.isin is not None
        )
        duplicate_isins = {
            isin: count
            for isin, count in isin_counts.items()
            if count > 1
        }
        result.append(
            {
                "exchange": exchange,
                "listing_count": len(items),
                "instrument_types": sorted_counter(
                    normalized_type(item.instrument_type) for item in items
                ),
                "currencies": sorted_counter(
                    normalized_value(item.currency) for item in items
                ),
                "missing_isin": sum(item.isin is None for item in items),
                "missing_name": sum(item.name is None for item in items),
                "duplicate_isin_count": len(duplicate_isins),
                "duplicate_isin_examples": dict(
                    sorted(duplicate_isins.items())[:20]
                ),
            }
        )
    return sorted(result, key=lambda item: item["exchange"])


def samples_by_type(
    listings,
    limit: int,
) -> dict[str, list[dict[str, object]]]:
    grouped = defaultdict(list)
    for item in sorted(
        listings,
        key=lambda value: (value.exchange, value.symbol),
    ):
        instrument_type = normalized_type(item.instrument_type)
        if len(grouped[instrument_type]) < limit:
            grouped[instrument_type].append(listing_record(item))
    return dict(sorted(grouped.items()))


def classification_review_candidates(listings, limit: int):
    categories = {
        "SECURITY_FORM_MISMATCH": [],
        "COLLECTIVE_VEHICLE_REVIEW": [],
    }
    category_counts = Counter()
    token_counts = Counter()
    venue_counts = Counter()
    for item in sorted(
        listings,
        key=lambda value: (value.exchange, value.symbol),
    ):
        if normalized_type(item.instrument_type) != "COMMON_STOCK":
            continue
        name = item.name or ""
        security_tokens = tuple(
            label
            for label, pattern in SECURITY_FORM_PATTERNS
            if pattern.search(name)
        )
        collective_tokens = tuple(
            label
            for label, pattern in COLLECTIVE_VEHICLE_PATTERNS
            if pattern.search(name)
        )
        category = None
        matched = ()
        if security_tokens:
            category = "SECURITY_FORM_MISMATCH"
            matched = security_tokens
        elif collective_tokens:
            category = "COLLECTIVE_VEHICLE_REVIEW"
            matched = collective_tokens
        if category is None:
            continue

        category_counts[category] += 1
        venue_counts[item.exchange] += 1
        token_counts.update(matched)
        if len(categories[category]) < limit:
            record = listing_record(item)
            record["matched_tokens"] = matched
            categories[category].append(record)
    return {
        "description": (
            "Word-boundary heuristic review only. Security-form mismatches "
            "are separated from collective vehicles whose listed shares may "
            "legitimately be represented as common stock."
        ),
        "match_count": sum(category_counts.values()),
        "category_counts": dict(sorted(category_counts.items())),
        "token_counts": dict(
            sorted(token_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "venue_counts": dict(
            sorted(venue_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "samples": categories,
    }


def duplicate_isin_statistics(listings) -> dict[str, object]:
    grouped = defaultdict(list)
    for item in listings:
        if item.isin:
            grouped[item.isin].append(item)
    duplicate = {
        isin: items for isin, items in grouped.items() if len(items) > 1
    }
    cross_venue = {
        isin: items
        for isin, items in duplicate.items()
        if len({item.exchange for item in items}) > 1
    }
    return {
        "unique_isins": len(grouped),
        "duplicate_isin_groups": len(duplicate),
        "cross_venue_isin_groups": len(cross_venue),
        "cross_venue_examples": [
            {
                "isin": isin,
                "listings": [
                    {
                        "exchange": item.exchange,
                        "symbol": item.symbol,
                        "instrument_type": normalized_type(
                            item.instrument_type
                        ),
                    }
                    for item in items
                ],
            }
            for isin, items in sorted(cross_venue.items())[:50]
        ],
    }


def scenario_statistics(listings) -> list[dict[str, object]]:
    scenarios = (
        ("COMMON_STOCK_ONLY", {"COMMON_STOCK"}),
        ("COMMON_STOCK_AND_ETF", {"COMMON_STOCK", "ETF"}),
        (
            "COMMON_STOCK_ETF_PREFERRED",
            {"COMMON_STOCK", "ETF", "PREFERRED_STOCK"},
        ),
    )
    total = len(listings)
    result = []
    for name, allowed_types in scenarios:
        selected = [
            item
            for item in listings
            if normalized_type(item.instrument_type) in allowed_types
        ]
        result.append(
            {
                "scenario": name,
                "allowed_types": sorted(allowed_types),
                "listing_count": len(selected),
                "percent_of_universe": percentage(len(selected), total),
                "venue_count": len({item.exchange for item in selected}),
                "missing_isin": sum(item.isin is None for item in selected),
            }
        )
    return result


def build_report(result, policy, discovery, args):
    listings = tuple(result.universe)
    return {
        "audit_id": "E2E-S2.2A",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "composition_status": result.status.value,
        "discovery_status": discovery.status.value,
        "universe_count": len(listings),
        "venue_count": len({item.exchange for item in listings}),
        "composition_metadata": result.metadata,
        "provider_diagnostics": [asdict(item) for item in result.diagnostics],
        "raw_instrument_type_values": sorted_counter(
            raw_type(item.instrument_type) for item in listings
        ),
        "raw_type_mapping": {
            raw: canonical.value
            for raw, canonical in sorted(RAW_TYPE_MAP.items())
        },
        "global_instrument_types": type_statistics(listings),
        "venue_statistics": venue_statistics(listings),
        "samples_by_type": samples_by_type(
            listings,
            args.samples_per_type,
        ),
        "classification_review": classification_review_candidates(
            listings,
            args.review_samples,
        ),
        "isin_statistics": duplicate_isin_statistics(listings),
        "hypothetical_scenarios": scenario_statistics(listings),
        "decision": (
            "AUDIT_ONLY: no instrument eligibility policy was applied"
        ),
    }


def print_report(report) -> None:
    print("\nGLOBAL INSTRUMENT TYPES:")
    for item in report["global_instrument_types"]:
        print(
            f"{item['instrument_type']} | count={item['count']} | "
            f"percent={item['percent']} | missing_isin={item['missing_isin']}"
        )

    print("\nVENUES:")
    for item in report["venue_statistics"]:
        types = json.dumps(item["instrument_types"], separators=(",", ":"))
        print(
            f"{item['exchange']} | listings={item['listing_count']} | "
            f"missing_isin={item['missing_isin']} | types={types}"
        )

    print("\nHYPOTHETICAL SCENARIOS:")
    for item in report["hypothetical_scenarios"]:
        print(
            f"{item['scenario']} | listings={item['listing_count']} | "
            f"percent={item['percent_of_universe']} | "
            f"venues={item['venue_count']} | "
            f"missing_isin={item['missing_isin']}"
        )

    review = report["classification_review"]
    isin = report["isin_statistics"]
    print("\nQUALITY INDICATORS:")
    print("classification_review_matches:", review["match_count"])
    print("classification_review_categories:", review["category_counts"])
    print("classification_review_tokens:", review["token_counts"])
    print("classification_review_venues:", review["venue_counts"])
    print("unique_isins:", isin["unique_isins"])
    print("duplicate_isin_groups:", isin["duplicate_isin_groups"])
    print("cross_venue_isin_groups:", isin["cross_venue_isin_groups"])


def main() -> int:
    args = parse_args()
    if args.timeout_seconds <= 0:
        raise ValueError("timeout-seconds must be > 0")
    if args.samples_per_type < 0:
        raise ValueError("samples-per-type must be >= 0")
    if args.review_samples < 0:
        raise ValueError("review-samples must be >= 0")

    policy = scanner_v1_production_policy()
    acquisition_codes = sorted(
        {entry.acquisition_code for entry in policy.enabled_entries}
    )

    print("audit: E2E-S2.2A")
    print("mode: cache-only")
    print("policy_id:", policy.policy_id)
    print("discovery: started", flush=True)
    discovery = EODHDExchangeDiscoveryProvider(
        timeout_seconds=args.timeout_seconds,
    ).discover()
    print("discovery_status:", discovery.status.value, flush=True)
    if discovery.status is ExchangeDiscoveryStatus.FAILED:
        print("AUDIT_STATUS: FAILED")
        return 1

    registry_args = SimpleNamespace(
        cache_dir=args.cache_dir,
        ttl_hours=24.0,
        mode="cache-only",
        allow_stale_on_error=False,
        max_stale_days=7.0,
        timeout_seconds=args.timeout_seconds,
    )
    registry = build_live_registry(
        registry_args,
        discovery.exchanges,
        acquisition_codes,
    )
    result = compose_exchange_universe(
        policy=policy,
        providers=registry,
    )
    if not result.universe:
        print("AUDIT_STATUS: FAILED")
        return 1

    report = build_report(result, policy, discovery, args)
    args.output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output_directory / (
        f"scanner_instrument_taxonomy_{timestamp}.json"
    )
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print_report(report)
    print("\nreport:", output_path)
    print("AUDIT_STATUS: SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
