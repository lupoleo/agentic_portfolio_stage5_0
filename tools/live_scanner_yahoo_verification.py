"""Small sequential Yahoo verification sample; no discovery or universe refresh."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import hashlib
import math
import os
from pathlib import Path
import tempfile
import time

from app.scanner.universe_models import MarketListing
from app.scanner.yahoo_market_data_verifier import YahooMarketDataVerifier, YAHOO_VENUE_CODES
from app.scanner.yahoo_symbol_resolver import YahooSymbolResolver
from app.scanner.market_data_verification_cache import CachedMarketDataVerifier, MODES
from tools.audit_scanner_yahoo_mapping import build_mapping_audit


# Prefer familiar symbols when present and ELIGIBLE; otherwise choose sorted keys.
PREFERRED = {"BIT": "A2A", "XETRA": "SAP", "NASDAQ": "MSFT", "NYSE": "IBM",
             "LSE": "SHEL", "AS": "ASML", "PA": "AIR", "SW": "NESN",
             "CO": "NOVO-B", "ST": "VOLV-B", "HE": "NOKIA", "OL": "EQNR"}


def select_sample(data, venues, per_venue):
    # Validate the complete eligibility report, and detect collisions before sampling.
    audit = build_mapping_audit(data)
    resolved = {(r["exchange"], r["symbol"]) for r in audit["mappings"]
                if r["mapping_status"] == "RESOLVED"}
    if not venues or len(venues) != len(set(venues)):
        raise ValueError("venue selection must be nonempty and unique")
    if not 1 <= per_venue <= 3:
        raise ValueError("per_venue must be 1..3 for this pilot")
    selected = []
    for venue in venues:
        if venue not in YAHOO_VENUE_CODES:
            raise ValueError(f"No verification rule for venue {venue}")
        rows = [r for r in data["all_decisions"] if r["exchange"] == venue
                and r["status"] == "ELIGIBLE" and (venue, r["symbol"]) in resolved]
        rows.sort(key=lambda r: (r["symbol"] != PREFERRED.get(venue), r["symbol"]))
        if len(rows) < per_venue:
            raise ValueError(f"Insufficient eligible resolved listings for {venue}")
        selected.extend(rows[:per_venue])
    return selected


def atomic_report(path, report):
    def encode(value):
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Cannot serialize {type(value).__name__}")
    payload = json.dumps(report, default=encode, indent=2, ensure_ascii=False) + "\n"
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as handle:
            temp = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def run_context(data, selected, lookback_days, resume=None):
    signature = hashlib.sha256(json.dumps({"source": data, "selected": selected},
                                          sort_keys=True).encode()).hexdigest()
    if resume is not None:
        if (resume.get("audit_id") != "E2E-S2.2C-VERIFICATION-PILOT"
                or resume.get("resume_signature") != signature
                or resume.get("lookback_days") != lookback_days):
            raise ValueError("Resume requires the same source report, sample and lookback")
        start = datetime.fromisoformat(resume["requested_start"])
        end = datetime.fromisoformat(resume["requested_end"])
        if (start.utcoffset() is None or end.utcoffset() is None
                or end - start != timedelta(days=lookback_days)):
            raise ValueError("Invalid resume window")
    else:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
    return signature, start, end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-report", type=Path, required=True)
    parser.add_argument("--venues", nargs="+", default=["BIT", "XETRA", "NASDAQ", "NYSE"])
    parser.add_argument("--per-venue", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=20.)
    parser.add_argument("--pause-seconds", type=float, default=1.)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--mode", choices=MODES, default="prefer-cache")
    parser.add_argument("--cache-directory", type=Path,
                        default=Path("data/cache/scanner/market_data_verifications"))
    parser.add_argument("--resume-report", type=Path)
    parser.add_argument("--output-directory", type=Path,
                        default=Path("data/cache/scanner/market_data_audit"))
    args = parser.parse_args()
    if not 1 <= args.lookback_days <= 90:
        parser.error("lookback-days must be 1..90")
    if not math.isfinite(args.pause_seconds) or not 0 <= args.pause_seconds <= 30:
        parser.error("pause-seconds must be 0..30")
    data = json.loads(args.input_report.read_text(encoding="utf-8-sig"))
    selected = select_sample(data, [v.upper() for v in args.venues], args.per_venue)
    verifier = CachedMarketDataVerifier(
        YahooMarketDataVerifier(timeout_seconds=args.timeout_seconds),
        args.cache_directory, mode=args.mode)
    resolver = YahooSymbolResolver()
    resume = json.loads(args.resume_report.read_text(encoding="utf-8-sig")) if args.resume_report else None
    signature, start, end = run_context(data, selected, args.lookback_days, resume)
    args.output_directory.mkdir(parents=True, exist_ok=True)
    path = args.output_directory / ("scanner_yahoo_verification_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    import yfinance as yf
    report = {"audit_id": "E2E-S2.2C-VERIFICATION-PILOT", "run_status": "RUNNING",
              "yfinance_version": yf.__version__, "source_report": args.input_report.name,
              "source_generated_at": data.get("generated_at"),
              "resume_signature": signature, "lookback_days": args.lookback_days,
              "mode": args.mode, "cache_directory": str(args.cache_directory),
              "resumed_from": args.resume_report.name if args.resume_report else None,
              "planned_count": len(selected), "requested_start": start, "requested_end": end,
              "results": []}
    atomic_report(path, report)
    print(f"sample_count: {len(selected)} | yfinance: {yf.__version__}", flush=True)
    print(f"report: {path}", flush=True)
    try:
        for index, row in enumerate(selected, 1):
            if index > 1:
                time.sleep(args.pause_seconds)
            venue, symbol = row["exchange"], row["symbol"]
            # Report lacks descriptive market/region; these local placeholders
            # are not identity evidence. Identity uses key/currency/type/ISIN.
            listing = MarketListing(symbol, venue, venue, "US" if venue in {
                "NASDAQ", "NYSE", "AMEX", "BATS", "NYSE ARCA"} else "EUROPE",
                currency=row.get("currency"), instrument_type=row.get("raw_instrument_type"),
                isin=row.get("isin"), name=row.get("name"))
            mapping = resolver.resolve(listing)
            print(f"VERIFY {index:02}/{len(selected):02} | {venue}:{symbol} -> {mapping.resolved_symbol} | started", flush=True)
            result = verifier.verify(listing, mapping, start=start, end=end)
            record = asdict(result)
            record["source_listing"] = asdict(listing)
            record["eligibility_policy_id"] = row.get("policy_id")
            record["eligibility_policy_version"] = row.get("policy_version")
            record["ready_at_check"] = result.ready_for_market_data(as_of=result.checked_at)
            report["results"].append(record)
            atomic_report(path, report)
            print(f"{venue}:{symbol} | {result.identity_status.value} | {result.availability_status.value} | bars={result.valid_bar_count} | ready={record['ready_at_check']} | cache={result.from_cache}", flush=True)
            print("metadata:", dict(result.identity_evidence), flush=True)
            for diagnostic in result.diagnostics:
                print(f"  {diagnostic.code}: {diagnostic.message}", flush=True)
            if any("RATE_LIMIT" in d.code for d in result.diagnostics):
                report["run_status"] = "RATE_LIMIT_STOP"
                break
        else:
            report["run_status"] = "COMPLETED"
    except KeyboardInterrupt:
        report["run_status"] = "INTERRUPTED"
    except Exception:
        report["run_status"] = "FAILED"
        print("Pilot failed unexpectedly; completed results retained.", flush=True)
    finally:
        report["completed_count"] = len(report["results"])
        report["ready_count"] = sum(r["ready_at_check"] for r in report["results"])
        report["identity_counts"] = dict(Counter(r["identity_status"] for r in report["results"]))
        report["availability_counts"] = dict(Counter(r["availability_status"] for r in report["results"]))
        report["cache_hits"] = verifier.cache_hits
        report["network_verifications"] = verifier.network_verifications
        report["cache_misses"] = verifier.cache_misses
        atomic_report(path, report)
    for field in ("run_status", "planned_count", "completed_count", "ready_count", "identity_counts", "availability_counts", "cache_hits", "network_verifications", "cache_misses"):
        print(f"{field}: {report[field]}")
    if report["run_status"] == "INTERRUPTED":
        return 130
    if report["run_status"] == "FAILED":
        return 1
    passed = report["run_status"] == "COMPLETED" and report["ready_count"] == len(selected)
    print("LIVE_VERIFICATION:", "PASS" if passed else "PARTIAL")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
