"""S5.0-1 spike: read-only Python connection to IB Gateway / TWS (architecture G5).

Run on the operator's PC with IB Gateway logged in (Live, port 4001,
"Read-Only API" ticked):

    python -m tools.s5_1_ibkr_gateway_spike [--port 4001]

The connection is opened with ``readonly=True`` and the script only issues
requests (account, positions, contract details, daily history, news). It never
places, modifies or cancels anything. Full responses go to
``data/recordings/ibkr_gateway_spike_<timestamp>.json`` (git-ignored, personal
data); the console shows only counts, statuses and timings, so it can be pasted
into chat without amounts.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# conid, exchange, label — one per instrument family of the portfolio
PROBES = [
    (272093, "SMART", "MSFT (US stock)"),
    (88819736, "SMART", "NBIS (US stock, no IBKR insights)"),
    (211008268, "BVME", "PST Poste Italiane (IT stock)"),
    (199482900, "BVME", "LUVE LU-VE (IT small cap)"),
    (175394979, "BVME.ETF", "SGLD Invesco Gold ETC (Milan)"),
    (100292038, "AEB", "IWDA iShares MSCI World (Amsterdam)"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--client-id", type=int, default=51)
    args = parser.parse_args()

    from ib_async import IB, Contract

    ib = IB()
    record: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "probes": {}}
    t0 = time.perf_counter()
    ib.connect(args.host, args.port, clientId=args.client_id, readonly=True, timeout=20)
    print(f"connected in {time.perf_counter() - t0:.1f}s; server version {ib.client.serverVersion()}")

    accounts = ib.managedAccounts()
    print(f"accounts: {len(accounts)}")
    summary = ib.accountSummary()
    record["account_summary_tags"] = sorted({v.tag for v in summary})
    print(f"account summary: {len(summary)} values, {len(record['account_summary_tags'])} tags")
    positions = ib.positions()
    record["positions"] = [{"conid": p.contract.conId, "symbol": p.contract.symbol, "qty": p.position} for p in positions]
    print(f"positions: {len(positions)}")

    providers = ib.reqNewsProviders()
    record["news_providers"] = [{"code": p.code, "name": p.name} for p in providers]
    print("news providers: " + (", ".join(f"{p.code} ({p.name})" for p in providers) or "NONE"))
    codes = "+".join(p.code for p in providers)

    for conid, exchange, label in PROBES:
        out: dict = {"label": label}
        c = Contract(conId=conid, exchange=exchange)
        try:
            details = ib.reqContractDetails(c)
            out["details"] = len(details)
            if details:
                d = details[0]
                out["long_name"] = d.longName
                out["primary_exchange"] = d.contract.primaryExchange
                out["currency"] = d.contract.currency
                c = d.contract
        except Exception as exc:  # noqa: BLE001 - spike: record every failure
            out["details_error"] = repr(exc)
        t = time.perf_counter()
        try:
            bars = ib.reqHistoricalData(c, endDateTime="", durationStr="1 Y", barSizeSetting="1 day",
                                        whatToShow="TRADES",
                                        useRTH=True, formatDate=2, timeout=60)
            out["bars"] = len(bars)
            out["last_bar"] = str(bars[-1].date) if bars else None
        except Exception as exc:  # noqa: BLE001
            out["bars_error"] = repr(exc)
        out["history_seconds"] = round(time.perf_counter() - t, 1)
        if codes:
            try:
                news = ib.reqHistoricalNews(conid, codes, "", "", 20)
                if news is None:  # ib_async returns None after its 4 s timeout
                    out["news_timeout"] = True
                news = news or []
                out["headlines"] = len(news)
                out["newest_headline_at"] = str(news[0].time) if news else None
                out["headline_sample"] = [n.headline for n in news[:3]]
            except Exception as exc:  # noqa: BLE001
                out["news_error"] = repr(exc)
        record["probes"][str(conid)] = out
        print(f"- {label}: details={out.get('details', 'ERR')} bars={out.get('bars', out.get('bars_error', '?'))} "
              f"({out['history_seconds']}s) headlines={out.get('headlines', out.get('news_error', 'n/a'))} "
              f"newest={out.get('newest_headline_at')}")

    ib.disconnect()
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    target = Path("data/recordings") / f"ibkr_gateway_spike_{datetime.now():%Y%m%d_%H%M%S}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    print(f"saved {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
