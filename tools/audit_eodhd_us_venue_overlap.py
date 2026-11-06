from __future__ import annotations

import argparse
import json
import os
import socket
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PUBLIC_URL = "https://eodhd.com/api/exchange-symbol-list/US"
REGULATED_CANDIDATES = (
    "NASDAQ",
    "NYSE",
    "NYSE ARCA",
    "BATS",
    "AMEX",
    "NYSE MKT",
    "US",
)
OTC_VENUES = (
    "PINK",
    "OTCQB",
    "OTCGREY",
    "OTCQX",
    "OTCCE",
    "OTCMKTS",
    "OTC",
    "OTCBB",
    "OTCPK",
)
FUND_VENUES = ("NMFQS",)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _upper(value: Any) -> str | None:
    normalized = _text(value)
    return normalized.upper() if normalized else None


def _percent(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100.0, 3)


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def fetch_rows(api_token: str, timeout_seconds: float) -> tuple[list[Any], int]:
    url = PUBLIC_URL + "?" + urlencode({"api_token": api_token, "fmt": "json"})
    request = Request(
        url,
        headers={"User-Agent": "AgenticPortfolio/1.0 (US venue audit)"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"EODHD returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"EODHD request failed: {type(exc).__name__}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("EODHD returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise RuntimeError("EODHD top-level payload is not a list")
    return payload, len(raw)


def normalize_rows(payload: Iterable[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            diagnostics.append(f"row {index}: not an object")
            continue
        symbol = _upper(raw.get("Code"))
        venue = _upper(raw.get("Exchange"))
        instrument_type = _upper(raw.get("Type"))
        if not symbol or not venue or not instrument_type:
            diagnostics.append(
                f"row {index}: missing Code, Exchange or Type"
            )
            continue
        rows.append(
            {
                "symbol": symbol,
                "venue": venue,
                "instrument_type": instrument_type,
                "isin": _upper(raw.get("Isin")),
                "name": _text(raw.get("Name")),
                "currency": _upper(raw.get("Currency")),
                "country": _upper(raw.get("Country")),
            }
        )
    return rows, diagnostics


def venue_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["venue"]].append(row)

    summaries = []
    for venue, venue_rows in grouped.items():
        isins = [row["isin"] for row in venue_rows if row["isin"]]
        symbols = [row["symbol"] for row in venue_rows]
        summaries.append(
            {
                "venue": venue,
                "rows": len(venue_rows),
                "unique_symbols": len(set(symbols)),
                "duplicate_symbols": len(symbols) - len(set(symbols)),
                "present_isin_rows": len(isins),
                "missing_isin_rows": len(venue_rows) - len(isins),
                "unique_isins": len(set(isins)),
                "duplicate_isin_rows": len(isins) - len(set(isins)),
                "instrument_types": _sorted_counter(
                    Counter(row["instrument_type"] for row in venue_rows)
                ),
                "currencies": _sorted_counter(
                    Counter(row["currency"] or "<MISSING>" for row in venue_rows)
                ),
            }
        )
    return sorted(summaries, key=lambda item: (-item["rows"], item["venue"]))


def _sets_by_venue(rows: list[dict[str, Any]], field: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        value = row[field]
        if value:
            result[row["venue"]].add(value)
    return result


def pairwise_overlap(
    rows: list[dict[str, Any]],
    venues: tuple[str, ...],
) -> list[dict[str, Any]]:
    isin_sets = _sets_by_venue(rows, "isin")
    symbol_sets = _sets_by_venue(rows, "symbol")
    output = []
    for left_index, left in enumerate(venues):
        for right in venues[left_index + 1 :]:
            left_isins = isin_sets[left]
            right_isins = isin_sets[right]
            shared_isins = left_isins & right_isins
            union_isins = left_isins | right_isins
            shared_symbols = symbol_sets[left] & symbol_sets[right]
            output.append(
                {
                    "left": left,
                    "right": right,
                    "shared_isins": len(shared_isins),
                    "left_isin_percent": _percent(len(shared_isins), len(left_isins)),
                    "right_isin_percent": _percent(len(shared_isins), len(right_isins)),
                    "isin_jaccard_percent": _percent(
                        len(shared_isins), len(union_isins)
                    ),
                    "shared_symbols": len(shared_symbols),
                }
            )
    return output


def incremental_coverage(
    rows: list[dict[str, Any]],
    venues: tuple[str, ...],
    instrument_type: str | None = None,
) -> list[dict[str, Any]]:
    selected = [
        row
        for row in rows
        if instrument_type is None or row["instrument_type"] == instrument_type
    ]
    isin_sets = _sets_by_venue(selected, "isin")
    covered: set[str] = set()
    output = []
    for venue in venues:
        venue_isins = isin_sets[venue]
        new_isins = venue_isins - covered
        already_covered = venue_isins & covered
        covered.update(venue_isins)
        output.append(
            {
                "venue": venue,
                "unique_isins": len(venue_isins),
                "new_isins": len(new_isins),
                "already_covered_isins": len(already_covered),
                "incremental_percent": _percent(len(new_isins), len(venue_isins)),
                "cumulative_unique_isins": len(covered),
            }
        )
    return output


def group_summary(
    rows: list[dict[str, Any]],
    name: str,
    venues: tuple[str, ...],
) -> dict[str, Any]:
    selected = [row for row in rows if row["venue"] in venues]
    isins = {row["isin"] for row in selected if row["isin"]}
    return {
        "name": name,
        "venues": list(venues),
        "rows": len(selected),
        "unique_symbols_by_venue": len(
            {(row["venue"], row["symbol"]) for row in selected}
        ),
        "unique_isins": len(isins),
        "missing_isin_rows": sum(row["isin"] is None for row in selected),
        "instrument_types": _sorted_counter(
            Counter(row["instrument_type"] for row in selected)
        ),
    }


def generic_us_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    generic = [row for row in rows if row["venue"] == "US"]
    other_regulated = [
        row
        for row in rows
        if row["venue"] in REGULATED_CANDIDATES and row["venue"] != "US"
    ]
    other_isins = {row["isin"] for row in other_regulated if row["isin"]}
    other_symbols = {row["symbol"] for row in other_regulated}
    samples = []
    for row in generic[:50]:
        samples.append(
            {
                **row,
                "isin_seen_on_other_regulated_venue": bool(
                    row["isin"] and row["isin"] in other_isins
                ),
                "symbol_seen_on_other_regulated_venue": row["symbol"] in other_symbols,
            }
        )
    generic_isins = {row["isin"] for row in generic if row["isin"]}
    generic_symbols = {row["symbol"] for row in generic}
    return {
        "rows": len(generic),
        "unique_isins": len(generic_isins),
        "missing_isin_rows": sum(row["isin"] is None for row in generic),
        "isins_seen_on_other_regulated_venues": len(generic_isins & other_isins),
        "symbols_seen_on_other_regulated_venues": len(
            generic_symbols & other_symbols
        ),
        "instrument_types": _sorted_counter(
            Counter(row["instrument_type"] for row in generic)
        ),
        "first_50_samples": samples,
    }


def print_incremental(title: str, values: list[dict[str, Any]]) -> None:
    print(f"\n{title}:")
    for item in values:
        print(
            f"{item['venue']} | isins={item['unique_isins']} | "
            f"new={item['new_isins']} | "
            f"covered={item['already_covered_isins']} | "
            f"incremental%={item['incremental_percent']} | "
            f"cumulative={item['cumulative_unique_isins']}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit venue coverage and overlap inside the EODHD US aggregate."
        )
    )
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("data/cache/scanner/exchange_audit"),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be > 0")
    api_token = os.getenv("EODHD_API_TOKEN", "").strip()
    if not api_token:
        raise SystemExit("EODHD_API_TOKEN is missing")

    started = time.perf_counter()
    print("US aggregate download started")
    payload, response_bytes = fetch_rows(api_token, args.timeout_seconds)
    rows, diagnostics = normalize_rows(payload)
    elapsed_download = time.perf_counter() - started
    print(
        f"US aggregate downloaded: {response_bytes} bytes in "
        f"{elapsed_download:.3f} seconds"
    )

    summaries = venue_summary(rows)
    pairwise = pairwise_overlap(rows, REGULATED_CANDIDATES)
    incremental_all = incremental_coverage(rows, REGULATED_CANDIDATES)
    incremental_common = incremental_coverage(
        rows, REGULATED_CANDIDATES, "COMMON STOCK"
    )
    incremental_etf = incremental_coverage(rows, REGULATED_CANDIDATES, "ETF")
    groups = [
        group_summary(rows, "regulated_candidates", REGULATED_CANDIDATES),
        group_summary(rows, "otc", OTC_VENUES),
        group_summary(rows, "fund_venues", FUND_VENUES),
    ]
    generic_us = generic_us_analysis(rows)

    print("\nVENUES:")
    for item in summaries:
        print(
            f"{item['venue']} | rows={item['rows']} | "
            f"isins={item['unique_isins']} | "
            f"missing_isin={item['missing_isin_rows']} | "
            f"types={json.dumps(item['instrument_types'], separators=(',', ':'))}"
        )

    print_incremental("REGULATED INCREMENTAL — ALL", incremental_all)
    print_incremental("REGULATED INCREMENTAL — COMMON STOCK", incremental_common)
    print_incremental("REGULATED INCREMENTAL — ETF", incremental_etf)

    print("\nREGULATED PAIRWISE OVERLAP:")
    for item in pairwise:
        print(
            f"{item['left']} vs {item['right']} | "
            f"shared_isins={item['shared_isins']} | "
            f"left%={item['left_isin_percent']} | "
            f"right%={item['right_isin_percent']} | "
            f"jaccard%={item['isin_jaccard_percent']} | "
            f"shared_symbols={item['shared_symbols']}"
        )

    print("\nGENERIC US BUCKET:")
    for key in (
        "rows",
        "unique_isins",
        "missing_isin_rows",
        "isins_seen_on_other_regulated_venues",
        "symbols_seen_on_other_regulated_venues",
    ):
        print(f"{key}: {generic_us[key]}")
    print(
        "instrument_types: "
        + json.dumps(generic_us["instrument_types"], separators=(",", ":"))
    )

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "public_source_url": PUBLIC_URL,
        "raw_row_count": len(payload),
        "valid_row_count": len(rows),
        "invalid_row_count": len(diagnostics),
        "response_bytes": response_bytes,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "diagnostics": diagnostics,
        "regulated_candidates": list(REGULATED_CANDIDATES),
        "otc_venues": list(OTC_VENUES),
        "fund_venues": list(FUND_VENUES),
        "venue_summaries": summaries,
        "group_summaries": groups,
        "regulated_pairwise_overlap": pairwise,
        "regulated_incremental_all": incremental_all,
        "regulated_incremental_common_stock": incremental_common,
        "regulated_incremental_etf": incremental_etf,
        "generic_us_bucket": generic_us,
    }
    args.output_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output_directory / f"eodhd_us_venue_overlap_{stamp}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nreport: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
