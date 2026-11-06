"""E2E-S4.0A validation harness.

Operational tooling only: it does not change any frozen business contract.
Every run happens in an isolated sandbox directory that receives a consistent
SQLite copy of the state database, so the production database under
``data/state`` is only ever opened read-only.

Subcommands::

    python -m tools.stage4_validation preflight --out preflight.json
    python -m tools.stage4_validation replay --workdir DIR --out replay.json
    python -m tools.stage4_validation live --workdir DIR --out live.json
    python -m tools.stage4_validation inspect --database DB --since ISO_TS

``replay`` resumes an existing root S4.0A run in ``CACHE_ONLY`` mode using the
verbatim persisted configuration and request, with outbound sockets blocked.
It proves run identity, zero network, zero LLM inference and zero side effects.

``live`` executes the real bounded replenishment CLI against the sandbox copy
with a small wave budget. It never touches the production database, and it
attaches an inspection of the evidence, research, scores and outcomes persisted
by the session, including the AI-8C.3-R1 acceptance checks.

``inspect`` produces the same inspection for any database and timestamp.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any, Iterator
from urllib import request as urllib_request


REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_REPORT_DIRECTORY = Path("data/cache/e2e/stage4/first_complete_e2e")
ROOT_REPORT_PREFIX = "stage4_first_complete_e2e_"
LISTING_REFERENCES = Path("config/scanner/listing_start_references_v1.json")
COUNTED_TABLES = (
    "ai_inferences",
    "trade_opportunities",
    "trade_proposals",
    "execution_plans",
    "portfolio_snapshots",
    "stage4_e2e_runs",
    "stage4_e2e_stage_attempts",
    "scanner_research_outcomes",
    "stage4_candidate_replenishment_sessions",
    "stage4_candidate_replenishment_waves",
)
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


class HarnessError(RuntimeError):
    """Raised when the harness refuses to continue."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Path handling
# ---------------------------------------------------------------------------


def is_windows_absolute(value: str) -> bool:
    return bool(_WINDOWS_ABSOLUTE.match(value))


def is_host_absolute(value: str) -> bool:
    """True when the string is an absolute path on the current OS."""
    if os.name == "nt":
        return PureWindowsPath(value).is_absolute()
    return value.startswith("/")


def _parts(value: str) -> list[str]:
    return [part for part in re.split(r"[\\/]+", value) if part]


def locate_source(value: str, repo_root: Path = REPO_ROOT) -> Path:
    """Locate the repository file that a persisted path string refers to.

    Persisted paths may be relative to the original working directory or
    absolute on the operator's Windows machine. Absolute paths on the current
    OS are used as they are. Foreign absolute paths are rebased onto the
    repository by locating the repository folder name inside the path.
    """
    if is_host_absolute(value):
        return Path(value)
    parts = _parts(value)
    if is_windows_absolute(value):
        names = [part.lower() for part in parts]
        anchor = repo_root.name.lower()
        if anchor in names:
            index = len(names) - 1 - names[::-1].index(anchor)
            return repo_root.joinpath(*parts[index + 1:])
        raise HarnessError(
            "FOREIGN_ABSOLUTE_PATH",
            f"cannot rebase {value!r} onto repository {repo_root}",
        )
    return repo_root.joinpath(*parts)


def sandbox_target(workdir: Path, value: str) -> Path | None:
    """Return where a persisted path string resolves inside the sandbox.

    ``None`` means the path is absolute on this OS and is used in place.
    The string is joined verbatim so the child process, whose working
    directory is the sandbox, resolves exactly the persisted string.
    """
    if is_host_absolute(value):
        return None
    return Path(os.path.join(str(workdir), value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_consistent_copy(source: Path, target: Path) -> None:
    """Copy a SQLite database through the backup API (WAL-safe, read-only)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    uri = source.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as src, sqlite3.connect(str(target)) as dst:
        src.backup(dst)


def table_counts(database: Path) -> dict[str, int | None]:
    uri = database.resolve().as_uri() + "?mode=ro"
    counts: dict[str, int | None] = {}
    with sqlite3.connect(uri, uri=True) as connection:
        existing = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        for table in COUNTED_TABLES:
            counts[table] = (
                connection.execute(f"select count(*) from {table}").fetchone()[0]
                if table in existing else None
            )
    return counts


# ---------------------------------------------------------------------------
# Root report and sandbox materialisation
# ---------------------------------------------------------------------------


def find_root_report(run_id: str | None, repo_root: Path = REPO_ROOT) -> Path:
    directory = repo_root / ROOT_REPORT_DIRECTORY
    if run_id:
        path = directory / f"{ROOT_REPORT_PREFIX}{run_id}.json"
        if not path.is_file():
            raise HarnessError("ROOT_REPORT_NOT_FOUND", str(path))
        return path
    candidates = sorted(
        directory.glob(f"{ROOT_REPORT_PREFIX}s4a-*.json"),
        key=lambda item: item.stat().st_mtime,
    )
    if not candidates:
        raise HarnessError("ROOT_REPORT_NOT_FOUND", str(directory))
    return candidates[-1]


def _copy_into_sandbox(workdir: Path, value: str, repo_root: Path) -> dict[str, Any]:
    target = sandbox_target(workdir, value)
    source = locate_source(value, repo_root)
    if not source.exists():
        raise HarnessError("SOURCE_MISSING", f"{value!r} -> {source}")
    if target is None:
        return {"path": value, "mode": "IN_PLACE_READ_ONLY", "source": str(source)}
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)
    return {"path": value, "mode": "COPIED", "source": str(source)}


def materialize_sandbox(
    report: dict[str, Any],
    workdir: Path,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    configuration = report["configuration"]
    request = report["request"]
    run = report["run"]
    if configuration.get("selected_parameters") is not None:
        raise HarnessError(
            "SELECTED_PARAMETERS_NOT_SUPPORTED",
            "replay supports root runs without operator overlay",
        )
    database_path = configuration["database_path"]
    if is_host_absolute(database_path) or is_windows_absolute(database_path):
        raise HarnessError(
            "ABSOLUTE_DB_PATH_REFUSED",
            "an absolute database path cannot be redirected to a sandbox "
            "without changing the run identity",
        )
    workdir.mkdir(parents=True, exist_ok=True)
    source_database = locate_source(database_path, repo_root)
    if not source_database.is_file():
        raise HarnessError("SOURCE_MISSING", str(source_database))
    sandbox_database = sandbox_target(workdir, database_path)
    assert sandbox_database is not None
    sqlite_consistent_copy(source_database, sandbox_database)

    files = [
        _copy_into_sandbox(workdir, request["portfolio_file"], repo_root),
        _copy_into_sandbox(workdir, configuration["eligibility_report_path"], repo_root),
        _copy_into_sandbox(workdir, configuration["mapping_report_path"], repo_root),
    ]
    files.extend(
        _copy_into_sandbox(workdir, value, repo_root)
        for value in configuration["history_report_paths"]
    )
    if run.get("watch_universe_report_path"):
        files.append(
            _copy_into_sandbox(workdir, run["watch_universe_report_path"], repo_root)
        )

    portfolio = sandbox_target(workdir, request["portfolio_file"]) or Path(
        request["portfolio_file"]
    )
    portfolio_fingerprint = sha256_file(portfolio)
    return {
        "workdir": str(workdir),
        "source_database": str(source_database),
        "sandbox_database": str(sandbox_database),
        "files": files,
        "portfolio_fingerprint_matches": (
            portfolio_fingerprint == request["portfolio_file_fingerprint"]
        ),
    }


# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------


@contextmanager
def block_network() -> Iterator[list[str]]:
    """Block outbound socket connections and record every attempt."""
    attempts: list[str] = []
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create = socket.create_connection

    def refuse(address: Any) -> None:
        attempts.append(repr(address))
        raise ConnectionRefusedError(f"validation harness blocked {address!r}")

    def connect(self, address):  # noqa: ANN001
        refuse(address)

    def connect_ex(self, address):  # noqa: ANN001
        refuse(address)

    def create_connection(address, *args, **kwargs):  # noqa: ANN001
        refuse(address)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.create_connection = create_connection  # type: ignore[assignment]
    try:
        yield attempts
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
        socket.create_connection = original_create  # type: ignore[assignment]


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check(name: str, passed: bool, detail: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _verdict(checks: list[dict[str, Any]]) -> str:
    return "PASS" if all(item["passed"] for item in checks) else "FAIL"


def _write(path: Path | None, value: dict[str, Any]) -> None:
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _requirements() -> dict[str, str]:
    pins: dict[str, str] = {}
    for name in ("requirements.txt", "requirements-scanner-history.txt"):
        path = REPO_ROOT / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        encoding = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
        for line in raw.decode(encoding).splitlines():
            line = line.split("#", 1)[0].strip()
            if "==" in line:
                package, version = line.split("==", 1)
                pins[package.strip()] = version.strip()
    return pins


def run_preflight(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    checks.append(_check("python>=3.10", sys.version_info >= (3, 10), sys.version))
    mismatched = {}
    for package, expected in _requirements().items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        if installed != expected:
            mismatched[package] = {"expected": expected, "installed": installed}
    checks.append(_check("requirements pinned versions installed", not mismatched, mismatched))

    database = REPO_ROOT / "data" / "state" / "portfolio_cio.db"
    checks.append(_check("state database present", database.is_file(), str(database)))
    try:
        report = find_root_report(args.run_id)
        checks.append(_check("root S4.0A report present", True, str(report)))
    except HarnessError as exc:
        checks.append(_check("root S4.0A report present", False, str(exc)))

    if "onedrive" in str(REPO_ROOT).lower():
        warnings.append(
            "Repository is inside OneDrive: SQLite files under sync can be "
            "locked or corrupted during writes. Prefer a non-synced folder."
        )

    live_checks: list[dict[str, Any]] = []
    try:
        with urllib_request.urlopen(f"{args.ollama_url.rstrip('/')}/api/tags", timeout=5) as response:
            tags = json.loads(response.read().decode("utf-8"))
        models = sorted(item.get("name", "") for item in tags.get("models", []))
        live_checks.append(_check("ollama reachable", True, args.ollama_url))
        live_checks.append(_check(f"ollama model {args.model} pulled", args.model in models, models))
    except Exception as exc:  # noqa: BLE001 - diagnostic surface
        live_checks.append(_check("ollama reachable", False, repr(exc)))

    if not args.skip_yahoo:
        try:
            import yfinance

            started = time.monotonic()
            history = yfinance.Ticker(args.yahoo_probe).history(period="5d", interval="1d")
            live_checks.append(
                _check(
                    f"yahoo history reachable ({args.yahoo_probe})",
                    not history.empty,
                    {"rows": int(len(history)), "seconds": round(time.monotonic() - started, 2)},
                )
            )
        except Exception as exc:  # noqa: BLE001 - diagnostic surface
            live_checks.append(_check(f"yahoo history reachable ({args.yahoo_probe})", False, repr(exc)))

    return {
        "step": "preflight",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": sys.executable,
        "repo_root": str(REPO_ROOT),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "checks": checks,
        "live_checks": live_checks,
        "live_ready": all(item["passed"] for item in live_checks),
        "warnings": warnings,
        "verdict": _verdict(checks),
    }


def run_replay(args: argparse.Namespace) -> dict[str, Any]:
    from app.e2e.stage4_contracts import Stage4E2ERequest, Stage4Mode
    from app.e2e.stage4_runtime import CanonicalStage4Runtime, Stage4RuntimeConfiguration
    from app.e2e.stage4_service import Stage4E2EService
    from app.e2e.stage4_store import Stage4E2EStore

    report_path = find_root_report(args.run_id)
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    expected_run_id = report["run"]["run_id"]
    workdir = Path(args.workdir).resolve()
    sandbox = materialize_sandbox(report, workdir)
    source_database = Path(sandbox["source_database"])
    sandbox_database = Path(sandbox["sandbox_database"])
    source_hash_before = sha256_file(source_database)
    counts_before = table_counts(sandbox_database)

    configuration = Stage4RuntimeConfiguration(**report["configuration"])
    request_payload = dict(report["request"])
    request_payload["mode"] = Stage4Mode.CACHE_ONLY.value
    request = Stage4E2ERequest(**request_payload)

    error = None
    run = None
    started = time.monotonic()
    with working_directory(workdir), block_network() as network_attempts:
        try:
            store = Stage4E2EStore(configuration.database_path)
            service = Stage4E2EService(
                store=store,
                adapters=CanonicalStage4Runtime(configuration).adapters(),
            )
            run = service.run(request, now=datetime.now(timezone.utc))
        except Exception as exc:  # noqa: BLE001 - reported as a failed check
            error = repr(exc)
    elapsed = round(time.monotonic() - started, 2)

    counts_after = table_counts(sandbox_database)
    source_hash_after = sha256_file(source_database)
    checks = [
        _check("replay completed without exception", error is None, error),
        _check("portfolio file fingerprint matches persisted request", sandbox["portfolio_fingerprint_matches"]),
        _check("configuration fingerprint reproduced", configuration.fingerprint == report["configuration_fingerprint"],
               {"expected": report["configuration_fingerprint"], "actual": configuration.fingerprint}),
    ]
    if run is not None:
        checks.extend([
            _check("run identity reproduced", run.run_id == expected_run_id,
                   {"expected": expected_run_id, "actual": run.run_id}),
            _check("mode is CACHE_ONLY", run.mode.value == "CACHE_ONLY", run.mode.value),
            _check("run reports zero network calls", run.network_calls == 0, run.network_calls),
            _check("zero side effects", not any((run.broker_orders_submitted, run.portfolio_mutations, run.automatic_executions)),
                   [run.broker_orders_submitted, run.portfolio_mutations, run.automatic_executions]),
            _check("still a non-authorized dry run", run.dry_run and not run.execution_authorized),
        ])
    checks.extend([
        _check("no outbound socket attempted", not network_attempts, network_attempts[:10]),
        _check("no new LLM inference persisted", counts_after["ai_inferences"] == counts_before["ai_inferences"],
               [counts_before["ai_inferences"], counts_after["ai_inferences"]]),
        _check("no execution plan created", counts_after["execution_plans"] == counts_before["execution_plans"],
               [counts_before["execution_plans"], counts_after["execution_plans"]]),
        _check("production database untouched", source_hash_before == source_hash_after, source_hash_after[:16]),
    ])
    return {
        "step": "replay",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root_report": str(report_path),
        "expected_run_id": expected_run_id,
        "sandbox": sandbox,
        "elapsed_seconds": elapsed,
        "run": run.model_dump(mode="json") if run is not None else None,
        "table_counts_before": counts_before,
        "table_counts_after": counts_after,
        "checks": checks,
        "verdict": _verdict(checks),
    }


def run_live(args: argparse.Namespace) -> dict[str, Any]:
    report_path = find_root_report(args.run_id)
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    configuration = report["configuration"]
    request = report["request"]
    run = report["run"]
    workdir = Path(args.workdir).resolve()
    sandbox = materialize_sandbox(report, workdir)
    source_database = Path(sandbox["source_database"])
    sandbox_database = Path(sandbox["sandbox_database"])
    source_hash_before = sha256_file(source_database)
    counts_before = table_counts(sandbox_database)

    stamp = datetime.now(timezone.utc)
    since = stamp.isoformat().replace("+00:00", "Z")
    output_directory = Path("validation_output") / stamp.strftime("%Y%m%dT%H%M%SZ")
    command = [
        sys.executable, "-m", "tools.live_stage4_candidate_replenishment",
        "--root-run-id", run["run_id"],
        "--portfolio-file", request["portfolio_file"],
        "--eligibility-report", configuration["eligibility_report_path"],
        "--mapping-report", configuration["mapping_report_path"],
        "--as-of", stamp.isoformat().replace("+00:00", "Z"),
        "--mode", args.mode,
        "--db", configuration["database_path"],
        "--output-directory", str(output_directory),
        "--listing-references", str(REPO_ROOT / LISTING_REFERENCES),
        "--model", args.model,
        "--max-waves", str(args.max_waves),
        "--max-hypotheses", str(args.max_hypotheses),
    ]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), *([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else [])]
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    log_path = Path(args.log) if args.log else workdir / "live_cli.log"
    started = time.monotonic()
    timed_out = False
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n\n")
        log.flush()
        process = subprocess.Popen(
            command, cwd=workdir, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        assert process.stdout is not None
        deadline = started + args.timeout_seconds
        for line in process.stdout:
            sys.stderr.write(line)
            log.write(line)
            log.flush()
            if time.monotonic() > deadline:
                timed_out = True
                process.kill()
                break
        exit_code = process.wait()
    elapsed = round(time.monotonic() - started, 2)

    reports = sorted((workdir / output_directory).glob("stage4_candidate_replenishment_*.json"))
    replenishment = json.loads(reports[-1].read_text(encoding="utf-8")) if reports else None
    counts_after = table_counts(sandbox_database)
    source_hash_after = sha256_file(source_database)
    result = (replenishment or {}).get("result", {})
    checks = [
        _check("CLI finished before timeout", not timed_out, args.timeout_seconds),
        _check("CLI exit code is 0 (opportunity) or 2 (valid non-complete)", exit_code in (0, 2), exit_code),
        _check("replenishment report written", replenishment is not None, [str(item) for item in reports]),
        _check("zero side effects", replenishment is not None and not any((
            result.get("broker_orders_submitted"), result.get("portfolio_mutations"), result.get("automatic_executions"),
        )), [result.get("broker_orders_submitted"), result.get("portfolio_mutations"), result.get("automatic_executions")]),
        _check("no execution plan created", counts_after["execution_plans"] == counts_before["execution_plans"],
               [counts_before["execution_plans"], counts_after["execution_plans"]]),
        _check("production database untouched", source_hash_before == source_hash_after, source_hash_after[:16]),
    ]
    try:
        inspection = inspect_research(sandbox_database, since)
    except Exception as exc:  # noqa: BLE001 - diagnostic surface
        inspection = {"error": repr(exc), "r1_acceptance": "FAIL", "r1_checks": []}
    shadow_export = None
    if not args.no_shadow_ledger:
        try:
            from app.e2e.shadow_ledger import ShadowLedgerStore, export_to_ledger

            shadow_export = export_to_ledger(
                sandbox_database,
                ShadowLedgerStore(REPO_ROOT / "data" / "state" / "shadow_ledger.db"),
                f"validation:{workdir.parent.name}",
            )
        except Exception as exc:  # noqa: BLE001 - reported, never fails the level
            shadow_export = {"error": repr(exc)}
    waves = (replenishment or {}).get("waves", [])
    return {
        "step": "live",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "root_run_id": run["run_id"],
        "command": command,
        "exit_code": exit_code,
        "elapsed_seconds": elapsed,
        "log": str(log_path),
        "sandbox": sandbox,
        "replenishment_report": str(reports[-1]) if reports else None,
        "summary": {
            "terminal_reason": result.get("terminal_reason"),
            "opportunity_ids": result.get("opportunity_ids"),
            "attempted_listing_keys": result.get("attempted_listing_keys"),
            "frontier_ranking_mode": result.get("frontier_ranking_mode"),
            "frontier_ranking_qualified_count": result.get("frontier_ranking_qualified_count"),
            "frontier_ranking_network_calls": result.get("frontier_ranking_network_calls"),
            "frontier_ranking_provider_circuit_open": result.get("frontier_ranking_provider_circuit_open"),
            "quarantined_listing_keys": result.get("quarantined_listing_keys"),
            "waves": [
                {
                    "wave_index": wave.get("wave_index"),
                    "status": wave.get("status"),
                    "terminal_reason": wave.get("terminal_reason"),
                    "listing_keys": wave.get("listing_keys"),
                    "opportunity_ids": wave.get("opportunity_ids"),
                    "diagnostics": [
                        item for item in wave.get("diagnostics", [])
                        if not str(item).startswith("history_report=")
                    ],
                }
                for wave in waves
            ],
        },
        "shadow_ledger_export": shadow_export,
        "research_inspection": inspection,
        "r1_acceptance": inspection.get("r1_acceptance"),
        "table_counts_before": counts_before,
        "table_counts_after": counts_after,
        "checks": checks,
        "verdict": _verdict(checks),
    }



# ---------------------------------------------------------------------------
# Research inspection (AI-8C.3-R1 acceptance evidence)
# ---------------------------------------------------------------------------


_VOLATILITY_STATEMENT = "annualized volatility:"
_R1_ADAPTER_VERSION = "stage4-research-technical-v2"


def _json_rows(connection: sqlite3.Connection, sql: str, parameters: tuple) -> list[dict[str, Any]]:
    try:
        return [json.loads(row[0]) for row in connection.execute(sql, parameters)]
    except sqlite3.OperationalError:
        return []


def _material_unknown_terms() -> tuple[str, ...]:
    from app.ai.research_validator import ResearchCoverageValidator

    return tuple(ResearchCoverageValidator._MATERIAL_UNKNOWN_TERMS)


def inspect_research(database: Path, since: str) -> dict[str, Any]:
    """Summarize evidence, research, scores and outcomes persisted since ``since``."""
    terms = _material_unknown_terms()
    uri = database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        bundles = _json_rows(
            connection,
            "select payload_json from scanner_evidence_bundles where created_at >= ? order by created_at",
            (since,),
        )
        research = _json_rows(
            connection,
            "select payload_json from opportunity_research where created_at >= ? order by created_at",
            (since,),
        )
        scores = _json_rows(
            connection,
            "select payload_json from opportunity_scores where created_at >= ? order by created_at",
            (since,),
        )
        outcomes = _json_rows(
            connection,
            "select payload_json from scanner_research_outcomes where updated_at >= ? order by updated_at",
            (since,),
        )

    technical_items = []
    bundle_has_volatility: dict[str, bool] = {}
    bundle_summaries: dict[str, dict[str, Any]] = {}
    for bundle in bundles:
        kinds: dict[str, int] = {}
        texts: dict[str, list[str]] = {}
        for item in bundle.get("items", []):
            kind = str(item.get("kind"))
            kinds[kind] = kinds.get(kind, 0) + 1
            if kind in {"FUNDAMENTAL", "ANALYST"}:
                texts.setdefault(kind, []).append(
                    str(item.get("evidence", {}).get("text", ""))[:600]
                )
        fundamental_dates = [
            str(item.get("evidence", {}).get("published_at"))
            for item in bundle.get("items", [])
            if item.get("kind") == "FUNDAMENTAL" and item.get("evidence", {}).get("published_at")
        ]
        fundamental_age = None
        if fundamental_dates and bundle.get("created_at"):
            try:
                created = datetime.fromisoformat(str(bundle["created_at"]).replace("Z", "+00:00"))
                quarter = datetime.fromisoformat(fundamental_dates[0].replace("Z", "+00:00"))
                fundamental_age = round((created - quarter).total_seconds() / 86400.0, 1)
            except ValueError:
                fundamental_age = None
        bundle_summaries[str(bundle.get("bundle_id"))] = {
            "fundamental_period_end": fundamental_dates[0] if fundamental_dates else None,
            "fundamental_age_days": fundamental_age,
            "evidence_kinds": dict(sorted(kinds.items())),
            "warnings": [str(value) for value in bundle.get("warnings", [])][:10],
            "fundamental_and_analyst_texts": texts,
        }
        has_volatility = False
        for item in bundle.get("items", []):
            if item.get("kind") != "TECHNICAL":
                continue
            text = str(item.get("evidence", {}).get("text", ""))
            stated = _VOLATILITY_STATEMENT in text.lower()
            has_volatility = has_volatility or stated
            technical_items.append({
                "bundle_id": bundle.get("bundle_id"),
                "ticker": bundle.get("ticker"),
                "adapter_version": item.get("metadata", {}).get("adapter_version"),
                "canonical_technical_contract": item.get("source", {}).get("metadata", {}).get(
                    "canonical_technical_contract"
                ),
                "volatility_stated": stated,
            })
        bundle_has_volatility[str(bundle.get("bundle_id"))] = has_volatility

    research_bundle = {
        str(outcome.get("research_id")): str(outcome.get("evidence_bundle_id"))
        for outcome in outcomes
        if outcome.get("research_id")
    }
    from types import SimpleNamespace

    from app.ai.research_validator import ResearchCoverageValidator

    validator = ResearchCoverageValidator()
    research_rows = []
    for record in research:
        unknowns = [str(item) for item in record.get("unknowns", [])]
        forward = [str(item) for item in record.get("forward_uncertainties", []) or []]
        material = [item for item in unknowns if any(term in item.lower() for term in terms)]
        metadata = record.get("metadata") or {}
        # Research from contract v3 on records gaps computed with its evidence
        # (context gaps exempted); recomputing without evidence would overcount.
        gaps = metadata.get("material_gaps")
        if not isinstance(gaps, list):
            gaps = validator.material_gaps(
                SimpleNamespace(unknowns=unknowns, forward_uncertainties=forward)
            )
        bundle_id = research_bundle.get(str(record.get("research_id")))
        research_rows.append({
            "research_id": record.get("research_id"),
            "ticker": record.get("ticker"),
            "research_status": record.get("research_status"),
            "evidence_quality": record.get("evidence_quality"),
            "research_confidence": record.get("research_confidence"),
            "requires_additional_research": record.get("requires_additional_research"),
            "evidence_bundle_id": bundle_id,
            "bundle_states_volatility": bundle_has_volatility.get(str(bundle_id)),
            # R1 is about the metric being supplied. An unknown that quotes the
            # supplied value (for example "volatility persistence beyond the
            # current 22.37% level") is a forward uncertainty, not a gap.
            # Same rule as the research validator (AI-8C.2-R1): implication,
            # impact and forward items about volatility are not claims that
            # the supplied metric is missing.
            "volatility_listed_unknown": any(
                "volatil" in item.lower()
                and ResearchCoverageValidator._unknown_conflicts_with_evidence(
                    item, "annualized volatility"
                )
                for item in unknowns
            ),
            "volatility_forward_mentions": [
                item for item in unknowns
                if "volatil" in item.lower() and re.search(r"\d", item)
            ],
            "material_unknowns": material,
            "other_unknowns": [item for item in unknowns if item not in material],
            "forward_uncertainties": forward,
            "material_gaps": gaps,
            "research_contract": metadata.get("research_contract"),
            "context_gaps": metadata.get("context_gaps") or [],
            "initial_research_confidence": metadata.get("initial_research_confidence"),
            "research_confidence_repaired": metadata.get("research_confidence_repaired"),
            "evidence_kinds": bundle_summaries.get(str(bundle_id), {}).get("evidence_kinds"),
            "bundle_warnings": bundle_summaries.get(str(bundle_id), {}).get("warnings"),
            "fundamental_period_end": bundle_summaries.get(str(bundle_id), {}).get("fundamental_period_end"),
            "fundamental_age_days": bundle_summaries.get(str(bundle_id), {}).get("fundamental_age_days"),
        })

    score_rows = [_score_row(record) for record in scores]
    from app.ai.research_service import RESEARCH_CONTRACT_VERSION

    c2r1_checks = [
        _check(
            f"every new research uses {RESEARCH_CONTRACT_VERSION}",
            bool(research_rows) and all(
                row["research_contract"] == RESEARCH_CONTRACT_VERSION
                for row in research_rows
            ),
            sorted({str(row["research_contract"]) for row in research_rows}),
        ),
        _check(
            "no MEDIUM/HIGH-quality research keeps research_confidence below 0.2",
            not any(
                row["evidence_quality"] in ("MEDIUM", "HIGH")
                and row["research_status"] != "INSUFFICIENT_EVIDENCE"
                and row["research_confidence"] is not None
                and float(row["research_confidence"]) < 0.2
                for row in research_rows
            ),
            [row["research_id"] for row in research_rows
             if row["evidence_quality"] in ("MEDIUM", "HIGH")
             and row["research_status"] != "INSUFFICIENT_EVIDENCE"
             and row["research_confidence"] is not None
             and float(row["research_confidence"]) < 0.2],
        ),
        _check(
            "no COMPLETE research contains a material gap",
            not any(row["research_status"] == "COMPLETE" and row["material_gaps"] for row in research_rows),
            [row["research_id"] for row in research_rows if row["research_status"] == "COMPLETE" and row["material_gaps"]],
        ),
    ]
    r2_checks, pairs = _r2_checks(score_rows)
    outcome_rows = [
        {
            "hypothesis_id": outcome.get("hypothesis_id"),
            "kind": outcome.get("kind"),
            "subject": outcome.get("subject_value"),
            "status": outcome.get("status"),
            "reason": outcome.get("reason"),
            "research_id": outcome.get("research_id"),
            "opportunity_id": outcome.get("opportunity_id"),
            "diagnostics": [str(item)[:600] for item in outcome.get("diagnostics") or []],
        }
        for outcome in outcomes
    ]

    r1_checks = [
        _check(
            "new TECHNICAL evidence persisted",
            bool(technical_items),
            len(technical_items),
        ),
        _check(
            f"every new TECHNICAL evidence uses {_R1_ADAPTER_VERSION}",
            bool(technical_items)
            and all(item["adapter_version"] == _R1_ADAPTER_VERSION for item in technical_items),
            sorted({str(item["adapter_version"]) for item in technical_items}),
        ),
        _check(
            "volatility stated in new TECHNICAL evidence",
            any(item["volatility_stated"] for item in technical_items),
            [item["ticker"] for item in technical_items if item["volatility_stated"]],
        ),
        _check(
            "no research lists volatility unknown when its evidence states it",
            not any(
                row["bundle_states_volatility"] and row["volatility_listed_unknown"]
                for row in research_rows
            ),
            [
                row["research_id"]
                for row in research_rows
                if row["bundle_states_volatility"] and row["volatility_listed_unknown"]
            ],
        ),
    ]
    return {
        "since": since,
        "database": str(database),
        "technical_evidence": technical_items,
        "evidence_bundles": bundle_summaries,
        "research": research_rows,
        "scores": score_rows,
        "outcomes": outcome_rows,
        "research_status_counts": dict(
            sorted(_count(row["research_status"] for row in research_rows).items())
        ),
        "processing_failures": [
            {key: row[key] for key in ("subject", "kind", "hypothesis_id", "diagnostics")}
            for row in outcome_rows
            if row["status"] == "FAILED"
        ],
        "outcome_reason_counts": dict(
            sorted(_count(f"{row['status']}/{row['reason']}" for row in outcome_rows).items())
        ),
        "r1_checks": r1_checks,
        "r1_acceptance": _verdict(r1_checks),
        "c2r1_checks": c2r1_checks,
        "c2r1_acceptance": _verdict(c2r1_checks) if research_rows else "NOT_APPLICABLE",
        "complete_research_count": sum(
            1 for row in research_rows if row["research_status"] == "COMPLETE"
        ),
        "partial_without_material_gaps": [
            row["research_id"] for row in research_rows
            if row["research_status"] == "PARTIAL" and not row["material_gaps"]
        ],
        "opportunity_ids": sorted(
            {str(row["opportunity_id"]) for row in outcome_rows if row.get("opportunity_id")}
        ),
        "direction_pairs": pairs,
        "r2_checks": r2_checks,
        "r2_acceptance": _verdict(r2_checks) if r2_checks else "NOT_APPLICABLE",
    }


_KIND_DIRECTION = {"NEW_LONG": "LONG", "NEW_SHORT": "SHORT"}


def _score_row(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata") or {}
    diagnostics = metadata.get("scoring_diagnostics") or {}
    direction = diagnostics.get("direction") if isinstance(diagnostics, dict) else None
    direction = direction if isinstance(direction, dict) else {}
    return {
        "opportunity_score_id": record.get("opportunity_score_id"),
        "ticker": record.get("ticker"),
        "hypothesis_kind": metadata.get("hypothesis_kind"),
        "direction": direction.get("direction"),
        "direction_source": direction.get("direction_source"),
        "directional_policy": direction.get("policy_version"),
        "company_frame_components": direction.get("company_frame_components"),
        "company_assessment_sources": ((direction.get("company_assessment") or {}).get("sources")),
        "company_assessment_fingerprint": (
            ((direction.get("company_assessment") or {}).get("assessment") or {}).get("fingerprint")
        ),
        "possible_direction_ignored": direction.get("possible_direction_ignored"),
        "scoring_status": record.get("scoring_status"),
        "raw_score": record.get("raw_score"),
        "confidence_adjusted_score": record.get("confidence_adjusted_score"),
        "thesis_score": record.get("thesis_score"),
        "catalyst_score": record.get("catalyst_score"),
        "fundamental_score": record.get("fundamental_score"),
        "technical_score": record.get("technical_score"),
        "expectations_score": record.get("expectations_score"),
        "volatility_listed_uncertain": any(
            "volatil" in str(item).lower() for item in record.get("uncertainty_factors", [])
        ),
    }


def _r2_checks(score_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """AI-8C.3-R2 acceptance: directional scores and non-contradictory pairs."""
    directional = [row for row in score_rows if row["hypothesis_kind"] in _KIND_DIRECTION]
    if not directional:
        return [], []
    by_ticker: dict[str, dict[str, dict[str, Any]]] = {}
    for row in directional:
        by_ticker.setdefault(str(row["ticker"]), {})[str(row["hypothesis_kind"])] = row
    pairs = []
    for ticker, rows in sorted(by_ticker.items()):
        long_row, short_row = rows.get("NEW_LONG"), rows.get("NEW_SHORT")
        if not long_row or not short_row:
            continue
        pairs.append({
            "ticker": ticker,
            "long_raw": long_row["raw_score"],
            "short_raw": short_row["raw_score"],
            "long_technical": long_row["technical_score"],
            "short_technical": short_row["technical_score"],
            "components_long": [long_row[f"{name}_score"] for name in ("thesis", "catalyst", "fundamental", "expectations")],
            "components_short": [short_row[f"{name}_score"] for name in ("thesis", "catalyst", "fundamental", "expectations")],
            "company_frame_long": long_row["company_frame_components"],
            "company_frame_short": short_row["company_frame_components"],
            "shared_assessment": (
                long_row["company_assessment_fingerprint"] is not None
                and long_row["company_assessment_fingerprint"] == short_row["company_assessment_fingerprint"]
            ),
            "sources_long": long_row["company_assessment_sources"],
            "sources_short": short_row["company_assessment_sources"],
        })

    def both_supported(pair: dict[str, Any]) -> bool:
        return (
            pair["long_raw"] is not None and pair["short_raw"] is not None
            and float(pair["long_raw"]) >= 60.0 and float(pair["short_raw"]) >= 60.0
        )

    from app.scanner.research_integration import ACCEPTED_DIRECTIONAL_SCORING_POLICIES

    mismatched = [
        row["opportunity_score_id"] for row in directional
        if row["direction_source"] != "HYPOTHESIS"
        or row["direction"] != _KIND_DIRECTION[row["hypothesis_kind"]]
    ]
    unaccepted = [
        row["opportunity_score_id"] for row in directional
        if row["directional_policy"] not in ACCEPTED_DIRECTIONAL_SCORING_POLICIES
    ]
    checks = [
        _check("every directional score records its hypothesis direction", not mismatched, mismatched),
        _check(
            "every directional score uses an accepted directional policy",
            not unaccepted,
            unaccepted,
        ),
        _check(
            "company-frame values identical within every pair sharing a company assessment",
            all(
                pair["company_frame_long"] == pair["company_frame_short"]
                for pair in pairs
                if pair["shared_assessment"]
                and pair["sources_long"] == pair["sources_short"]
            ),
            [pair["ticker"] for pair in pairs
             if pair["shared_assessment"] and pair["sources_long"] == pair["sources_short"]
             and pair["company_frame_long"] != pair["company_frame_short"]],
        ),
        _check(
            "no LONG/SHORT pair where both sides score raw >= 60",
            not any(both_supported(pair) for pair in pairs),
            [pair["ticker"] for pair in pairs if both_supported(pair)],
        ),
    ]
    return checks, pairs


def _count(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def run_inspect(args: argparse.Namespace) -> dict[str, Any]:
    database = Path(args.database)
    if not database.is_file():
        raise HarnessError("DATABASE_NOT_FOUND", str(database))
    result = inspect_research(database, args.since)
    result["step"] = "inspect"
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    result["checks"] = result["r1_checks"]
    result["verdict"] = result["r1_acceptance"]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--run-id")
    preflight.add_argument("--model", default="qwen3:8b")
    # The runtime LocalProvider is bound to http://localhost:11434.
    preflight.add_argument("--ollama-url", default="http://localhost:11434")
    preflight.add_argument("--yahoo-probe", default="SAP.DE")
    preflight.add_argument("--skip-yahoo", action="store_true")
    preflight.add_argument("--out", type=Path)

    replay = subparsers.add_parser("replay")
    replay.add_argument("--run-id")
    replay.add_argument("--workdir", type=Path, required=True)
    replay.add_argument("--out", type=Path)

    live = subparsers.add_parser("live")
    live.add_argument("--run-id")
    live.add_argument("--workdir", type=Path, required=True)
    live.add_argument("--mode", choices=("LIVE", "PREFER_CACHE", "CACHE_ONLY"), default="LIVE")
    live.add_argument("--model", default="qwen3:8b")
    live.add_argument("--max-waves", type=int, default=1)
    live.add_argument("--max-hypotheses", type=int, default=4)
    live.add_argument("--timeout-seconds", type=float, default=3600.0)
    live.add_argument("--log", type=Path)
    live.add_argument("--no-shadow-ledger", action="store_true",
                      help="do not export scored hypotheses to data/state/shadow_ledger.db")
    live.add_argument("--out", type=Path)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--database", type=Path, required=True)
    inspect.add_argument("--since", required=True, help="ISO timestamp, e.g. 2026-09-30T12:25:55Z")
    inspect.add_argument("--out", type=Path)

    args = parser.parse_args(argv)
    handlers = {
        "preflight": run_preflight,
        "replay": run_replay,
        "live": run_live,
        "inspect": run_inspect,
    }
    try:
        result = handlers[args.command](args)
    except HarnessError as exc:
        result = {"step": args.command, "verdict": "FAIL", "error": exc.code, "message": str(exc), "checks": []}
    _write(args.out, result)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
