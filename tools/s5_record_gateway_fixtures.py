"""Record IB Gateway data for the simulated holdings (read-only), for offline development.

Run on the operator's PC with IB Gateway logged in (Read-Only API, port 4001):

    python -m tools.s5_record_gateway_fixtures [--fixture data/simulation/fineco_usd_2026-11-06.json]

For every holding of the simulation fixture it records contract details,
2 years of daily bars and up to 30 recent headlines into
``data/recordings/gateway_fixtures_<timestamp>.json`` (git-ignored). The
console shows only counts.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.broker.gateway import IbAsyncGatewaySession
from app.broker.ibkr_client import BrokerClientError
from app.broker.sources import SimulationFixture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default="data/simulation/fineco_usd_2026-11-06.json")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--duration", default="2 Y")
    args = parser.parse_args()

    fixture = SimulationFixture.load(args.fixture)
    out: dict = {"recorded_at": datetime.now().astimezone().isoformat(), "fixture_id": fixture.fixture_id, "instruments": {}}
    with IbAsyncGatewaySession(port=args.port) as session:
        codes = session.news_providers()
        out["news_providers"] = codes
        for h in fixture.holdings:
            entry: dict = {"symbol": h.symbol, "isin": h.isin}
            t = time.perf_counter()
            try:
                entry["contract"] = asdict(session.contract_info(h.conid, "SMART"))
                bars = session.daily_history(h.conid, "SMART", duration=args.duration)
                entry["bars"] = [{**asdict(b), "day": b.day.isoformat()} for b in bars]
            except BrokerClientError as exc:
                entry["history_error"] = str(exc)
            try:
                news = session.headlines(h.conid, codes, limit=30)
                entry["headlines"] = [{**asdict(n), "published_at": n.published_at.isoformat()} for n in news]
            except BrokerClientError as exc:
                entry["news_error"] = str(exc)
            out["instruments"][str(h.conid)] = entry
            print(f"- {h.symbol}: bars={len(entry.get('bars', []))} headlines={len(entry.get('headlines', []))} "
                  f"({time.perf_counter() - t:.1f}s){' ERR' if 'history_error' in entry or 'news_error' in entry else ''}")
    target = Path("data/recordings") / f"gateway_fixtures_{datetime.now():%Y%m%d_%H%M%S}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"saved {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
