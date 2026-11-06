from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


DEFAULT_TICKERS = [
    "PATH",
    "NVDA",
    "TSLA",
    "AVGO",
    "HPE",
    "AI",
    "AMD",
    "META",
    "PLTR",
    "CRM",
    "MRVL",
    "TTWO",
]


@dataclass
class RunResult:
    ticker: str
    iteration: int
    returncode: int | None
    duration_seconds: float
    timed_out: bool
    status: str
    failure_class: str | None
    stdout_file: str
    stderr_file: str
    variance_diagnostics: dict[str, Any] | None = None


def classify_failure(stdout: str, stderr: str, timed_out: bool) -> str | None:
    text = f"{stdout}\n{stderr}"

    if timed_out:
        return "SUBPROCESS_TIMEOUT"
    if "done_reason='length'" in text or 'done_reason="length"' in text:
        return "OLLAMA_GENERATION_LENGTH"
    if "LocalProviderTimeoutError" in text:
        return "LOCAL_PROVIDER_TIMEOUT"
    if "invalid structured JSON" in text:
        return "INVALID_STRUCTURED_JSON"
    if "ResearchCoverageValidationError" in text:
        return "RESEARCH_COVERAGE_VALIDATION"
    if "UNKNOWN_CONTRADICTS_SUPPLIED_FACT" in text:
        return "UNKNOWN_CONTRADICTS_SUPPLIED_FACT"
    if "USEFUL_ANALYSIS_MARKED_INSUFFICIENT" in text:
        return "USEFUL_ANALYSIS_MARKED_INSUFFICIENT"
    if "ValidationError" in text:
        return "PYDANTIC_VALIDATION"
    if "supporting evidence" in text:
        return "SCORING_GROUNDING"
    if "cannot be produced without" in text:
        return "MISSING_CANONICAL_CONTEXT_SCORE"
    if "Traceback (most recent call last)" in text:
        return "UNCLASSIFIED_EXCEPTION"
    return None


_VARIANCE_RE = re.compile(
    r"=== AI-8C\.3d\.1 VARIANCE JSON ===\s*(\{.*?\})\s*"
    r"=== END AI-8C\.3d\.1 VARIANCE JSON ===",
    re.DOTALL,
)


def parse_variance_diagnostics(stdout: str) -> dict[str, Any] | None:
    match = _VARIANCE_RE.search(stdout)
    if match is None:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _same(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def compare_pair(a: RunResult, b: RunResult) -> dict[str, Any]:
    x, y = a.variance_diagnostics, b.variance_diagnostics
    out = {
        "ticker": a.ticker,
        "run_a": a.iteration,
        "run_b": b.iteration,
        "classification": "NOT_COMPARABLE",
        "input_equivalent": False,
        "input_checks": {},
        "component_deltas": {},
        "null_transitions": [],
        "raw_score_delta": None,
        "confidence_adjusted_delta": None,
    }
    if not x or not y:
        return out

    checks = {
        key: _same(x.get(key), y.get(key))
        for key in (
            "selected_evidence_ids",
            "required_context_fields",
            "final_missing_required_contexts",
            "evidence_semantic_dimensions",
            "scorability",
            "research_semantic_fingerprint",
        )
    }
    out["input_checks"] = checks
    out["input_equivalent"] = all(checks.values())

    research_a = x.get("research_semantic_input") or {}
    research_b = y.get("research_semantic_input") or {}
    research_fields = sorted(set(research_a) | set(research_b))
    out["research_semantic_changed_fields"] = [
        name
        for name in research_fields
        if not _same(research_a.get(name), research_b.get(name))
    ]

    changed = False
    for name in ("thesis", "catalyst", "fundamental", "technical", "expectations"):
        sx = (x.get("components", {}).get(name) or {}).get("score")
        sy = (y.get("components", {}).get(name) or {}).get("score")
        if (sx is None) != (sy is None):
            out["null_transitions"].append(name)
            delta = None
            changed = True
        elif sx is None:
            delta = None
        else:
            delta = round(float(sy) - float(sx), 6)
            changed = changed or delta != 0
        out["component_deltas"][name] = {
            "run_a": sx, "run_b": sy, "delta": delta,
            "abs_delta": None if delta is None else abs(delta),
        }

    if x.get("raw_score") is not None and y.get("raw_score") is not None:
        out["raw_score_delta"] = round(float(y["raw_score"]) - float(x["raw_score"]), 6)
    if x.get("confidence_adjusted_score") is not None and y.get("confidence_adjusted_score") is not None:
        out["confidence_adjusted_delta"] = round(
            float(y["confidence_adjusted_score"]) - float(x["confidence_adjusted_score"]), 6
        )

    if not out["input_equivalent"]:
        out["classification"] = "INPUT_VARIANCE"
    elif changed:
        out["classification"] = "TRUE_SEMANTIC_VARIANCE"
    else:
        out["classification"] = "STABLE"
    return out


def build_variance_report(results: list[RunResult]) -> dict[str, Any]:
    grouped: dict[str, list[RunResult]] = {}
    for r in results:
        if r.status == "PASS":
            grouped.setdefault(r.ticker, []).append(r)

    pairs = []
    for runs in grouped.values():
        runs = sorted(runs, key=lambda r: r.iteration)
        for i in range(0, len(runs) - 1, 2):
            pairs.append(compare_pair(runs[i], runs[i + 1]))

    components = {}
    for name in ("thesis", "catalyst", "fundamental", "technical", "expectations"):
        vals = [
            p["component_deltas"][name]["abs_delta"]
            for p in pairs
            if name in p["component_deltas"]
            and p["component_deltas"][name]["abs_delta"] is not None
        ]
        components[name] = {
            "mean_abs_delta": round(sum(vals) / len(vals), 6) if vals else None,
            "max_abs_delta": round(max(vals), 6) if vals else None,
            "null_transitions": sum(name in p["null_transitions"] for p in pairs),
        }

    raw = [abs(p["raw_score_delta"]) for p in pairs if p["raw_score_delta"] is not None]
    adj = [abs(p["confidence_adjusted_delta"]) for p in pairs if p["confidence_adjusted_delta"] is not None]
    return {
        "diagnostic_version": "ai-8c.3d.1-semantic-score-variance-v1",
        "pair_count": len(pairs),
        "classification_counts": {
            k: sum(p["classification"] == k for p in pairs)
            for k in ("STABLE", "TRUE_SEMANTIC_VARIANCE", "INPUT_VARIANCE", "NOT_COMPARABLE")
        },
        "components": components,
        "raw_score": {
            "mean_abs_delta": round(sum(raw) / len(raw), 6) if raw else None,
            "max_abs_delta": round(max(raw), 6) if raw else None,
        },
        "confidence_adjusted_score": {
            "mean_abs_delta": round(sum(adj) / len(adj), 6) if adj else None,
            "max_abs_delta": round(max(adj), 6) if adj else None,
        },
        "pairs": pairs,
    }


SEMANTIC_REPRODUCIBILITY_ACCEPTANCE_POLICY = {
    "policy_version": "ai-8c.3e-reproducibility-acceptance-v1",
    "require_all_runs_pass": True,
    "require_all_expected_pairs_comparable": True,
    "max_true_semantic_null_transitions": 0,
    "component_max_abs_delta": {
        "technical": 5.0,
        "fundamental": 5.0,
        "catalyst": 5.0,
        "thesis": 5.0,
    },
    "confidence_adjusted_max_abs_delta": 2.0,
}


def evaluate_semantic_reproducibility_acceptance(
    results: list[RunResult],
    variance_report: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic acceptance gate over live reproducibility diagnostics.

    INPUT_VARIANCE pairs are not treated as semantic failures. Component and
    final-score thresholds are evaluated only on TRUE_SEMANTIC_VARIANCE pairs.
    """
    policy = SEMANTIC_REPRODUCIBILITY_ACCEPTANCE_POLICY
    failures: list[str] = []
    run_failures = [r for r in results if r.status != "PASS"]

    if policy["require_all_runs_pass"] and run_failures:
        failures.append(f"live_run_failures={len(run_failures)}")

    expected_pairs = len(results) // 2
    comparable_pairs = sum(
        p["classification"] != "NOT_COMPARABLE"
        for p in variance_report["pairs"]
    )
    if (
        policy["require_all_expected_pairs_comparable"]
        and comparable_pairs != expected_pairs
    ):
        failures.append(
            f"comparable_pairs={comparable_pairs}/{expected_pairs}"
        )

    true_pairs = [
        p for p in variance_report["pairs"]
        if p["classification"] == "TRUE_SEMANTIC_VARIANCE"
    ]

    null_transitions = sum(
        len(p["null_transitions"]) for p in true_pairs
    )
    if null_transitions > policy["max_true_semantic_null_transitions"]:
        failures.append(
            f"true_semantic_null_transitions={null_transitions}"
        )

    observed_components: dict[str, Any] = {}
    for name, limit in policy["component_max_abs_delta"].items():
        values = [
            p["component_deltas"][name]["abs_delta"]
            for p in true_pairs
            if name in p["component_deltas"]
            and p["component_deltas"][name]["abs_delta"] is not None
        ]
        observed = max(values) if values else 0.0
        observed_components[name] = {
            "observed_max_abs_delta": observed,
            "limit": limit,
            "pass": observed <= limit,
        }
        if observed > limit:
            failures.append(
                f"{name}_max_abs_delta={observed}>{limit}"
            )

    adjusted_values = [
        abs(float(p["confidence_adjusted_delta"]))
        for p in true_pairs
        if p["confidence_adjusted_delta"] is not None
    ]
    adjusted_max = max(adjusted_values) if adjusted_values else 0.0
    adjusted_limit = policy["confidence_adjusted_max_abs_delta"]
    if adjusted_max > adjusted_limit:
        failures.append(
            f"confidence_adjusted_max_abs_delta="
            f"{adjusted_max}>{adjusted_limit}"
        )

    return {
        "policy_version": policy["policy_version"],
        "accepted": not failures,
        "failures": failures,
        "run_pass_count": len(results) - len(run_failures),
        "run_count": len(results),
        "expected_pair_count": expected_pairs,
        "comparable_pair_count": comparable_pairs,
        "true_semantic_pair_count": len(true_pairs),
        "input_variance_pair_count": sum(
            p["classification"] == "INPUT_VARIANCE"
            for p in variance_report["pairs"]
        ),
        "true_semantic_null_transitions": null_transitions,
        "components": observed_components,
        "confidence_adjusted_score": {
            "observed_max_abs_delta": adjusted_max,
            "limit": adjusted_limit,
            "pass": adjusted_max <= adjusted_limit,
        },
    }


def write_acceptance_report(
    acceptance: dict[str, Any],
    output_dir: Path,
) -> None:
    (output_dir / "semantic_acceptance.json").write_text(
        json.dumps(acceptance, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# AI-8C.3e Semantic Reproducibility Acceptance Gate",
        "",
        f"- Policy: {acceptance['policy_version']}",
        f"- ACCEPTED: {acceptance['accepted']}",
        f"- Live runs: {acceptance['run_pass_count']}/{acceptance['run_count']}",
        f"- Comparable pairs: "
        f"{acceptance['comparable_pair_count']}/"
        f"{acceptance['expected_pair_count']}",
        f"- TRUE_SEMANTIC_VARIANCE pairs: "
        f"{acceptance['true_semantic_pair_count']}",
        f"- INPUT_VARIANCE pairs: "
        f"{acceptance['input_variance_pair_count']}",
        f"- TRUE semantic NULL transitions: "
        f"{acceptance['true_semantic_null_transitions']}",
        "",
        "## Component gates",
        "",
        "| Component | Observed max | Limit | Pass |",
        "|---|---:|---:|---|",
    ]
    for name, item in acceptance["components"].items():
        lines.append(
            f"| {name.upper()} | {item['observed_max_abs_delta']} | "
            f"{item['limit']} | {item['pass']} |"
        )
    final = acceptance["confidence_adjusted_score"]
    lines += [
        "",
        "## Final-score gate",
        "",
        f"- Confidence-adjusted max |delta|: "
        f"{final['observed_max_abs_delta']} "
        f"(limit {final['limit']}) — PASS={final['pass']}",
        "",
        "## Failures",
        "",
    ]
    if acceptance["failures"]:
        lines.extend(f"- {item}" for item in acceptance["failures"])
    else:
        lines.append("- None")

    (output_dir / "semantic_acceptance.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def persist_variance_report(results: list[RunResult], output_dir: Path) -> None:
    report = build_variance_report(results)
    (output_dir / "semantic_variance.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    acceptance = evaluate_semantic_reproducibility_acceptance(
        results, report
    )
    write_acceptance_report(acceptance, output_dir)
    lines = [
        "# AI-8C.3d.1 Semantic Score Variance Diagnostics", "",
        f"- Pair count: {report['pair_count']}",
    ]
    for k, v in report["classification_counts"].items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Component variance", "",
              "| Component | Mean abs delta | Max abs delta | NULL transitions |",
              "|---|---:|---:|---:|"]
    for name, s in report["components"].items():
        lines.append(f"| {name.upper()} | {s['mean_abs_delta']} | {s['max_abs_delta']} | {s['null_transitions']} |")
    lines += ["", "## Aggregate score variance", "",
              f"- Raw score mean abs delta: {report['raw_score']['mean_abs_delta']}",
              f"- Raw score max abs delta: {report['raw_score']['max_abs_delta']}",
              f"- Confidence-adjusted mean abs delta: {report['confidence_adjusted_score']['mean_abs_delta']}",
              f"- Confidence-adjusted max abs delta: {report['confidence_adjusted_score']['max_abs_delta']}",
              "", "## Pair details", "",
              "| Ticker | Class | Input equivalent | THESIS | CATALYST | FUNDAMENTAL | TECHNICAL | EXPECTATIONS | Raw d | Adjusted d |",
              "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for p in report["pairs"]:
        d = lambda n: p["component_deltas"].get(n, {}).get("delta")
        lines.append(
            f"| {p['ticker']} | {p['classification']} | {p['input_equivalent']} | "
            f"{d('thesis')} | {d('catalyst')} | {d('fundamental')} | "
            f"{d('technical')} | {d('expectations')} | {p['raw_score_delta']} | "
            f"{p['confidence_adjusted_delta']} |"
        )
    (output_dir / "semantic_variance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_markdown(results: list[RunResult], output_dir: Path) -> None:
    total = len(results)
    passed = sum(r.status == "PASS" for r in results)
    failed = total - passed

    by_failure: dict[str, int] = {}
    for result in results:
        if result.failure_class:
            by_failure[result.failure_class] = (
                by_failure.get(result.failure_class, 0) + 1
            )

    lines = [
        "# AI-8C.2 Batch Validation Report",
        "",
        f"- Total runs: {total}",
        f"- PASS: {passed}",
        f"- FAIL: {failed}",
        f"- Pass rate: {(passed / total * 100.0) if total else 0.0:.1f}%",
        "",
        "## Failure classes",
        "",
    ]

    if by_failure:
        for name, count in sorted(by_failure.items()):
            lines.append(f"- {name}: {count}")
    else:
        lines.append("- None")

    lines += [
        "",
        "## Run matrix",
        "",
        "| Ticker | Run | Status | Failure | Seconds |",
        "|---|---:|---|---|---:|",
    ]

    for r in results:
        lines.append(
            f"| {r.ticker} | {r.iteration} | {r.status} | "
            f"{r.failure_class or ''} | {r.duration_seconds:.1f} |"
        )

    (output_dir / "summary.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def parse_tickers(values: Iterable[str] | None) -> list[str]:
    if not values:
        return list(DEFAULT_TICKERS)

    tickers: list[str] = []
    for value in values:
        for token in value.replace(",", " ").split():
            ticker = token.strip().upper()
            if ticker and ticker not in tickers:
                tickers.append(ticker)
    return tickers


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run AI-8C.2 live opportunity scoring across a ticker matrix, "
            "continue after crashes, and collect diagnostics."
        )
    )
    parser.add_argument(
        "--tickers",
        nargs="*",
        help="Ticker list (space- or comma-separated). Defaults to validation set.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=2,
        help="Runs per ticker (default: 2).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=900,
        help="Hard subprocess timeout per live run (default: 900).",
    )
    parser.add_argument(
        "--max-news",
        type=int,
        default=None,
        help="Optional value forwarded to live_opportunity_scoring_8c2.",
    )
    args = parser.parse_args()

    if args.repeats <= 0:
        raise SystemExit("--repeats must be > 0")
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be > 0")

    tickers = parse_tickers(args.tickers)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path("artifacts") / f"ai_8c2_validation_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "created_at_local": datetime.now().isoformat(),
        "python": sys.executable,
        "tickers": tickers,
        "repeats": args.repeats,
        "timeout_seconds": args.timeout_seconds,
        "max_news": args.max_news,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    results: list[RunResult] = []

    print("=" * 72)
    print("AI-8C.2 BATCH LIVE VALIDATION")
    print("=" * 72)
    print("Tickers:", ", ".join(tickers))
    print("Repeats:", args.repeats)
    print("Output:", output_dir)
    print()

    for ticker in tickers:
        for iteration in range(1, args.repeats + 1):
            cmd = [
                sys.executable,
                "-m",
                "tools.live_opportunity_scoring_8c2",
                "--ticker",
                ticker,
            ]
            if args.max_news is not None:
                cmd += ["--max-news", str(args.max_news)]

            stem = f"{ticker}_run{iteration}"
            stdout_path = output_dir / f"{stem}.stdout.txt"
            stderr_path = output_dir / f"{stem}.stderr.txt"

            print(f"[{ticker} run {iteration}] START")
            started = time.perf_counter()
            timed_out = False
            returncode: int | None = None

            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=args.timeout_seconds,
                )
                stdout = completed.stdout
                stderr = completed.stderr
                returncode = completed.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = exc.stdout or ""
                stderr = exc.stderr or ""
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", errors="replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                stderr += (
                    f"\nBATCH HARNESS: subprocess exceeded "
                    f"{args.timeout_seconds}s and was terminated.\n"
                )

            duration = time.perf_counter() - started
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr, encoding="utf-8")

            failure = classify_failure(stdout, stderr, timed_out)
            status = (
                "PASS"
                if not timed_out and returncode == 0
                else "FAIL"
            )

            result = RunResult(
                ticker=ticker,
                iteration=iteration,
                returncode=returncode,
                duration_seconds=round(duration, 3),
                timed_out=timed_out,
                status=status,
                failure_class=failure,
                stdout_file=str(stdout_path),
                stderr_file=str(stderr_path),
                variance_diagnostics=parse_variance_diagnostics(stdout),
            )
            results.append(result)

            print(
                f"[{ticker} run {iteration}] {status} "
                f"({duration:.1f}s)"
                + (f" -> {failure}" if failure else "")
            )

            # Persist after every run so partial matrices survive interruption.
            (output_dir / "summary.json").write_text(
                json.dumps(
                    [asdict(item) for item in results],
                    indent=2,
                ),
                encoding="utf-8",
            )
            write_markdown(results, output_dir)
            persist_variance_report(results, output_dir)

    print()
    print("=" * 72)
    print("COMPLETE")
    print("=" * 72)
    print("Report:", output_dir / "summary.md")
    print("Raw results:", output_dir / "summary.json")
    print("Semantic variance:", output_dir / "semantic_variance.md")
    print("Semantic variance JSON:", output_dir / "semantic_variance.json")
    print("Semantic acceptance:", output_dir / "semantic_acceptance.md")
    print("Semantic acceptance JSON:", output_dir / "semantic_acceptance.json")
    print("Per-run stdout/stderr are in the same directory.")


if __name__ == "__main__":
    main()
