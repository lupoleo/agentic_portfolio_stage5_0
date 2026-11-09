from datetime import date, datetime, timedelta, timezone

import pytest

from app.broker.gateway import (
    BrokerPermissionError,
    ContractInfo,
    DailyBar,
    RecordedGatewaySession,
    ScannerRow,
    scan_key,
)
from app.broker.ibkr_client import BrokerClientError, BrokerNoDataError
from app.scanner.exchange_session_calendar import ExchangeSessionCalendarProvider
from app.scanner.ibkr_universe import (
    DEFAULT_PLAN,
    ScanPlan,
    ScanQuery,
    acquire_universe,
    build_scanner_reports,
    fx_rates_from_bars,
    research_symbol,
)
from tools.audit_scanner_watch_universe import build_watch_universe_audit

AS_OF = datetime(2026, 11, 5, 22, 30, tzinfo=timezone.utc)  # Thursday, after the US close
START = AS_OF.date() - timedelta(days=370)


def _sessions(exchange):
    calendar = ExchangeSessionCalendarProvider().build(exchange, start=START, end=AS_OF.date()).calendar
    return [s.session for s in calendar.sessions]


def _bars(exchange, *, price=100.0, volume=1_000_000.0):
    return [DailyBar(day, price, price * 1.01, price * 0.99, price, volume) for day in _sessions(exchange)]


INFOS = {
    1: ContractInfo(1, "AAA", "SMART", "NASDAQ", "USD", "AAA Corp", "STK", "COMMON"),
    2: ContractInfo(2, "ETFX", "SMART", "ARCA", "USD", "Some ETF", "STK", "ETF"),
    3: ContractInfo(3, "SAP", "SMART", "IBIS", "EUR", "SAP SE", "STK", "COMMON"),
    4: ContractInfo(4, "NOPE", "SMART", "NYSE", "USD", "Nope Inc", "STK", "COMMON"),
    5: ContractInfo(5, "HELD", "SMART", "NASDAQ", "USD", "Held Inc", "STK", "COMMON"),
    6: ContractInfo(6, "THIN", "SMART", "NYSE", "USD", "Thin Inc", "STK", "COMMON"),
}


class FakeSession:
    def __init__(self):
        self.history_calls = []
        self.scans = {
            scan_key("STK", "STK.US.MAJOR", "MOST_ACTIVE"): [
                ScannerRow(0, 1, "AAA", "NASDAQ", "USD"), ScannerRow(1, 2, "ETFX", "ARCA", "USD"),
                ScannerRow(2, 4, "NOPE", "NYSE", "USD"), ScannerRow(3, 5, "HELD", "NASDAQ", "USD"),
                ScannerRow(4, 6, "THIN", "NYSE", "USD"),
            ],
            scan_key("STK", "STK.US.MAJOR", "TOP_PERC_GAIN"): [ScannerRow(0, 1, "AAA", "NASDAQ", "USD")],
            scan_key("STOCK.EU", "STK.EU.IBIS", "MOST_ACTIVE"): [ScannerRow(0, 3, "SAP", "IBIS", "EUR")],
        }

    def scanner(self, instrument, location, scan_code, *, rows=50, market_cap_above_musd=None):
        key = scan_key(instrument, location, scan_code)
        if key not in self.scans:
            raise BrokerClientError(f"scanner {location} {scan_code}: not configured")
        return self.scans[key][:rows]

    def contract_info(self, conid, exchange=""):
        return INFOS[conid]

    def daily_history(self, conid, exchange, *, duration="1 Y"):
        self.history_calls.append(conid)
        if conid == 4:
            raise BrokerPermissionError("history 4: IB error 162: no market data permissions")
        if conid == 6:
            return _bars("NYSE", price=5.0, volume=1000.0)  # turnover far below EUR 1M
        exchange = INFOS[conid].primary_exchange
        return _bars(exchange)

    def fx_daily_history(self, currency, *, duration="1 Y"):
        assert currency == "USD"
        day, bars = START, []
        while day <= AS_OF.date():
            if day.weekday() < 5:
                bars.append(DailyBar(day, 1.1, 1.1, 1.1, 1.1, 0.0))
            day += timedelta(days=1)
        return bars


PLAN = ScanPlan("test", "1", (
    ScanQuery("STK", "STK.US.MAJOR", "MOST_ACTIVE"),
    ScanQuery("STK", "STK.US.MAJOR", "TOP_PERC_GAIN"),
    ScanQuery("STOCK.EU", "STK.EU.IBIS", "MOST_ACTIVE"),
    ScanQuery("STOCK.EU", "STK.EU.SBF", "MOST_ACTIVE"),
))


def test_acquisition_dedups_by_conid_and_records_failed_scans():
    acquisition = acquire_universe(FakeSession(), PLAN, now=AS_OF)
    assert [item.conid for item in acquisition.listings] == [1, 3, 2, 4, 5, 6]
    aaa = acquisition.listings[0]
    assert aaa.sources == ("STK.US.MAJOR:MOST_ACTIVE", "STK.US.MAJOR:TOP_PERC_GAIN")
    failed = [s for s in acquisition.scans if s.error]
    assert len(failed) == 1 and failed[0].query.location == "STK.EU.SBF"


def test_reports_feed_the_unchanged_watch_universe_assembly():
    session = FakeSession()
    acquisition = acquire_universe(session, PLAN, now=AS_OF)
    reports = build_scanner_reports(session, acquisition, as_of=AS_OF, exclude_conids=frozenset({5}))

    assert 5 not in session.history_calls and 2 not in session.history_calls  # held / ineligible: no history
    statuses = {row["symbol"]: row["status"] for row in reports.eligibility["all_decisions"]}
    assert statuses == {"AAA": "ELIGIBLE", "ETFX": "INELIGIBLE", "SAP": "ELIGIBLE", "NOPE": "ELIGIBLE",
                        "THIN": "ELIGIBLE"}
    assert reports.mapping["provider_id"] == "ibkr"
    routes = {r["research_symbol"]: (r["quality"] or {}).get("route") for r in reports.history["results"]}
    assert routes == {"AAA": "STANDARD", "SAP.DE": "STANDARD", "NOPE": None, "THIN": "BLOCKED"}
    assert {ref.ticker: ref.conid for ref in reports.instrument_refs} == {"AAA": 1, "SAP.DE": 3, "NOPE": 4, "THIN": 6}
    assert reports.summary["fx_currencies"] == ["USD"]

    audit = build_watch_universe_audit(
        reports.eligibility, reports.mapping, (reports.history,), (),
        portfolio_snapshot_id="snap-1", as_of=AS_OF, assembled_at=AS_OF,
    )
    assert audit["summary"]["candidate_count"] == 2
    reasons = audit["summary"]["candidate_reason_counts"]
    assert reasons["INCLUDED"] == 2
    assert reasons["ELIGIBILITY_NOT_ELIGIBLE"] == 1
    symbols = {m["yahoo_symbols"][0] for m in audit["universe"]["members"]}
    assert symbols == {"AAA", "SAP.DE"}


def test_max_listings_bounds_gateway_calls():
    session = FakeSession()
    acquisition = acquire_universe(session, PLAN, now=AS_OF)
    reports = build_scanner_reports(session, acquisition, as_of=AS_OF, max_listings=2)
    assert reports.summary["evaluated_listings"] == 2
    assert sorted(session.history_calls) == [1, 3]


def test_research_symbol_is_unique_per_venue():
    assert research_symbol("AAPL", "NASDAQ") == "AAPL"
    assert research_symbol("SAP", "IBIS") == "SAP.DE"
    assert research_symbol("BT.A", "LSE") == "BT-A.L"
    assert research_symbol("ENEL", "BVME") == "ENEL.MI"


def test_fx_rates_are_eur_per_unit_on_exact_sessions():
    bars = [DailyBar(date(2026, 11, 2), 1.1, 1.1, 1.1, 1.25, 0.0), DailyBar(date(2026, 11, 3), 1, 1, 1, 0.0, 0.0)]
    rates = fx_rates_from_bars("usd", bars, known_at=AS_OF)
    assert len(rates) == 1 and rates[0].currency == "USD" and rates[0].eur_per_unit == pytest.approx(0.8)


def test_default_plan_uses_stock_eu_for_european_locations():
    for query in DEFAULT_PLAN.queries:
        assert query.instrument == ("STOCK.EU" if query.location.startswith("STK.EU.") else "STK")
    assert len(DEFAULT_PLAN.fingerprint()) == 64


def test_recorded_session_replays_scans_and_fx():
    session = RecordedGatewaySession({
        "scans": {scan_key("STK", "STK.US.MAJOR", "MOST_ACTIVE"): [
            {"rank": 0, "conid": 1, "symbol": "AAA", "exchange": "NASDAQ", "currency": "USD"}]},
        "fx": {"USD": [{"day": "2026-11-02", "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1}]},
    })
    assert session.scanner("STK", "STK.US.MAJOR", "MOST_ACTIVE")[0].conid == 1
    assert session.fx_daily_history("usd")[0].close == 1.1
    with pytest.raises(BrokerNoDataError):
        session.scanner("STK", "STK.US.MAJOR", "TOP_PERC_GAIN")


def test_max_candidates_stops_after_enough_standard_routes():
    session = FakeSession()
    acquisition = acquire_universe(session, PLAN, now=AS_OF)
    reports = build_scanner_reports(session, acquisition, as_of=AS_OF, max_candidates=1)
    assert session.history_calls == [1]  # AAA is STANDARD: SAP and the rest are not evaluated
    assert reports.summary["standard_candidates"] == 1 and reports.summary["stopped_at_max_candidates"]
    assert [row["symbol"] for row in reports.eligibility["all_decisions"]] == ["AAA"]


def test_venue_without_calendar_is_skipped_before_history():
    session = FakeSession()
    INFOS_FWB = dict(INFOS)
    INFOS_FWB[3] = ContractInfo(3, "SAP", "SMART", "FWB2", "EUR", "SAP SE", "STK", "COMMON")
    session.contract_info = lambda conid, exchange="": INFOS_FWB[conid]
    acquisition = acquire_universe(session, PLAN, now=AS_OF)
    reports = build_scanner_reports(session, acquisition, as_of=AS_OF)
    assert 3 not in session.history_calls
    assert reports.summary["counts"]["unsupported_venue:FWB2"] == 1
    assert "SAP" not in {row["symbol"] for row in reports.eligibility["all_decisions"]}


def test_uptrend_filter_drops_listings_below_their_50_session_average():
    session = FakeSession()
    falling = _bars("NASDAQ")
    falling = [DailyBar(b.day, 200 - i * 0.2, 201 - i * 0.2, 199 - i * 0.2, 200 - i * 0.2, b.volume)
               for i, b in enumerate(falling)]
    rising = [DailyBar(b.day, 50 + i * 0.2, 51 + i * 0.2, 49 + i * 0.2, 50 + i * 0.2, b.volume)
              for i, b in enumerate(_bars("IBIS"))]
    original = session.daily_history
    session.daily_history = lambda conid, exchange, *, duration="1 Y": (
        falling if conid == 1 else rising if conid == 3 else original(conid, exchange, duration=duration))
    acquisition = acquire_universe(session, PLAN, now=AS_OF)
    reports = build_scanner_reports(session, acquisition, as_of=AS_OF, require_uptrend=True)
    symbols = {row["symbol"] for row in reports.eligibility["all_decisions"]}
    assert "AAA" not in symbols and "SAP" in symbols  # AAA falls, SAP rises
    assert reports.summary["counts"]["trend:below_sma50"] >= 1
    assert "AAA" not in {ref.ticker for ref in reports.instrument_refs}


def test_listings_found_by_more_scans_rank_first():
    session = FakeSession()
    session.scans[scan_key("STK", "STK.US.MAJOR", "TOP_PERC_GAIN")] = [
        ScannerRow(0, 6, "THIN", "NYSE", "USD"), ScannerRow(1, 1, "AAA", "NASDAQ", "USD")]
    session.scans[scan_key("STOCK.EU", "STK.EU.IBIS", "MOST_ACTIVE")] = [
        ScannerRow(0, 3, "SAP", "IBIS", "EUR"), ScannerRow(1, 6, "THIN", "NYSE", "USD")]
    order = [item.conid for item in acquire_universe(session, PLAN, now=AS_OF).listings]
    assert order[:2] == [6, 1]  # THIN in 3 scans, AAA in 2, then single-scan listings by rank


def test_default_plan_v2_leads_with_opportunity_scans():
    codes = [q.scan_code for q in DEFAULT_PLAN.queries if q.location == "STK.US.MAJOR"]
    assert codes[:2] == ["HIGH_VS_52W_HL", "HIGH_VS_13W_HL"] and DEFAULT_PLAN.version == "2"
    assert len(DEFAULT_PLAN.queries) == 30


def test_balanced_regions_alternate_us_and_european_candidates():
    from app.scanner.ibkr_universe import UniverseListing, interleave_regions

    def eu(conid, n):
        return UniverseListing(conid, f"E{conid}", "BVME", "EUR", 0, tuple(f"STK.EU.BVME:S{i}" for i in range(n)))

    def us(conid, n):
        return UniverseListing(conid, f"U{conid}", "NASDAQ", "USD", 0, tuple(f"STK.US.MAJOR:S{i}" for i in range(n)))

    ranked = [eu(1, 4), eu(2, 4), eu(3, 3), us(10, 2), eu(4, 2), us(11, 1)]
    assert [x.conid for x in interleave_regions(ranked)] == [10, 1, 11, 2, 3, 4]
    assert [x.conid for x in interleave_regions([eu(1, 1), eu(2, 1)])] == [1, 2]
