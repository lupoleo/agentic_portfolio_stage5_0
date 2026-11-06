"""First Stage 5 E2E (part A) on synthetic recordings: IBKR account state -> portfolio analysis -> 4.0 E2E service."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from tests.test_broker_account_sync import balances, reading, summary
from tools import s5_first_e2e

CONIDS = {"AAA": 1001, "BBB": 1002, "CCC": 1003, "SPY": 756733}


def bars(n, start, step):
    day, out = date(2023, 1, 2), []
    for i in range(n):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        close = start + i * step + (i % 9) * 0.7 - (i % 4) * 0.5
        out.append({"day": day.isoformat(), "open": close, "high": close + 1, "low": close - 1, "close": close,
                    "volume": 1000 + i % 17})
        day += timedelta(days=1)
    return out


def write_inputs(tmp_path):
    instruments = {}
    for i, (symbol, conid) in enumerate(CONIDS.items()):
        instruments[str(conid)] = {
            "contract": {"conid": conid, "symbol": symbol, "exchange": "NASDAQ", "primary_exchange": "NASDAQ",
                         "currency": "USD", "long_name": symbol, "sec_type": "STK"},
            "bars": bars(800, 50 + 10 * i, 0.05 * (i + 1)),
            "headlines": [],
        }
    gateway = tmp_path / "gateway.json"
    gateway.write_text(json.dumps({"news_providers": ["BRFG"], "instruments": instruments}), encoding="utf-8")
    account = tmp_path / "account.json"
    account.write_text(json.dumps({"readings": [{
        "observed_at": "2026-11-06T10:30:00+00:00",
        **reading(positions=[], summ=summary(nlv=6_000, cash=6_000, gross=0), bal=balances(eur=6_000, usd=0, usd_rate=0.86)),
    }]}), encoding="utf-8")
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({
        "fixture_id": "synthetic", "purpose": "test", "valid_until": "2027-03-31", "source_file": "x.xlsx",
        "source_file_sha256": "0" * 64, "source_exported_on": "2026-11-06",
        "holdings": [
            {"conid": CONIDS[s], "symbol": s, "isin": f"US000000000{n}", "description": f"{s} INC",
             "listing_exchange": "NASDAQ", "currency": "USD", "quantity": 10 * (n + 1), "average_price": 40,
             "market_price_at_export": 60}
            for n, s in enumerate(("AAA", "BBB", "CCC"))
        ],
    }), encoding="utf-8")
    return gateway, account, fixture


def test_first_e2e_runs_from_ibkr_account_to_research_without_side_effects(tmp_path, capsys):
    gateway, account, fixture = write_inputs(tmp_path)
    code = s5_first_e2e.main([
        "--gateway-recording", str(gateway), "--account-recording", str(account), "--fixture", str(fixture),
        "--db", str(tmp_path / "cio.db"), "--sync-db", str(tmp_path / "sync.db"),
        "--output-directory", str(tmp_path / "out"), "--cache-only",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "analyzed=3/3" in out and "origin=SIMULATED_TEMPORARY" in out
    assert "completed_stages: ['PORTFOLIO_ANALYSIS', 'SCANNER_DISCOVERY', 'WATCH_UNIVERSE_ASSEMBLY']" in out
    assert "CACHE_ONLY_MISS=3" in out  # research ran on the three holdings, model calls disabled
    for counter in ("broker_orders_submitted", "portfolio_mutations", "automatic_executions"):
        assert f"{counter}: 0" in out
    (report_path,) = (tmp_path / "out").glob("stage5_first_e2e_*.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["request"]["portfolio_file"].startswith("ibkr://SIMULATED_FINECO_USD/")
    assert report["configuration"]["evidence_source"] == "ibkr"


def test_positions_are_valued_in_eur_with_ibkr_fx(tmp_path):
    from app.broker.account_sync import AccountSyncService
    from app.broker.ibkr_client import ReadOnlyIbkrClient
    from app.broker.sources import LiveIbkrAccountSource
    from app.broker.sync_store import SyncStore
    from app.e2e.stage5_portfolio import Stage5PortfolioError, directory_from_version, positions_from_version
    from tests.test_broker_account_sync import T0, position
    from app.broker.recordings import RecordedTransport

    transport = RecordedTransport({k: [v] for k, v in reading(positions=[position(qty=10, price=200.0, avg=180.0)]).items()})
    version = AccountSyncService(LiveIbkrAccountSource(ReadOnlyIbkrClient(transport), clock=lambda: T0),
                                 SyncStore(tmp_path / "s.db")).sync().version
    (p,) = positions_from_version(version)
    assert p.market_value_eur == pytest.approx(2000 * 0.9) and p.cost_value_eur == pytest.approx(1800 * 0.9)
    assert p.market_data_symbol == "AAA" and p.instrument_reference_id == "IBKR:1001" and p.direction == "LONG"
    directory = directory_from_version(version)
    assert directory.get("AAA").conid == 1001 and directory.get("SPY").conid == 756733

    usd_base = version.model_copy(update={"reading": version.reading.model_copy(update={
        "metrics": version.reading.metrics.model_copy(update={"base_currency": "USD"})})})
    with pytest.raises(Stage5PortfolioError):
        positions_from_version(usd_base)
