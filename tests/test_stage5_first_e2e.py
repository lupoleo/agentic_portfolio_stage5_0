"""First Stage 5 E2E (part A) on synthetic recordings: IBKR account state -> portfolio analysis -> 4.0 E2E service."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from tests.test_broker_account_sync import balances, position, reading, summary
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
    """A Gateway recording holding the real-account snapshot (3 USD stocks, EUR base) and their bars."""
    instruments = {}
    for i, (symbol, conid) in enumerate(CONIDS.items()):
        instruments[str(conid)] = {
            "contract": {"conid": conid, "symbol": symbol, "exchange": "NASDAQ", "primary_exchange": "NASDAQ",
                         "currency": "USD", "long_name": symbol, "sec_type": "STK"},
            "bars": bars(800, 50 + 10 * i, 0.05 * (i + 1)),
            "headlines": [],
        }
    held = [position(conid=CONIDS[s], symbol=s, qty=10 * (n + 1), price=60.0, avg=40.0)
            for n, s in enumerate(("AAA", "BBB", "CCC"))]
    for p, s in zip(held, ("AAA", "BBB", "CCC")):
        p["isin"] = f"US000000000{s}"
    value_eur = sum(p["market_value"] for p in held) * 0.86
    snap = reading(positions=held, summ=summary(nlv=6_000 + value_eur, cash=6_000, gross=value_eur),
                   bal=balances(eur=6_000, usd=0, usd_rate=0.86))
    snap.pop("get_flow_type")
    gateway = tmp_path / "gateway.json"
    gateway.write_text(json.dumps({"news_providers": ["BRFG"], "instruments": instruments, "account": snap}),
                       encoding="utf-8")
    return gateway


def test_first_e2e_runs_from_ibkr_account_to_research_without_side_effects(tmp_path, capsys):
    gateway = write_inputs(tmp_path)
    code = s5_first_e2e.main([
        "--gateway-recording", str(gateway),
        "--db", str(tmp_path / "cio.db"), "--sync-db", str(tmp_path / "sync.db"),
        "--output-directory", str(tmp_path / "out"), "--cache-only",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "analyzed=3/3" in out and "origin=REAL_IBKR" in out
    assert "completed_stages: ['PORTFOLIO_ANALYSIS', 'SCANNER_DISCOVERY', 'WATCH_UNIVERSE_ASSEMBLY']" in out
    assert "CACHE_ONLY_MISS=3" in out  # research ran on the three holdings, model calls disabled
    for counter in ("broker_orders_submitted", "portfolio_mutations", "automatic_executions"):
        assert f"{counter}: 0" in out
    (report_path,) = (tmp_path / "out").glob("stage5_first_e2e_*.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["request"]["portfolio_file"].startswith("ibkr://LIVE/")
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


def test_scanner_candidates_join_the_watch_universe(tmp_path, capsys):
    from datetime import datetime, timezone

    from app.broker.gateway import scan_key
    from app.scanner.exchange_session_calendar import ExchangeSessionCalendarProvider

    gateway = write_inputs(tmp_path)
    data = json.loads(gateway.read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).date()
    calendar = ExchangeSessionCalendarProvider().build("NASDAQ", start=today - timedelta(days=400), end=today).calendar
    now = datetime.now(timezone.utc)
    sessions = [s.session for s in calendar.sessions if s.closes_at <= now]
    data["instruments"]["2001"] = {
        "contract": {"conid": 2001, "symbol": "NEWC", "exchange": "SMART", "primary_exchange": "NASDAQ",
                     "currency": "USD", "long_name": "New Candidate Inc", "sec_type": "STK", "stock_type": "COMMON"},
        "bars": [{"day": d.isoformat(), "open": 40 + i * 0.05, "high": 41 + i * 0.05, "low": 39 + i * 0.05,
                  "close": 40 + i * 0.05, "volume": 500_000} for i, d in enumerate(sessions)],  # up-trend
        "headlines": [],
    }
    data["scans"] = {
        scan_key("STK", "STK.US.MAJOR", "MOST_ACTIVE"): [
            {"rank": 0, "conid": 2001, "symbol": "NEWC", "exchange": "NASDAQ", "currency": "USD"},
            {"rank": 1, "conid": 1001, "symbol": "AAA", "exchange": "NASDAQ", "currency": "USD"},  # held
        ],
    }
    day, fx = today - timedelta(days=400), []
    while day <= today:
        if day.weekday() < 5:
            fx.append({"day": day.isoformat(), "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1})
        day += timedelta(days=1)
    data["fx"] = {"USD": fx}
    gateway.write_text(json.dumps(data), encoding="utf-8")

    code = s5_first_e2e.main([
        "--gateway-recording", str(gateway),
        "--db", str(tmp_path / "cio.db"), "--sync-db", str(tmp_path / "sync.db"),
        "--output-directory", str(tmp_path / "out"), "--cache-only",
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "listings=2" in out and "NEWC: STANDARD" in out
    assert "WATCH_UNIVERSE_ASSEMBLY" in out and "CACHE_ONLY_MISS" in out  # research reached, model calls off
    (watch_path,) = (tmp_path / "out" / "watch_universe").glob("*.json")
    members = json.loads(watch_path.read_text(encoding="utf-8"))["universe"]["members"]
    assert {m["yahoo_symbols"][0]: m["provenances"] for m in members} == {
        "AAA": ["CURRENT_POSITION"], "BBB": ["CURRENT_POSITION"], "CCC": ["CURRENT_POSITION"],
        "NEWC": ["NEW_CANDIDATE"]}
    (report_path,) = (tmp_path / "out").glob("stage5_first_e2e_*.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["scanner"]["excluded_held"] == 1 and report["scanner"]["evaluated_listings"] == 1
    assert report["configuration"]["max_hypotheses"] == 10
    for counter in ("broker_orders_submitted", "portfolio_mutations", "automatic_executions"):
        assert f"{counter}: 0" in out


def test_saved_scanner_reports_are_reused_without_a_new_scan(tmp_path, capsys):
    test_scanner_candidates_join_the_watch_universe(tmp_path, capsys)  # first run: scans and saves reports
    saved = tmp_path / "out" / "scanner"
    gateway = tmp_path / "gateway.json"
    data = json.loads(gateway.read_text(encoding="utf-8"))
    data.pop("scans")  # a second scan would find nothing: the candidate must come from the saved reports
    gateway.write_text(json.dumps(data), encoding="utf-8")
    code = s5_first_e2e.main([
        "--gateway-recording", str(gateway), "--db", str(tmp_path / "cio2.db"),
        "--sync-db", str(tmp_path / "sync2.db"), "--output-directory", str(tmp_path / "out2"),
        "--cache-only", "--reuse-scanner", str(saved),
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert f"scanner: REUSED {saved}" in out and "candidates=['NEWC']" in out
    (watch_path,) = (tmp_path / "out2" / "watch_universe").glob("*.json")
    members = json.loads(watch_path.read_text(encoding="utf-8"))["universe"]["members"]
    assert {m["yahoo_symbols"][0] for m in members} == {"AAA", "BBB", "CCC", "NEWC"}
