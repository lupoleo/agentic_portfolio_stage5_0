"""Check the Stage 5 research evidence channels on the IBKR account holdings (§5D).

Run on the operator's PC with IB Gateway logged in (Read-Only API, port 4001):

    python -m tools.s5_evidence_channels_check [--recording data/recordings/gateway_fixtures_<ts>.json] [--no-yahoo]

For every live IBKR position it fetches the market, news,
fundamental and analyst channels and the canonical technical input exactly as
the runtime builds them with ``evidence_source="ibkr"``: IBKR first (live
Gateway, or a recording), Yahoo as declared secondary. Read-only; no model
call. The console shows only sources, statuses and counts.
"""

from __future__ import annotations

import argparse
import time

from app.ai.evidence_provider import EvidenceRequest
from app.ai.ibkr_evidence_wiring import build_ibkr_evidence_channels
from app.broker.gateway import IbAsyncGatewaySession, RecordedGatewaySession
from app.broker.instrument_directory import InstrumentDirectory
from app.broker.account_sync import AccountSyncService
from app.broker.sources import gateway_account_source
from app.broker.sync_store import SyncStore


def _channel(provider, ticker: str) -> str:
    if provider is None:
        return "off"
    try:
        result = provider.fetch(EvidenceRequest(ticker=ticker, max_items=20))
    except Exception as exc:  # noqa: BLE001 - report every failure
        return f"ERR {type(exc).__name__}"
    secondary = " (secondary)" if result.metadata.get("secondary_source") else ""
    return f"{result.provider} {result.status.value} {len(result.items)}{secondary}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync-db", default="data/state/stage5_account_sync.db")
    parser.add_argument("--recording", help="replay a Gateway recording instead of the live Gateway")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--no-yahoo", action="store_true", help="IBKR only, no secondary")
    args = parser.parse_args()

    secondary: dict = {}
    if not args.no_yahoo:
        from app.ai.analyst_evidence_provider import YahooAnalystEvidenceProvider
        from app.ai.canonical_technical import build_canonical_technical_input
        from app.ai.fundamental_evidence_provider import YahooFundamentalEvidenceProvider
        from app.ai.market_evidence_provider import YahooMarketEvidenceProvider
        from app.ai.news_evidence_provider import YahooNewsEvidenceProvider

        secondary = dict(
            secondary_market=YahooMarketEvidenceProvider(), secondary_news=YahooNewsEvidenceProvider(),
            secondary_fundamental=YahooFundamentalEvidenceProvider(), secondary_analyst=YahooAnalystEvidenceProvider(),
            secondary_technical_loader=build_canonical_technical_input,
        )
    session = RecordedGatewaySession(args.recording) if args.recording else IbAsyncGatewaySession(port=args.port)
    try:
        version = AccountSyncService(gateway_account_source(session), SyncStore(args.sync_db)).sync().version
        directory = InstrumentDirectory.from_reading(version.reading)
        channels = build_ibkr_evidence_channels(session, directory, **secondary)
        for h in version.reading.positions:
            t = time.perf_counter()
            try:
                technical = channels.canonical_technical_loader(h.symbol).trend
            except Exception as exc:  # noqa: BLE001
                technical = f"ERR {type(exc).__name__}"
            print(f"- {h.symbol}: market={_channel(channels.market, h.symbol)} | news={_channel(channels.news, h.symbol)} | "
                  f"fund={_channel(channels.fundamental, h.symbol)} | analyst={_channel(channels.analyst, h.symbol)} | "
                  f"technical={technical} ({time.perf_counter() - t:.1f}s)")
        if channels.technical_secondary_tickers:
            print("technical from secondary: " + ", ".join(channels.technical_secondary_tickers))
    finally:
        if hasattr(session, "close"):
            session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
