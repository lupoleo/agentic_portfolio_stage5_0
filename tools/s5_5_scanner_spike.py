"""S5.0-5 spike: IB Gateway market scanners as the Stage 5 candidate universe (gap G2).

Run on the operator's PC with IB Gateway logged in (Read-Only API, port 4001):

    python -m tools.s5_5_scanner_spike [--port 4001]

Read-only: it asks the Gateway for the scanner parameter catalogue and runs a
small fixed plan of stock scans (each at most 50 rows) on the US and European
venues the catalogue offers. Nothing is created or saved on the account.
Full responses go to ``data/recordings/scanner_spike_<timestamp>.json`` and the
parameter XML next to it (both git-ignored); the console shows only counts and
timings.
"""

from __future__ import annotations

import argparse
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SCAN_CODES = ("MOST_ACTIVE", "TOP_PERC_GAIN", "TOP_PERC_LOSE", "HOT_BY_VOLUME", "HIGH_VS_52W_HL", "LOW_VS_52W_HL")
WANTED_LOCATIONS = ("STK.US.MAJOR", "STK.EU.IBIS", "STK.EU.SBF", "STK.EU.AEB", "STK.EU.BVME", "STK.EU.LSE", "STK.EU.SBVM")
MIN_MARKET_CAP_MUSD = 2000


def catalogue(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    locations = sorted({e.text for e in root.iter("locationCode") if e.text and e.text.startswith("STK.")})
    scans = sorted({e.text for e in root.iter("scanCode") if e.text})
    filters = sorted({e.text for e in root.iter("code") if e.text})
    return {"stock_locations": locations, "scan_codes": scans, "filter_codes": filters}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--client-id", type=int, default=52)
    args = parser.parse_args()

    from ib_async import IB, ScannerSubscription

    ib = IB()
    ib.connect(args.host, args.port, clientId=args.client_id, readonly=True, timeout=20)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    out_dir = Path("data/recordings")
    out_dir.mkdir(parents=True, exist_ok=True)
    record: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "scans": []}
    try:
        t = time.perf_counter()
        xml_text = ib.reqScannerParameters()
        (out_dir / f"scanner_parameters_{stamp}.xml").write_text(xml_text, encoding="utf-8")
        cat = catalogue(xml_text)
        record["catalogue_counts"] = {k: len(v) for k, v in cat.items()}
        record["stock_locations"] = cat["stock_locations"]
        print(f"parameters in {time.perf_counter() - t:.1f}s: {len(cat['stock_locations'])} stock locations, "
              f"{len(cat['scan_codes'])} scan codes, {len(cat['filter_codes'])} filters")
        eu = [loc for loc in cat["stock_locations"] if loc.startswith("STK.EU.")]
        print("european stock locations: " + ", ".join(eu[:40]))

        locations = [loc for loc in WANTED_LOCATIONS if loc in cat["stock_locations"]]
        missing = [loc for loc in WANTED_LOCATIONS if loc not in cat["stock_locations"]]
        if missing:
            print("not offered: " + ", ".join(missing))
        conids: set[int] = set()
        for location in locations:
            for code in SCAN_CODES:
                if code not in cat["scan_codes"]:
                    continue
                sub = ScannerSubscription(instrument="STK", locationCode=location, scanCode=code, numberOfRows=50,
                                          marketCapAbove=MIN_MARKET_CAP_MUSD)
                t = time.perf_counter()
                try:
                    rows = ib.reqScannerData(sub)
                    error = None
                except Exception as exc:  # noqa: BLE001 - spike: record every failure
                    rows, error = [], repr(exc)
                items = [
                    {"rank": r.rank, "conid": r.contractDetails.contract.conId, "symbol": r.contractDetails.contract.symbol,
                     "exchange": r.contractDetails.contract.primaryExchange or r.contractDetails.contract.exchange,
                     "currency": r.contractDetails.contract.currency}
                    for r in rows
                ]
                conids.update(i["conid"] for i in items)
                record["scans"].append({"location": location, "scan_code": code, "rows": items, "error": error,
                                        "seconds": round(time.perf_counter() - t, 1)})
                print(f"- {location} {code}: rows={len(items)} ({time.perf_counter() - t:.1f}s)"
                      f"{' ERR ' + error if error else ''}")
        record["distinct_conids"] = len(conids)
        print(f"distinct instruments across all scans: {len(conids)}")
    finally:
        ib.disconnect()
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    target = out_dir / f"scanner_spike_{stamp}.json"
    target.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print(f"saved {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
