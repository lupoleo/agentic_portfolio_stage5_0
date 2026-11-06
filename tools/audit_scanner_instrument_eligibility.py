from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.scanner.eodhd_exchange_discovery import (
    EODHDExchangeDiscoveryProvider,
    ExchangeDiscoveryStatus,
)
from app.scanner.exchange_policy import scanner_v1_production_policy
from app.scanner.instrument_eligibility import (
    InstrumentEligibilityStatus,
    evaluate_instrument_eligibility,
    scanner_v1_instrument_eligibility_policy,
)
from app.scanner.provider_composition import compose_exchange_universe
from tools.live_scanner_production_policy import build_live_registry


DEFAULT_CACHE_DIR = Path("data/cache/scanner/exchange_symbols")
DEFAULT_OUTPUT_DIR = Path("data/cache/scanner/instrument_audit")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit Scanner V1 instrument eligibility against the cached "
            "production universe."
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
    parser.add_argument("--review-samples", type=int, default=100)
    return parser.parse_args()


def decision_record(listing, decision) -> dict[str, object]:
    return {
        "exchange": listing.exchange,
        "symbol": listing.symbol,
        "name": listing.name,
        "isin": listing.isin,
        "currency": listing.currency,
        "raw_instrument_type": decision.raw_instrument_type,
        "canonical_type": decision.canonical_type.value,
        "status": decision.status.value,
        "reason_code": decision.reason_code.value,
        "review_flags": [item.value for item in decision.review_flags],
        "policy_id": decision.policy_id,
        "policy_version": decision.policy_version,
    }


def nested_counts(decisions, attribute: str) -> dict[str, dict[str, int]]:
    grouped = defaultdict(Counter)
    for listing, decision in decisions:
        value = getattr(decision, attribute)
        label = value.value if hasattr(value, "value") else str(value)
        grouped[listing.exchange][label] += 1
    return {
        exchange: dict(
            sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        )
        for exchange, counts in sorted(grouped.items())
    }


def build_report(result, policy, eligibility_policy, discovery, sample_limit):
    decisions = [
        (
            listing,
            evaluate_instrument_eligibility(
                listing,
                policy=eligibility_policy,
            ),
        )
        for listing in result.universe
    ]
    status_counts = Counter(
        decision.status.value for _, decision in decisions
    )
    reason_counts = Counter(
        decision.reason_code.value for _, decision in decisions
    )
    type_counts = Counter(
        decision.canonical_type.value for _, decision in decisions
    )
    review_decisions = [
        (listing, decision)
        for listing, decision in decisions
        if decision.status is InstrumentEligibilityStatus.REVIEW_REQUIRED
    ]
    flag_counts = Counter(
        flag.value
        for _, decision in review_decisions
        for flag in decision.review_flags
    )
    review = [
        decision_record(listing, decision)
        for listing, decision in review_decisions
    ]
    admitted = [
        decision_record(listing, decision)
        for listing, decision in decisions
        if decision.automatically_admitted
    ]

    return {
        "audit_id": "E2E-S2.2B",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exchange_policy_id": policy.policy_id,
        "eligibility_policy_id": eligibility_policy.policy_id,
        "eligibility_policy_version": eligibility_policy.policy_version,
        "composition_status": result.status.value,
        "discovery_status": discovery.status.value,
        "universe_count": len(result.universe),
        "decision_count": len(decisions),
        "status_counts": dict(sorted(status_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "canonical_type_counts": dict(sorted(type_counts.items())),
        "review_flag_counts": dict(sorted(flag_counts.items())),
        "status_by_venue": nested_counts(decisions, "status"),
        "reason_by_venue": nested_counts(decisions, "reason_code"),
        "review_count": len(review),
        "review_samples": review[:sample_limit],
        "automatically_admitted_count": len(admitted),
        "automatically_admitted_keys": [
            {
                "exchange": item["exchange"],
                "symbol": item["symbol"],
            }
            for item in admitted
        ],
        "all_decisions": [
            decision_record(listing, decision)
            for listing, decision in decisions
        ],
        "composition_metadata": result.metadata,
        "decision": (
            "Policy evaluation only: market-data, liquidity and history "
            "gates were not applied"
        ),
    }


def print_report(report) -> None:
    print("\nELIGIBILITY SUMMARY:")
    print("universe_count:", report["universe_count"])
    print("decision_count:", report["decision_count"])
    print(
        "automatically_admitted_count:",
        report["automatically_admitted_count"],
    )
    print("review_count:", report["review_count"])
    print("status_counts:", report["status_counts"])
    print("reason_counts:", report["reason_counts"])
    print("review_flag_counts:", report["review_flag_counts"])

    print("\nSTATUS BY VENUE:")
    for exchange, counts in report["status_by_venue"].items():
        print(
            f"{exchange} | "
            + json.dumps(counts, separators=(",", ":"))
        )

    print("\nREVIEW SAMPLES:")
    for item in report["review_samples"]:
        print(
            f"{item['exchange']} | {item['symbol']} | "
            f"{item['name']} | {','.join(item['review_flags'])}"
        )


def main() -> int:
    args = parse_args()
    if args.timeout_seconds <= 0:
        raise ValueError("timeout-seconds must be > 0")
    if args.review_samples < 0:
        raise ValueError("review-samples must be >= 0")

    exchange_policy = scanner_v1_production_policy()
    eligibility_policy = scanner_v1_instrument_eligibility_policy()
    acquisition_codes = sorted(
        {
            entry.acquisition_code
            for entry in exchange_policy.enabled_entries
        }
    )

    print("audit: E2E-S2.2B")
    print("mode: cache-only")
    print("exchange_policy_id:", exchange_policy.policy_id)
    print("eligibility_policy_id:", eligibility_policy.policy_id)
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
        policy=exchange_policy,
        providers=registry,
    )
    if not result.universe:
        print("AUDIT_STATUS: FAILED")
        return 1

    report = build_report(
        result,
        exchange_policy,
        eligibility_policy,
        discovery,
        args.review_samples,
    )
    args.output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output_directory / (
        f"scanner_instrument_eligibility_{timestamp}.json"
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
