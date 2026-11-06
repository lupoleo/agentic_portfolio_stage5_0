from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from app.portfolio.fineco_importer import load_fineco_positions
from app.portfolio.ticker_resolver import resolve_yahoo_symbol


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit S2.2H portfolio instrument coverage")
    parser.add_argument("--portfolio", default="data/input/portafoglio-export.xlsx")
    parser.add_argument("--output", default="data/cache/portfolio/instrument_coverage_s22h.json")
    args = parser.parse_args()

    positions = load_fineco_positions(args.portfolio)
    rows = []
    for item in positions:
        symbol = resolve_yahoo_symbol(item)
        rows.append({
            "name": item.name,
            "isin": item.isin,
            "broker_symbol": item.broker_symbol,
            "direction": item.direction,
            "accounting_exposure_eur": abs(item.market_value_eur),
            "market_data_symbol": symbol,
            "history_method": item.market_data_method,
            "leverage_multiplier": item.leverage_multiplier,
            "instrument_reference_id": item.instrument_reference_id,
            "identity_represented": bool(symbol),
        })

    methods = Counter(x["history_method"] for x in rows if x["identity_represented"])
    summary = {
        "checkpoint": "E2E-S2.2H",
        "network_calls": 0,
        "position_count": len(rows),
        "identity_represented_count": sum(x["identity_represented"] for x in rows),
        "reviewed_reference_count": sum(bool(x["instrument_reference_id"]) for x in rows),
        "direct_count": methods["DIRECT"],
        "leveraged_proxy_count": methods["LEVERAGED_PROXY"],
        "accounting_gross_exposure_eur": sum(x["accounting_exposure_eur"] for x in rows),
        "rows": rows,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"position_count: {summary['position_count']}")
    print(f"identity_represented_count: {summary['identity_represented_count']}")
    print(f"reviewed_reference_count: {summary['reviewed_reference_count']}")
    print(f"direct_count: {summary['direct_count']}")
    print(f"leveraged_proxy_count: {summary['leveraged_proxy_count']}")
    print("network_calls: 0")
    print(f"report: {output}")
    complete = summary["identity_represented_count"] == summary["position_count"]
    print("AUDIT_STATUS:", "SUCCESS" if complete else "PARTIAL")
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
