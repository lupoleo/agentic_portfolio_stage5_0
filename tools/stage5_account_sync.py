"""Stage 5.0 account sync.

    python -m tools.stage5_account_sync --mode simulated --recording data/recordings/ibkr_account_2026-11.json
    python -m tools.stage5_account_sync --mode live --recording data/recordings/ibkr_account_2026-11.json

``--recording`` replays real read-only IBKR responses, one sync per recorded
reading. A direct live connection is added after the S5.0-1 client spike (G5).
``--mode simulated`` is the TEMPORARY Fineco-USD simulation.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.broker.account_sync import AccountSyncService
from app.broker.ibkr_client import ReadOnlyIbkrClient
from app.broker.recordings import load_account_recording
from app.broker.sources import LiveIbkrAccountSource, SimulatedFinecoUsdSource, SimulationFixture
from app.broker.sync_store import SyncStore

DEFAULT_FIXTURE = Path("data/simulation/fineco_usd_2026-11-06.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["live", "simulated"], default="live")
    parser.add_argument("--recording", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--db", type=Path, default=Path("data/state/stage5_account_sync.db"))
    args = parser.parse_args(argv)

    transport, times = load_account_recording(args.recording)
    clock_values = iter(times)
    client = ReadOnlyIbkrClient(transport)
    if args.mode == "simulated":
        source = SimulatedFinecoUsdSource(client, SimulationFixture.load(args.fixture), clock=lambda: next(clock_values))
    else:
        source = LiveIbkrAccountSource(client, clock=lambda: next(clock_values))
    args.db.parent.mkdir(parents=True, exist_ok=True)
    service = AccountSyncService(source, SyncStore(args.db))

    for _ in times:
        r = service.sync()
        m = r.version.reading.metrics
        print(f"{r.observed_at:%Y-%m-%d %H:%M} {r.outcome.value:<14} {r.version.version_id}  "
              f"positions={len(r.version.reading.positions)} NLV={m.net_liquidation:,.2f} {m.base_currency}")
        for c in r.changes:
            print(f"    {c.change_type.value:<22} {c.subject:<18} {c.before} -> {c.after}")
    print(f"broker calls: {dict(client.calls)}  (all read-only); orders submitted: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
