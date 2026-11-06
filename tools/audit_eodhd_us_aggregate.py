from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from app.scanner.providers.eodhd_exchange_symbols import (
    public_exchange_symbols_url,
)


DEFAULT_OUTPUT_DIRECTORY = Path(
    "data/cache/scanner/exchange_audit"
)


def normalized_value(
    row: dict[str, Any],
    field: str,
) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        return "<MISSING>"
    normalized = value.strip()
    return normalized.upper() if normalized else "<MISSING>"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the raw EODHD US aggregate by venue and "
            "instrument type."
        )
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
    )
    return parser.parse_args()


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

    public_url = public_exchange_symbols_url("US")
    request_url = public_url + "?" + urlencode(
        {
            "api_token": api_token.strip(),
            "fmt": "json",
        }
    )

    print("US raw download started")
    started = time.perf_counter()

    with urlopen(
        request_url,
        timeout=args.timeout_seconds,
    ) as response:
        raw = response.read()

    download_elapsed = time.perf_counter() - started
    print(
        "US raw download completed:",
        len(raw),
        "bytes in",
        round(download_elapsed, 3),
        "seconds",
    )

    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(
            "EODHD US payload must be a JSON array"
        )

    venue_rows = Counter()
    venue_symbols: dict[str, set[str]] = defaultdict(set)
    venue_missing_isin = Counter()
    venue_types: dict[str, Counter] = defaultdict(Counter)
    venue_currencies: dict[str, Counter] = defaultdict(
        Counter
    )
    venue_countries: dict[str, Counter] = defaultdict(
        Counter
    )

    global_types = Counter()
    global_currencies = Counter()
    global_countries = Counter()
    invalid_row_count = 0
    missing_symbol_count = 0
    missing_exchange_count = 0
    missing_type_count = 0
    missing_isin_count = 0

    for row in payload:
        if not isinstance(row, dict):
            invalid_row_count += 1
            continue

        venue = normalized_value(row, "Exchange")
        symbol = normalized_value(row, "Code")
        instrument_type = normalized_value(row, "Type")
        currency = normalized_value(row, "Currency")
        country = normalized_value(row, "Country")

        isin_value = row.get("Isin")
        has_isin = (
            isinstance(isin_value, str)
            and bool(isin_value.strip())
        )

        venue_rows[venue] += 1
        venue_types[venue][instrument_type] += 1
        venue_currencies[venue][currency] += 1
        venue_countries[venue][country] += 1

        global_types[instrument_type] += 1
        global_currencies[currency] += 1
        global_countries[country] += 1

        if symbol == "<MISSING>":
            missing_symbol_count += 1
        else:
            venue_symbols[venue].add(symbol)

        if venue == "<MISSING>":
            missing_exchange_count += 1
        if instrument_type == "<MISSING>":
            missing_type_count += 1
        if not has_isin:
            missing_isin_count += 1
            venue_missing_isin[venue] += 1

    venue_summary = {}

    for venue, row_count in sorted(
        venue_rows.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        unique_symbol_count = len(venue_symbols[venue])

        venue_summary[venue] = {
            "row_count": row_count,
            "unique_symbol_count": unique_symbol_count,
            "duplicate_symbol_row_count": (
                row_count - unique_symbol_count
            ),
            "missing_isin_count": (
                venue_missing_isin[venue]
            ),
            "instrument_types": dict(
                venue_types[venue].most_common()
            ),
            "currencies": dict(
                venue_currencies[venue].most_common()
            ),
            "countries": dict(
                venue_countries[venue].most_common()
            ),
        }

    report = {
        "report_version": "1",
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source_url": public_url,
        "response_bytes": len(raw),
        "download_elapsed_seconds": round(
            download_elapsed,
            3,
        ),
        "raw_row_count": len(payload),
        "invalid_row_count": invalid_row_count,
        "missing_symbol_count": missing_symbol_count,
        "missing_exchange_count": missing_exchange_count,
        "missing_type_count": missing_type_count,
        "missing_isin_count": missing_isin_count,
        "global_instrument_types": dict(
            global_types.most_common()
        ),
        "global_currencies": dict(
            global_currencies.most_common()
        ),
        "global_countries": dict(
            global_countries.most_common()
        ),
        "venues": venue_summary,
    }

    args.output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )
    output_path = args.output_directory / (
        "eodhd_us_aggregate_"
        + datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
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

    print("\nGLOBAL:")
    print("raw_rows:", len(payload))
    print("invalid_rows:", invalid_row_count)
    print("missing_symbol:", missing_symbol_count)
    print("missing_exchange:", missing_exchange_count)
    print("missing_type:", missing_type_count)
    print("missing_isin:", missing_isin_count)
    print(
        "instrument_types:",
        json.dumps(
            dict(global_types.most_common()),
            ensure_ascii=False,
        ),
    )

    print("\nVENUE × TYPE:")
    for venue, summary in venue_summary.items():
        print(
            " | ".join(
                (
                    venue,
                    f"rows={summary['row_count']}",
                    (
                        "unique_symbols="
                        f"{summary['unique_symbol_count']}"
                    ),
                    (
                        "missing_isin="
                        f"{summary['missing_isin_count']}"
                    ),
                    (
                        "types="
                        + json.dumps(
                            summary["instrument_types"],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    ),
                )
            )
        )

    print("\nreport:", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
