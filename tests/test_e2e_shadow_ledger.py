"""E2E-S4.0B shadow ledger: export, measurement and report."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest

from app.e2e.shadow_ledger import (
    ShadowLedgerStore,
    benchmark_for,
    build_report,
    collect_scored_hypotheses,
    export_to_ledger,
    measure_ledger,
    report_markdown,
    spearman,
)


def _source_db(path, rows):
    with sqlite3.connect(path) as connection:
        for table in ("scanner_research_outcomes", "opportunity_scores", "opportunity_research"):
            connection.execute(f"create table {table} (payload_json text)")
        for outcome, score, research in rows:
            connection.execute("insert into scanner_research_outcomes values (?)", (json.dumps(outcome),))
            if score:
                connection.execute("insert into opportunity_scores values (?)", (json.dumps(score),))
            if research:
                connection.execute("insert into opportunity_research values (?)", (json.dumps(research),))


def _row(index, kind="NEW_LONG", ticker="UCG.MI", adjusted=55.0, policy="ai-8c3-directional-scoring-v3",
         status="COMPLETE", created="2026-10-01T08:10:00Z"):
    score_id = f"score-{index}"
    outcome = {"hypothesis_id": f"hyp-{index}", "integration_run_id": "run-1", "kind": kind,
               "status": "EXCLUDED", "reason": "SCORE_BELOW_THRESHOLD",
               "opportunity_score_id": score_id, "opportunity_id": None}
    score = {"opportunity_score_id": score_id, "ticker": ticker, "created_at": created,
             "research_id": f"RES-{index}", "raw_score": adjusted + 3, "confidence_adjusted_score": adjusted,
             "score_confidence": 0.5, "scoring_status": "SCORED", "research_confidence": 0.7,
             "evidence_quality": "HIGH", "thesis_score": 65.0, "catalyst_score": 55.0,
             "fundamental_score": 50.0, "technical_score": 80.0, "expectations_score": 50.0,
             "metadata": {"hypothesis_kind": kind, "scoring_diagnostics": {"direction": {
                 "policy_version": policy, "direction_source": "HYPOTHESIS"}}}}
    research = {"research_id": f"RES-{index}", "research_status": status,
                "metadata": {"research_contract": "ai-8c2-research-v3-context-gaps"}}
    return outcome, score, research


def test_benchmarks():
    assert benchmark_for("UCG.MI") == "FTSEMIB.MI"
    assert benchmark_for("BMW.DE") == "^GDAXI"
    assert benchmark_for("WTRG") == "^GSPC"
    assert benchmark_for("XYZ.TO") is None


def test_collect_keeps_only_scored_directional_hypotheses(tmp_path):
    source = tmp_path / "state.db"
    monitor = ({"hypothesis_id": "m", "kind": "PORTFOLIO_MONITOR", "opportunity_score_id": None}, None, None)
    unscored = ({"hypothesis_id": "u", "kind": "NEW_LONG", "opportunity_score_id": None}, None, None)
    _source_db(source, [_row(1), _row(2, kind="NEW_SHORT"), monitor, unscored])
    entries = collect_scored_hypotheses(source, "test")
    assert [(e["entry_id"], e["direction"]) for e in entries] == [("score-1", "LONG"), ("score-2", "SHORT")]
    entry = entries[0]
    assert entry["research_status"] == "COMPLETE"
    assert entry["directional_policy"] == "ai-8c3-directional-scoring-v3"
    assert entry["benchmark"] == "FTSEMIB.MI"
    assert entry["components"]["technical"] == 80.0


def test_export_is_idempotent_and_source_untouched(tmp_path):
    source = tmp_path / "state.db"
    _source_db(source, [_row(1), _row(2)])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    ledger = ShadowLedgerStore(tmp_path / "ledger.db")
    assert export_to_ledger(source, ledger, "run-a") == {"found": 2, "added": 2, "already_present": 0}
    assert export_to_ledger(source, ledger, "run-a") == {"found": 2, "added": 0, "already_present": 2}
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def _business_days(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _loader(prices):
    def load(symbol, start, end):
        return [(day, value) for day, value in prices[symbol] if start <= day <= end]
    return load


def _prices(days, start_value, step):
    return [(day, start_value + step * index) for index, day in enumerate(days)]


def test_measurement_uses_last_close_before_evaluation_and_direction(tmp_path):
    source = tmp_path / "state.db"
    _source_db(source, [_row(1), _row(2, kind="NEW_SHORT")])
    ledger = ShadowLedgerStore(tmp_path / "ledger.db")
    export_to_ledger(source, ledger, "run-a")
    days = _business_days(date(2026, 9, 21), 40)
    prices = {"UCG.MI": _prices(days, 100.0, 1.0), "FTSEMIB.MI": _prices(days, 1000.0, 5.0)}
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    counts = measure_ledger(ledger, now=now, price_loader=_loader(prices), horizons=(5,))
    assert counts["measured"] == 2
    measurements = ledger.measurements()
    long = measurements[("score-1", 5)]
    short = measurements[("score-2", 5)]
    assert long["reference_date"] == "2026-09-30"           # evaluated 2026-10-01 08:10Z
    assert long["horizon_date"] == "2026-10-07"
    assert long["directional_return"] == pytest.approx(5 / 107)
    assert short["directional_return"] == pytest.approx(-5 / 107)
    assert long["benchmark_return"] == pytest.approx(25 / 1035)
    assert long["directional_excess_return"] == pytest.approx(5 / 107 - 25 / 1035)
    assert measure_ledger(ledger, now=now, price_loader=_loader(prices), horizons=(5,))["already_measured"] == 2


def test_measurement_waits_for_enough_sessions(tmp_path):
    source = tmp_path / "state.db"
    _source_db(source, [_row(1)])
    ledger = ShadowLedgerStore(tmp_path / "ledger.db")
    export_to_ledger(source, ledger, "run-a")
    days = _business_days(date(2026, 9, 21), 12)
    prices = {"UCG.MI": _prices(days, 100.0, 1.0), "FTSEMIB.MI": _prices(days, 1000.0, 5.0)}
    counts = measure_ledger(ledger, now=datetime(2026, 10, 7, tzinfo=timezone.utc),
                            price_loader=_loader(prices), horizons=(20,))
    assert counts == {"measured": 0, "pending": 1, "unavailable": 0, "already_measured": 0}


def test_unavailable_prices_are_counted_not_raised(tmp_path):
    source = tmp_path / "state.db"
    _source_db(source, [_row(1)])
    ledger = ShadowLedgerStore(tmp_path / "ledger.db")
    export_to_ledger(source, ledger, "run-a")

    def failing(symbol, start, end):
        raise RuntimeError("provider down")

    counts = measure_ledger(ledger, now=datetime(2026, 11, 20, tzinfo=timezone.utc),
                            price_loader=failing, horizons=(5,))
    assert counts["unavailable"] == 1


def test_spearman():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 2], [1, 2]) is None


def test_report_buckets_filters_and_markdown(tmp_path):
    source = tmp_path / "state.db"
    _source_db(source, [
        _row(1, adjusted=48.0), _row(2, adjusted=53.0), _row(3, adjusted=58.0, kind="NEW_SHORT"),
        _row(4, adjusted=62.0, status="PARTIAL"), _row(5, adjusted=57.0, policy="ai-8c3-directional-scoring-v1"),
    ])
    ledger = ShadowLedgerStore(tmp_path / "ledger.db")
    export_to_ledger(source, ledger, "run-a")
    days = _business_days(date(2026, 9, 21), 40)
    prices = {"UCG.MI": _prices(days, 100.0, 1.0), "FTSEMIB.MI": _prices(days, 1000.0, 5.0)}
    measure_ledger(ledger, now=datetime(2026, 11, 20, tzinfo=timezone.utc), price_loader=_loader(prices))

    report = build_report(ledger, policies={"ai-8c3-directional-scoring-v3"})
    assert report["entries"] == 4
    buckets = report["horizons"]["5"]["buckets"]
    assert buckets["<50"]["directional_return"]["n"] == 1
    assert buckets["55-60"]["directional_return"]["hit_rate"] == 0.0      # SHORT in a rising series
    assert buckets[">=60"]["directional_return"]["n"] == 1
    assert build_report(ledger, policies=None)["entries"] == 5
    assert build_report(ledger, policies={"ai-8c3-directional-scoring-v3"}, complete_only=True)["entries"] == 3
    text = report_markdown(report)
    assert "| 50-55 | 1 |" in text and "Spearman" in text


def test_cli_roundtrip(tmp_path, capsys):
    from tools import shadow_ledger as cli

    source = tmp_path / "state.db"
    _source_db(source, [_row(1)])
    ledger = tmp_path / "ledger.db"
    assert cli.main(["--ledger", str(ledger), "export", "--source-db", str(source), "--label", "x"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["added"] == 1
    assert cli.main(["--ledger", str(ledger), "report", "--out", str(tmp_path / "r" / "report")]) == 0
    assert (tmp_path / "r" / "report.md").is_file()


def test_list_filters_and_returns(tmp_path):
    from app.e2e.shadow_ledger import list_entries, list_text

    source = tmp_path / "state.db"
    _source_db(source, [
        _row(1, adjusted=53.0), _row(2, kind="NEW_SHORT", adjusted=55.0),
        _row(3, ticker="BMW.DE", status="PARTIAL", policy="ai-8c3-directional-scoring-v1"),
    ])
    ledger = ShadowLedgerStore(tmp_path / "ledger.db")
    export_to_ledger(source, ledger, "run-a")
    days = _business_days(date(2026, 9, 21), 14)             # 5 sessions after 2026-09-30, not 10
    prices = {"UCG.MI": _prices(days, 100.0, 1.0), "FTSEMIB.MI": _prices(days, 1000.0, 5.0),
              "BMW.DE": _prices(days, 80.0, 0.0), "^GDAXI": _prices(days, 20000.0, 0.0)}
    measure_ledger(ledger, now=datetime(2026, 10, 9, tzinfo=timezone.utc), price_loader=_loader(prices))

    rows = list_entries(ledger)
    assert len(rows) == 3
    long = next(row for row in rows if row["direction"] == "LONG" and row["ticker"] == "UCG.MI")
    assert long["return_5"] == pytest.approx(5 / 107)
    assert long["return_10"] is None and long["return_20"] is None   # not yet reached
    assert [r["ticker"] for r in list_entries(ledger, ticker="ucg.mi")] == ["UCG.MI", "UCG.MI"]
    assert len(list_entries(ledger, direction="short")) == 1
    assert len(list_entries(ledger, complete_only=True)) == 2
    assert len(list_entries(ledger, policies={"ai-8c3-directional-scoring-v3"})) == 2
    text = list_text(rows)
    assert "+4.7%" in text and "3 entries" in text


def test_csv_for_italian_excel(tmp_path):
    from app.e2e.shadow_ledger import LIST_COLUMNS, list_entries, write_csv

    source = tmp_path / "state.db"
    _source_db(source, [_row(1, adjusted=53.25)])
    ledger = ShadowLedgerStore(tmp_path / "ledger.db")
    export_to_ledger(source, ledger, "run-a")
    path = tmp_path / "out" / "ledger.csv"
    write_csv(list_entries(ledger), path)
    content = path.read_text(encoding="utf-8-sig").splitlines()
    assert content[0] == ";".join(LIST_COLUMNS)
    assert ";53,25;" in content[1]
    write_csv(list_entries(ledger), path, decimal_comma=False)
    assert ",53.25," in path.read_text(encoding="utf-8-sig").splitlines()[1]


def test_cli_list(tmp_path, capsys):
    from tools import shadow_ledger as cli

    source = tmp_path / "state.db"
    _source_db(source, [_row(1)])
    ledger = tmp_path / "ledger.db"
    cli.main(["--ledger", str(ledger), "export", "--source-db", str(source), "--label", "x"])
    capsys.readouterr()
    assert cli.main(["--ledger", str(ledger), "list", "--direction", "LONG",
                     "--csv", str(tmp_path / "list.csv")]) == 0
    out = capsys.readouterr().out
    assert "UCG.MI" in out and "1 entries" in out
    assert (tmp_path / "list.csv").is_file()
