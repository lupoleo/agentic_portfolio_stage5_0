"""Offline mapping audit from an S2.2B eligibility report."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from app.scanner.universe_models import ListingKey
from app.scanner.yahoo_symbol_resolver import YahooSymbolResolver


def build_mapping_audit(data: dict) -> dict:
    if data.get("audit_id") != "E2E-S2.2B":
        raise ValueError("Expected an S2.2B eligibility report")
    rows = data.get("all_decisions")
    if not isinstance(rows, list):
        raise ValueError("Missing all_decisions array")
    seen = set()
    eligible = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid decision record")
        for field in ("exchange", "symbol"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"Missing {field}")
        key = ListingKey(row["exchange"], row["symbol"])
        if key in seen:
            raise ValueError("Duplicate listing key in eligibility report")
        seen.add(key)
        status = row.get("status")
        if status not in {"ELIGIBLE", "INELIGIBLE", "REVIEW_REQUIRED"}:
            raise ValueError("Invalid eligibility status")
        if status == "ELIGIBLE":
            eligible.append(key)
    if data.get("decision_count") != len(rows):
        raise ValueError("Eligibility decision count mismatch")
    if data.get("automatically_admitted_count") != len(eligible):
        raise ValueError("Eligibility admitted count mismatch")
    resolver = YahooSymbolResolver()
    results = resolver.resolve_many(eligible)
    records = [{
        "exchange": r.listing_key.exchange, "symbol": r.listing_key.symbol,
        "mapping_status": r.status.value, "yahoo_symbol": r.resolved_symbol,
        "candidate_symbols": list(r.candidate_symbols), "rule_id": r.rule_id,
        "diagnostics": [{"code": d.code, "message": d.message} for d in r.diagnostics],
    } for r in results]
    by_venue = {}
    for record in records:
        by_venue.setdefault(record["exchange"], Counter())[record["mapping_status"]] += 1
    return {
        "audit_id": "E2E-S2.2C-MAPPING", "network_calls": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_generated_at": data.get("generated_at"),
        "source_eligibility_policy_id": data.get("eligibility_policy_id"),
        "source_eligibility_policy_version": data.get("eligibility_policy_version"),
        "mapping_version": resolver.mapping_version, "provider_id": "yahoo",
        "eligible_input_count": len(eligible), "mapping_count": len(records),
        "status_counts": dict(Counter(r["mapping_status"] for r in records)),
        "diagnostic_counts": dict(Counter(d["code"] for r in records for d in r["diagnostics"])),
        "status_by_venue": {k: dict(v) for k, v in sorted(by_venue.items())},
        "mappings": records,
        "note": "Candidate syntax only. Identity, availability and freshness not checked.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-report", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path,
                        default=Path("data/cache/scanner/market_data_audit"))
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    if args.samples < 0:
        parser.error("samples must be nonnegative")
    report = build_mapping_audit(json.loads(args.input_report.read_text(encoding="utf-8-sig")))
    args.output_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = args.output_directory / f"scanner_yahoo_mapping_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for field in ("network_calls", "eligible_input_count", "mapping_count", "status_counts", "diagnostic_counts"):
        print(f"{field}: {report[field]}")
    for venue, counts in report["status_by_venue"].items():
        print(f"{venue} | {counts}")
    print("\nUNRESOLVED SAMPLES:")
    unresolved = [r for r in report["mappings"] if r["mapping_status"] != "RESOLVED"]
    for row in unresolved[:args.samples]:
        print(f"{row['exchange']} | {row['symbol']} | {row['diagnostics']}")
    print(f"\nreport: {path}")
    print("AUDIT_STATUS: SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
