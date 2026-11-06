"""S5.0-5 spike, step 2: which scanner venues give this account daily history.

Run on the operator's PC with IB Gateway logged in (Read-Only API, port 4001),
after ``tools.s5_5_scanner_spike``:

    python -m tools.s5_5_history_permission_probe [--spike data/recordings/scanner_spike_<ts>.json] [--per-venue 5]

For each venue of the scanner spike it asks 1 month of daily bars for a few
of the instruments found there and counts OK / no permission (IB 162/354) /
other errors. Read-only; the console shows only counts per venue, so it tells
which market-data subscription would actually be needed.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.broker.gateway import BrokerPermissionError, IbAsyncGatewaySession
from app.broker.ibkr_client import BrokerClientError


def latest_spike() -> Path:
    files = sorted(Path("data/recordings").glob("scanner_spike_*.json"))
    if not files:
        raise SystemExit("no scanner_spike_*.json in data/recordings: run tools.s5_5_scanner_spike first")
    return files[-1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spike", type=Path)
    parser.add_argument("--per-venue", type=int, default=5)
    parser.add_argument("--port", type=int, default=4001)
    args = parser.parse_args()

    spike = json.loads((args.spike or latest_spike()).read_text(encoding="utf-8"))
    by_venue: dict[str, dict[int, str]] = defaultdict(dict)
    for scan in spike["scans"]:
        for row in scan["rows"]:
            by_venue[scan["location"]].setdefault(row["conid"], row["symbol"])

    results = {}
    with IbAsyncGatewaySession(port=args.port, client_id=53) as session:
        for venue, rows in sorted(by_venue.items()):
            counts = {"ok": 0, "no_permission": 0, "other_error": 0}
            for conid in list(rows)[: args.per_venue]:
                try:
                    bars = session.daily_history(conid, "SMART", duration="1 M")
                    counts["ok" if bars else "other_error"] += 1
                except BrokerPermissionError:
                    counts["no_permission"] += 1
                except BrokerClientError:
                    counts["other_error"] += 1
            results[venue] = counts
            print(f"- {venue}: history ok={counts['ok']} no_permission={counts['no_permission']} "
                  f"other_error={counts['other_error']} (of {min(len(rows), args.per_venue)} sampled, "
                  f"{len(rows)} found by the scanner)")
    target = Path("data/recordings/history_permission_probe.json")
    target.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"saved {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
