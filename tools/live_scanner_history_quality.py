"""Small history-gate pilot with ECB FX and reviewed listing evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
import json
import math
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from app.scanner.ecb_session_fx import ECBSessionFXProvider
from app.scanner.exchange_session_calendar import ExchangeSessionCalendarProvider
from app.scanner.history_quality import (
    evaluate_history_quality,
    history_quality_result_to_dict,
)
from app.scanner.history_quality_contracts import HistoryQualityPolicy
from app.scanner.listing_start_reference import ListingStartReferenceRegistry
from app.scanner.market_data_verification_cache import atomic_json
from app.scanner.universe_models import MarketListing
from app.scanner.yahoo_history_snapshot import YahooHistorySnapshotProvider
from app.scanner.yahoo_symbol_resolver import YahooSymbolResolver
from tools.audit_scanner_yahoo_mapping import build_mapping_audit
from tools.live_scanner_yahoo_verification import select_sample


def run_pilot(
    data,
    *,
    venues,
    output,
    history_provider,
    calendar_provider,
    now=lambda: datetime.now(timezone.utc),
    pause_seconds=2.,
    lookback_days=365,
    fx_provider=None,
    listing_references=None,
    listing_keys=None,
):
    if not venues or any(v not in {"BIT", "XETRA", "NYSE", "NASDAQ"} for v in venues):
        raise ValueError("Pilot supports BIT, XETRA, NYSE and NASDAQ only")

    if not math.isfinite(pause_seconds) or not 0 <= pause_seconds <= 30:
        raise ValueError("Invalid pause")

    if type(lookback_days) is not int or not 90 <= lookback_days <= 730:
        raise ValueError("Lookback must be 90..730 days")

    if listing_keys is None:
        selected = select_sample(data, venues, 1)
    else:
        # Validate the complete report before selecting explicit keys.
        audit = build_mapping_audit(data)
        keys = tuple(tuple(key.split(":")) for key in listing_keys)

        if (
            not 1 <= len(keys) <= 3
            or len(set(keys)) != len(keys)
            or any(len(k) != 2 or k[0] not in venues for k in keys)
        ):
            raise ValueError(
                "Select 1..3 unique EXCHANGE:SYMBOL keys within pilot venues"
            )

        resolved = {
            (r["exchange"], r["symbol"])
            for r in audit["mappings"]
            if r["mapping_status"] == "RESOLVED"
        }

        eligible = {
            (r["exchange"], r["symbol"]): r
            for r in data["all_decisions"]
            if r["status"] == "ELIGIBLE"
        }

        if any(k not in resolved or k not in eligible for k in keys):
            raise ValueError(
                "Explicit sample must be eligible and globally resolved"
            )

        selected = [eligible[k] for k in keys]

    expected_currency = {
        "BIT": "EUR",
        "XETRA": "EUR",
        "NYSE": "USD",
        "NASDAQ": "USD",
    }

    if any(
        row.get("currency") != expected_currency[row["exchange"]]
        for row in selected
    ):
        raise ValueError("Unexpected pilot listing currency")

    if any(row["currency"] != "EUR" for row in selected) and fx_provider is None:
        raise ValueError("Non-EUR pilot requires explicit FX provider")

    begun = now()
    path = Path(output) / (
        "scanner_history_quality_"
        + begun.strftime("%Y%m%dT%H%M%S%fZ")
        + ".json"
    )

    report = {
        "audit_id": "E2E-S2.2D-HISTORY-PILOT",
        "run_status": "RUNNING",
        "started_at": begun.isoformat(),
        "planned_count": len(selected),
        "results": [],
        "lookback_days": lookback_days,
        "fx_mode": "ECB_EXACT_DATE" if fx_provider else "EUR_IDENTITY_ONLY",
        "listing_start_mode": (
            "REVIEWED_PRIMARY_REFERENCE"
            if listing_references
            else "NO_REFERENCE_SUPPLIED"
        ),
    }

    atomic_json(path, report)
    print(f"report: {path}", flush=True)
    resolver = YahooSymbolResolver()

    try:
        for index, row in enumerate(selected, 1):
            if index > 1:
                time.sleep(pause_seconds)

            print(
                f"HISTORY {index}/{len(selected)} | "
                f"{row['exchange']}:{row['symbol']} | started",
                flush=True,
            )

            tick = now()
            reference_end = tick.date() + timedelta(days=1)
            reference_start = tick.date() - timedelta(days=lookback_days + 2)

            context = calendar_provider.build(
                row["exchange"],
                start=reference_start,
                end=reference_end,
            )

            local_end = tick.astimezone(ZoneInfo(context.timezone)).date()
            local_start = local_end - timedelta(days=lookback_days)

            listing = MarketListing(
                row["symbol"],
                row["exchange"],
                row["exchange"],
                "US" if row["exchange"] in {"NYSE", "NASDAQ"} else "EUROPE",
                currency=row.get("currency"),
                instrument_type=row.get("raw_instrument_type"),
                isin=row.get("isin"),
                name=row.get("name"),
            )

            reference = (
                listing_references.resolve(listing, as_of=tick)
                if listing_references
                else None
            )

            if reference is not None:
                print(
                    f"LISTING_REFERENCE: {reference.status}",
                    flush=True,
                )

            mapping = resolver.resolve(listing)

            acquired = history_provider.fetch(
                listing,
                mapping,
                start=local_start,
                end=local_end,
                timezone=context.timezone,
            )

            fx = None

            if acquired.snapshot is not None and row["currency"] != "EUR":
                policy = HistoryQualityPolicy()
                cutoff = now()

                due = [
                    s.session
                    for s in context.calendar.sessions
                    if acquired.snapshot.start_session <= s.session
                    and s.closes_at + policy.publication_grace <= cutoff
                    and (
                        reference is None
                        or reference.evidence is None
                        or s.session >= reference.evidence.first_session
                    )
                ]

                if due:
                    fx = fx_provider.fetch(
                        row["currency"],
                        sessions=due[-policy.liquidity_window:],
                    )

                    print(
                        f"FX {fx.currency}: {fx.status} | "
                        f"rates={len(fx.rates)} | "
                        f"missing={len(fx.missing_sessions)}",
                        flush=True,
                    )

                    for diagnostic in fx.diagnostics:
                        print(f"  {diagnostic}", flush=True)

            as_of = now()

            record = {
                "source_decision": row,
                "as_of": as_of.isoformat(),
                "calendar": history_quality_result_to_dict(context),
                "acquisition": history_quality_result_to_dict(acquired),
                "fx": history_quality_result_to_dict(fx) if fx else None,
                "listing_reference": (
                    history_quality_result_to_dict(reference)
                    if reference
                    else None
                ),
                "quality": None,
            }

            reference_unusable = (
                reference is not None
                and reference.status in {"CONFLICT", "NOT_YET_KNOWN"}
            )

            if acquired.snapshot is not None and not reference_unusable:
                quality = evaluate_history_quality(
                    snapshot=acquired.snapshot,
                    verification=acquired.verification,
                    as_of=as_of,
                    calendar=context.calendar,
                    fx_rates=fx.rates if fx else (),
                    listing_start=reference.evidence if reference else None,
                )

                record["quality"] = history_quality_result_to_dict(quality)

                print(
                    f"{row['exchange']}:{row['symbol']} | "
                    f"route={quality.route.value}",
                    flush=True,
                )
                print("metrics:", dict(quality.metrics), flush=True)
                print(f"listing_event: {quality.listing_event}", flush=True)

                for capability in quality.indicators:
                    print(
                        f"  {capability.name}: "
                        f"available={capability.available} | "
                        f"sessions={capability.observed_sessions}/"
                        f"{capability.required_sessions}",
                        flush=True,
                    )

                for gate in quality.gates:
                    print(
                        f"  {gate.name}: {gate.status.value} | {gate.reason}",
                        flush=True,
                    )
            else:
                reason = (
                    "LISTING_REFERENCE_UNUSABLE"
                    if reference_unusable
                    else "SNAPSHOT_UNAVAILABLE"
                )
                print(f"quality: NOT_EVALUATED | {reason}", flush=True)

            diagnostics = (
                acquired.verification.diagnostics + acquired.diagnostics
            )

            for diagnostic in diagnostics:
                print(
                    f"  {diagnostic.code}: {diagnostic.message}",
                    flush=True,
                )

            report["results"].append(record)
            atomic_json(path, report)

            if any("RATE_LIMIT" in d.code for d in diagnostics):
                report["run_status"] = "RATE_LIMIT_STOP"
                break
        else:
            report["run_status"] = "COMPLETED"

    except KeyboardInterrupt:
        report["run_status"] = "INTERRUPTED"

    except Exception as exc:
        report["run_status"] = "FAILED"
        report["error_type"] = type(exc).__name__
        print(f"Pilot failure: {type(exc).__name__}", flush=True)

    finally:
        report["completed_count"] = len(report["results"])
        report["route_counts"] = dict(
            Counter(
                r["quality"]["route"] if r["quality"] else "NOT_EVALUATED"
                for r in report["results"]
            )
        )
        atomic_json(path, report)

    for key in (
        "run_status",
        "planned_count",
        "completed_count",
        "route_counts",
    ):
        print(f"{key}: {report[key]}", flush=True)

    if report["run_status"] == "INTERRUPTED":
        return 130

    if report["run_status"] == "FAILED":
        return 1

    passed = (
        report["run_status"] == "COMPLETED"
        and report["route_counts"].get("STANDARD", 0) == len(selected)
    )

    print(
        "LIVE_HISTORY_QUALITY:",
        "PASS" if passed else "PARTIAL",
        flush=True,
    )
    return 0 if passed else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--input-report", type=Path, required=True)
    parser.add_argument("--venues", nargs="+", default=["BIT", "XETRA"])
    parser.add_argument(
        "--listing",
        action="append",
        help="Explicit eligible EXCHANGE:SYMBOL; repeat at most 3 times",
    )
    parser.add_argument(
        "--listing-references",
        type=Path,
        help="Reviewed primary-source JSON manifest",
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.)
    parser.add_argument("--pause-seconds", type=float, default=2.)
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("data/cache/scanner/history_audit"),
    )

    args = parser.parse_args()

    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error("Timeout must be positive and finite")

    for package in ("yfinance", "pandas", "exchange-calendars"):
        print(f"{package}: {version(package)}", flush=True)

    data = json.loads(
        args.input_report.read_text(encoding="utf-8-sig")
    )

    registry = (
        ListingStartReferenceRegistry.load(args.listing_references)
        if args.listing_references
        else None
    )

    return run_pilot(
        data,
        venues=[v.upper() for v in args.venues],
        output=args.output_directory,
        history_provider=YahooHistorySnapshotProvider(
            timeout_seconds=args.timeout_seconds
        ),
        calendar_provider=ExchangeSessionCalendarProvider(),
        fx_provider=ECBSessionFXProvider(
            timeout_seconds=args.timeout_seconds
        ),
        pause_seconds=args.pause_seconds,
        lookback_days=args.lookback_days,
        listing_references=registry,
        listing_keys=args.listing,
    )


if __name__ == "__main__":
    raise SystemExit(main())
