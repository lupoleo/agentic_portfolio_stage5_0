"""Stage 5.0 account sync from the live IBKR account.

    python -m tools.stage5_account_sync                       # live, IB Gateway (Read-Only API)
    python -m tools.stage5_account_sync --recording data/recordings/ibkr_account_2026-11.json

Live: one sync of the real account (summary, cash per currency, positions,
open orders) read through IB Gateway. ``--recording`` replays recorded real
read-only connector responses, one sync per recorded reading.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.broker.account_sync import AccountSyncService
from app.broker.gateway import IbAsyncGatewaySession
from app.broker.ibkr_client import ReadOnlyIbkrClient
from app.broker.recordings import load_account_recording
from app.broker.sources import LiveIbkrAccountSource, gateway_account_source
from app.broker.sync_store import SyncStore


def _print(r) -> None:
    reading = r.version.reading
    m = reading.metrics
    print(f"{r.observed_at:%Y-%m-%d %H:%M} {r.outcome.value:<14} {r.version.version_id}  "
          f"positions={len(reading.positions)} NLV={m.net_liquidation:,.2f} {m.base_currency}")
    print(f"  cash {m.total_cash_value or 0:,.2f}  positions value {m.gross_position_value or 0:,.2f}  "
          f"available {m.available_funds or 0:,.2f}  init margin {m.initial_margin or 0:,.2f} {m.base_currency}")
    for b in reading.balances:
        if b.currency != "BASE":
            print(f"  cash {b.currency}: {b.cash_balance:,.2f}")
    for p in reading.positions:
        side = "SHORT" if p.quantity < 0 else "LONG "
        print(f"  {side} {p.symbol:<8} qty {p.quantity:>8g}  avg {p.average_price or 0:>10,.2f}  "
              f"last {p.market_price or 0:>10,.2f}  value {p.market_value or 0:>11,.2f} {p.currency}  "
              f"P&L {p.unrealized_pnl or 0:>9,.2f}")
    for o in reading.open_orders:
        price = o.get("stop_price") or o.get("limit_price") or ""
        print(f"  order {o.get('side')} {o.get('quantity')} {o.get('symbol')} {o.get('order_type')} {price} "
              f"{o.get('time_in_force')} [{o.get('status')}]")
    for c in r.changes:
        if c.change_type.value == "INITIAL":
            print("  first sync: this version is the baseline for change detection")
            continue
        print(f"    {c.change_type.value:<22} {c.subject:<18} {c.before} -> {c.after}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recording", type=Path, help="replay recorded connector readings instead of IB Gateway")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--db", type=Path, default=Path("data/state/stage5_account_sync.db"))
    args = parser.parse_args(argv)
    args.db.parent.mkdir(parents=True, exist_ok=True)

    if args.recording:
        transport, times = load_account_recording(args.recording)
        clock_values = iter(times)
        client = ReadOnlyIbkrClient(transport)
        service = AccountSyncService(LiveIbkrAccountSource(client, clock=lambda: next(clock_values)), SyncStore(args.db))
        for _ in times:
            _print(service.sync())
        print(f"broker calls: {dict(client.calls)}  (all read-only); orders submitted: 0")
        return 0

    with IbAsyncGatewaySession(port=args.port) as session:
        _print(AccountSyncService(gateway_account_source(session), SyncStore(args.db)).sync())
    print("IB Gateway Read-Only API; orders submitted: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
