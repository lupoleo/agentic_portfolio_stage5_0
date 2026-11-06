"""E2E-S4.0B shadow ledger: measure what scored hypotheses would have earned.

Every directional hypothesis that reaches an opportunity score (above or below
the threshold, COMPLETE or PARTIAL research, LONG or SHORT) is recorded in a
separate SQLite file. A later measurement step fetches daily closes and records
the return after 5, 10 and 20 sessions in the direction of the hypothesis, in
absolute terms and against the listing's market index. A report groups the
results by confidence-adjusted score.

The ledger has no effect on any portfolio, proposal, opportunity or execution
contract. Source databases are opened read-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Callable, Iterable

LEDGER_VERSION = "e2e-s4.0b-shadow-ledger-v1"
DEFAULT_LEDGER_PATH = Path("data/state/shadow_ledger.db")
HORIZONS = (5, 10, 20)
SCORE_BUCKETS = ((None, 50.0, "<50"), (50.0, 55.0, "50-55"), (55.0, 60.0, "55-60"), (60.0, None, ">=60"))
DIRECTIONS = {"NEW_LONG": "LONG", "NEW_SHORT": "SHORT"}

# Market index per Yahoo suffix; listings without a known index are measured
# in absolute terms only.
BENCHMARKS = {
    "MI": "FTSEMIB.MI", "DE": "^GDAXI", "F": "^GDAXI", "PA": "^FCHI",
    "AS": "^AEX", "BR": "^BFX", "MC": "^IBEX", "L": "^FTSE", "SW": "^SSMI",
    "LS": "PSI20.LS", "VI": "^ATX", "CO": "^OMXC25", "ST": "^OMX", "HE": "^OMXH25",
}
US_BENCHMARK = "^GSPC"

PriceLoader = Callable[[str, date, date], list[tuple[date, float]]]


def benchmark_for(ticker: str) -> str | None:
    if "." not in ticker:
        return US_BENCHMARK
    return BENCHMARKS.get(ticker.rsplit(".", 1)[1].upper())


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class ShadowLedgerStore:
    def __init__(self, path: Path | str = DEFAULT_LEDGER_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                create table if not exists shadow_entries (
                    entry_id text primary key,
                    as_of text not null,
                    ticker text not null,
                    direction text not null,
                    source_label text not null,
                    payload_json text not null
                );
                create table if not exists shadow_measurements (
                    entry_id text not null,
                    horizon_sessions integer not null,
                    measured_at text not null,
                    payload_json text not null,
                    primary key (entry_id, horizon_sessions)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path))

    def add_entry(self, entry: dict[str, Any]) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "insert or ignore into shadow_entries values (?, ?, ?, ?, ?, ?)",
                (entry["entry_id"], entry["as_of"], entry["ticker"], entry["direction"],
                 entry["source_label"], json.dumps(entry, sort_keys=True)),
            )
            return cursor.rowcount == 1

    def entries(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "select payload_json from shadow_entries order by as_of, entry_id")]

    def measurements(self) -> dict[tuple[str, int], dict[str, Any]]:
        with self._connect() as connection:
            return {
                (row[0], row[1]): json.loads(row[2])
                for row in connection.execute(
                    "select entry_id, horizon_sessions, payload_json from shadow_measurements")
            }

    def add_measurement(self, entry_id: str, horizon: int, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "insert or ignore into shadow_measurements values (?, ?, ?, ?)",
                (entry_id, horizon, payload["measured_at"], json.dumps(payload, sort_keys=True)),
            )


# --- export --------------------------------------------------------------------


def _rows(connection: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    try:
        return [json.loads(row[0]) for row in connection.execute(sql)]
    except sqlite3.OperationalError:
        return []


def collect_scored_hypotheses(source_db: Path | str, source_label: str) -> list[dict[str, Any]]:
    """Directional hypotheses with a confidence-adjusted score, read-only."""
    uri = Path(source_db).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        outcomes = _rows(connection, "select payload_json from scanner_research_outcomes")
        scores = {s["opportunity_score_id"]: s for s in _rows(
            connection, "select payload_json from opportunity_scores")}
        research = {r["research_id"]: r for r in _rows(
            connection, "select payload_json from opportunity_research")}
    entries = []
    for outcome in outcomes:
        direction = DIRECTIONS.get(str(outcome.get("kind")))
        score = scores.get(outcome.get("opportunity_score_id"))
        if direction is None or score is None or score.get("confidence_adjusted_score") is None:
            continue
        diagnostics = (score.get("metadata") or {}).get("scoring_diagnostics") or {}
        direction_block = diagnostics.get("direction") if isinstance(diagnostics, dict) else None
        direction_block = direction_block if isinstance(direction_block, dict) else {}
        record = research.get(score.get("research_id")) or {}
        entries.append({
            "ledger_version": LEDGER_VERSION,
            "entry_id": score["opportunity_score_id"],
            "source_label": source_label,
            "as_of": score["created_at"],
            "ticker": score["ticker"],
            "benchmark": benchmark_for(score["ticker"]),
            "direction": direction,
            "hypothesis_id": outcome.get("hypothesis_id"),
            "integration_run_id": outcome.get("integration_run_id"),
            "outcome_status": outcome.get("status"),
            "outcome_reason": outcome.get("reason"),
            "opportunity_id": outcome.get("opportunity_id"),
            "raw_score": score.get("raw_score"),
            "confidence_adjusted_score": score.get("confidence_adjusted_score"),
            "score_confidence": score.get("score_confidence"),
            "scoring_status": score.get("scoring_status"),
            "components": {name: score.get(f"{name}_score") for name in
                           ("thesis", "catalyst", "fundamental", "technical", "expectations")},
            "research_id": score.get("research_id"),
            "research_status": record.get("research_status"),
            "research_confidence": score.get("research_confidence"),
            "evidence_quality": score.get("evidence_quality"),
            "directional_policy": direction_block.get("policy_version"),
            "direction_source": direction_block.get("direction_source"),
            "research_contract": (record.get("metadata") or {}).get("research_contract"),
        })
    return entries


def export_to_ledger(source_db: Path | str, ledger: ShadowLedgerStore, source_label: str) -> dict[str, int]:
    entries = collect_scored_hypotheses(source_db, source_label)
    added = sum(1 for entry in entries if ledger.add_entry(entry))
    return {"found": len(entries), "added": added, "already_present": len(entries) - added}


# --- measurement -----------------------------------------------------------------


def default_price_loader(symbol: str, start: date, end: date) -> list[tuple[date, float]]:
    import yfinance

    frame = yfinance.Ticker(symbol).history(
        start=start.isoformat(), end=end.isoformat(), interval="1d", auto_adjust=True,
    )
    closes = []
    for index, value in frame["Close"].items():
        if value is None or not math.isfinite(float(value)):
            continue
        closes.append((index.date(), float(value)))
    return closes


@dataclass(frozen=True)
class _Window:
    reference_date: date
    reference_close: float
    horizon_date: date
    horizon_close: float

    @property
    def simple_return(self) -> float:
        return self.horizon_close / self.reference_close - 1.0


def _window(series: list[tuple[date, float]], as_of: datetime, horizon: int) -> _Window | None:
    """Reference = last close strictly before the evaluation day (no look-ahead);
    horizon = the close ``horizon`` sessions later."""
    ordered = sorted(series)
    before = [index for index, (day, _) in enumerate(ordered) if day < as_of.date()]
    if not before:
        return None
    start = before[-1]
    if start + horizon >= len(ordered):
        return None
    reference_day, reference_close = ordered[start]
    horizon_day, horizon_close = ordered[start + horizon]
    return _Window(reference_day, reference_close, horizon_day, horizon_close)


def measure_ledger(
    ledger: ShadowLedgerStore,
    *,
    now: datetime | None = None,
    price_loader: PriceLoader = default_price_loader,
    horizons: Iterable[int] = HORIZONS,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    horizons = tuple(horizons)
    existing = ledger.measurements()
    cache: dict[str, list[tuple[date, float]]] = {}
    counts = {"measured": 0, "pending": 0, "unavailable": 0, "already_measured": 0}

    def series(symbol: str, start: date) -> list[tuple[date, float]]:
        key = f"{symbol}|{start.isoformat()}"
        if key not in cache:
            try:
                cache[key] = price_loader(symbol, start, now.date())
            except Exception:  # noqa: BLE001 - recorded as unavailable
                cache[key] = []
        return cache[key]

    for entry in ledger.entries():
        as_of = _utc(entry["as_of"])
        start = date.fromordinal(as_of.date().toordinal() - 10)
        for horizon in horizons:
            if (entry["entry_id"], horizon) in existing:
                counts["already_measured"] += 1
                continue
            stock = _window(series(entry["ticker"], start), as_of, horizon)
            if stock is None:
                stock_series = series(entry["ticker"], start)
                counts["unavailable" if not stock_series else "pending"] += 1
                continue
            sign = 1.0 if entry["direction"] == "LONG" else -1.0
            payload = {
                "measured_at": now.isoformat(),
                "horizon_sessions": horizon,
                "reference_date": stock.reference_date.isoformat(),
                "reference_close": stock.reference_close,
                "horizon_date": stock.horizon_date.isoformat(),
                "horizon_close": stock.horizon_close,
                "stock_return": stock.simple_return,
                "directional_return": sign * stock.simple_return,
                "benchmark": entry.get("benchmark"),
                "benchmark_return": None,
                "directional_excess_return": None,
            }
            if entry.get("benchmark"):
                bench = _window(series(entry["benchmark"], start), as_of, horizon)
                if bench is not None and bench.reference_date == stock.reference_date:
                    payload["benchmark_return"] = bench.simple_return
                    payload["directional_excess_return"] = sign * (
                        stock.simple_return - bench.simple_return
                    )
            ledger.add_measurement(entry["entry_id"], horizon, payload)
            counts["measured"] += 1
    return counts


# --- listing ---------------------------------------------------------------------


LIST_COLUMNS = (
    "as_of", "ticker", "direction", "confidence_adjusted_score", "raw_score",
    "research_status", "research_confidence", "evidence_quality", "outcome_reason",
    "directional_policy", "source_label",
    "return_5", "return_10", "return_20", "excess_5", "excess_10", "excess_20",
)


def list_entries(
    ledger: ShadowLedgerStore,
    *,
    ticker: str | None = None,
    direction: str | None = None,
    complete_only: bool = False,
    policies: set[str] | None = None,
) -> list[dict[str, Any]]:
    """One flat row per entry with its measured returns (None if pending)."""
    measurements = ledger.measurements()
    rows = []
    for entry in ledger.entries():
        if ticker and entry["ticker"].upper() != ticker.strip().upper():
            continue
        if direction and entry["direction"] != direction.strip().upper():
            continue
        if complete_only and entry.get("research_status") != "COMPLETE":
            continue
        if policies is not None and entry.get("directional_policy") not in policies:
            continue
        row = {column: entry.get(column) for column in LIST_COLUMNS[:11]}
        for horizon in HORIZONS:
            measured = measurements.get((entry["entry_id"], horizon))
            row[f"return_{horizon}"] = measured["directional_return"] if measured else None
            row[f"excess_{horizon}"] = measured["directional_excess_return"] if measured else None
        rows.append(row)
    return rows


def list_text(rows: list[dict[str, Any]]) -> str:
    def pct(value: float | None) -> str:
        return f"{value:+.1%}" if value is not None else "-"

    lines = [
        f"{'evaluated (UTC)':16}  {'ticker':10} {'dir':5} {'adj':>5} {'raw':>5}  "
        f"{'research':9} {'outcome':28} {'5s':>7} {'10s':>7} {'20s':>7}"
    ]
    for row in rows:
        lines.append(
            f"{str(row['as_of'])[:16]:16}  {row['ticker']:10} {row['direction']:5} "
            f"{row['confidence_adjusted_score']:5.1f} {row['raw_score'] or 0:5.1f}  "
            f"{str(row['research_status']):9} {str(row['outcome_reason']):28} "
            f"{pct(row['return_5']):>7} {pct(row['return_10']):>7} {pct(row['return_20']):>7}"
        )
    lines.append("")
    lines.append(f"{len(rows)} entries; returns are directional (positive = the hypothesis was right).")
    return "\n".join(lines) + "\n"


def write_csv(rows: list[dict[str, Any]], path: Path, *, decimal_comma: bool = True) -> None:
    """CSV for Excel: ';' separator and decimal comma by default (Italian locale)."""
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)

    def cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            text = f"{value:.6f}".rstrip("0").rstrip(".")
            return text.replace(".", ",") if decimal_comma else text
        return str(value)

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";" if decimal_comma else ",")
        writer.writerow(LIST_COLUMNS)
        for row in rows:
            writer.writerow([cell(row[column]) for column in LIST_COLUMNS])

# --- report ----------------------------------------------------------------------


def _bucket(score: float) -> str:
    for low, high, label in SCORE_BUCKETS:
        if (low is None or score >= low) and (high is None or score < high):
            return label
    raise ValueError(score)


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2.0 + 1.0
        for index in range(position, end + 1):
            ranks[order[index]] = average
        position = end + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    sx = math.sqrt(sum((v - mx) ** 2 for v in rx))
    sy = math.sqrt(sum((v - my) ** 2 for v in ry))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)) / (sx * sy)


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "hit_rate": sum(1 for value in values if value > 0) / len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
    }


def build_report(
    ledger: ShadowLedgerStore,
    *,
    policies: set[str] | None = None,
    complete_only: bool = False,
) -> dict[str, Any]:
    measurements = ledger.measurements()
    entries = [
        entry for entry in ledger.entries()
        if (policies is None or entry.get("directional_policy") in policies)
        and (not complete_only or entry.get("research_status") == "COMPLETE")
    ]
    horizons = {}
    for horizon in HORIZONS:
        rows = [
            (entry, measurements[(entry["entry_id"], horizon)])
            for entry in entries if (entry["entry_id"], horizon) in measurements
        ]
        buckets = {}
        for _, _, label in SCORE_BUCKETS:
            selected = [m for e, m in rows if _bucket(float(e["confidence_adjusted_score"])) == label]
            buckets[label] = {
                "directional_return": _summary([m["directional_return"] for m in selected]),
                "directional_excess_return": _summary([
                    m["directional_excess_return"] for m in selected
                    if m["directional_excess_return"] is not None
                ]),
            }
        by_direction = {
            direction: _summary([m["directional_return"] for e, m in rows if e["direction"] == direction])
            for direction in ("LONG", "SHORT")
        }
        horizons[str(horizon)] = {
            "measured": len(rows),
            "buckets": buckets,
            "by_direction": by_direction,
            "spearman_score_vs_directional_return": spearman(
                [float(e["confidence_adjusted_score"]) for e, _ in rows],
                [m["directional_return"] for _, m in rows],
            ),
            "spearman_score_vs_excess_return": spearman(
                [float(e["confidence_adjusted_score"]) for e, m in rows if m["directional_excess_return"] is not None],
                [m["directional_excess_return"] for _, m in rows if m["directional_excess_return"] is not None],
            ),
        }
    return {
        "ledger_version": LEDGER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {"policies": sorted(policies) if policies else None, "complete_only": complete_only},
        "entries": len(entries),
        "horizons": horizons,
        "caution": (
            "Small samples: read hit rates and means together with n; a bucket "
            "with fewer than 30 measurements is not evidence for a threshold change."
        ),
    }


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Shadow ledger report ({report['generated_at'][:19]}Z)",
        "",
        f"Entries: {report['entries']}; filters: {json.dumps(report['filters'])}",
        "",
        report["caution"],
    ]
    for horizon, data in report["horizons"].items():
        lines += ["", f"## {horizon} sessions (measured: {data['measured']})", "",
                  "| Adjusted score | n | Hit rate | Mean | Median | Excess n | Excess mean |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for label, bucket in data["buckets"].items():
            raw, excess = bucket["directional_return"], bucket["directional_excess_return"]
            if raw["n"] == 0:
                lines.append(f"| {label} | 0 | | | | | |")
                continue
            excess_mean = f"{excess['mean']:+.2%}" if excess["n"] else ""
            lines.append(
                f"| {label} | {raw['n']} | {raw['hit_rate']:.0%} | {raw['mean']:+.2%} | "
                f"{raw['median']:+.2%} | {excess['n']} | {excess_mean} |"
            )
        corr = data["spearman_score_vs_directional_return"]
        corr_excess = data["spearman_score_vs_excess_return"]
        lines.append("")
        lines.append(
            "Spearman(score, directional return): "
            + (f"{corr:+.2f}" if corr is not None else "n/a")
            + "; vs excess: " + (f"{corr_excess:+.2f}" if corr_excess is not None else "n/a")
        )
    return "\n".join(lines) + "\n"
