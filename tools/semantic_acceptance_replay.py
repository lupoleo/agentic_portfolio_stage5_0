from __future__ import annotations

import argparse
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from tools.batch_validate_opportunity_scoring_8c2 import (
    RunResult,
    evaluate_semantic_reproducibility_acceptance,
)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Required artifact not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON artifact: {path}: {exc}") from exc


def _load_run_results(path: Path) -> list[RunResult]:
    raw = _load_json(path)
    if not isinstance(raw, list):
        raise SystemExit(f"summary.json must contain a list: {path}")

    allowed = {field.name for field in fields(RunResult)}
    results: list[RunResult] = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise SystemExit(
                f"summary.json row {index} must be an object"
            )
        payload = {key: value for key, value in row.items() if key in allowed}
        try:
            results.append(RunResult(**payload))
        except TypeError as exc:
            raise SystemExit(
                f"summary.json row {index} is incompatible with RunResult: {exc}"
            ) from exc
    return results


def _validate_variance_report(report: Any, path: Path) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise SystemExit(f"semantic_variance.json must contain an object: {path}")
    if not isinstance(report.get("pairs"), list):
        raise SystemExit(
            f"semantic_variance.json missing pairs list: {path}"
        )
    return report


def replay_acceptance(artifact_dir: Path) -> dict[str, Any]:
    artifact_dir = artifact_dir.resolve()
    if not artifact_dir.exists() or not artifact_dir.is_dir():
        raise SystemExit(f"Artifact directory not found: {artifact_dir}")

    summary_path = artifact_dir / "summary.json"
    variance_path = artifact_dir / "semantic_variance.json"

    results = _load_run_results(summary_path)
    variance_report = _validate_variance_report(
        _load_json(variance_path),
        variance_path,
    )

    acceptance = evaluate_semantic_reproducibility_acceptance(
        results,
        variance_report,
    )
    acceptance["replay"] = {
        "artifact_dir": str(artifact_dir),
        "summary_file": str(summary_path),
        "semantic_variance_file": str(variance_path),
        "live_inference_performed": False,
    }
    return acceptance


def _write_markdown(acceptance: dict[str, Any], path: Path) -> None:
    lines = [
        "# AI-8C.3e.1 Historical Semantic Acceptance Replay",
        "",
        f"- Policy: {acceptance['policy_version']}",
        f"- ACCEPTED: {acceptance['accepted']}",
        "- Live inference performed: False",
        f"- Live runs in artifact: "
        f"{acceptance['run_pass_count']}/{acceptance['run_count']}",
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

    lines += [
        "",
        "## Replay provenance",
        "",
        f"- Artifact directory: {acceptance['replay']['artifact_dir']}",
        f"- Summary: {acceptance['replay']['summary_file']}",
        f"- Semantic variance: "
        f"{acceptance['replay']['semantic_variance_file']}",
    ]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the AI-8C.3e semantic reproducibility acceptance gate "
            "against an existing batch-validation artifact directory. "
            "No live inference or market retrieval is performed."
        )
    )
    parser.add_argument(
        "artifact_dir",
        type=Path,
        help=(
            "Path containing summary.json and semantic_variance.json, e.g. "
            "artifacts/ai_8c2_validation_20260907-205202"
        ),
    )
    args = parser.parse_args()

    acceptance = replay_acceptance(args.artifact_dir)

    json_path = args.artifact_dir / "semantic_acceptance_replay.json"
    md_path = args.artifact_dir / "semantic_acceptance_replay.md"

    json_path.write_text(
        json.dumps(acceptance, indent=2),
        encoding="utf-8",
    )
    _write_markdown(acceptance, md_path)

    print("=" * 72)
    print("AI-8C.3e.1 HISTORICAL ACCEPTANCE REPLAY")
    print("=" * 72)
    print("Artifact:", args.artifact_dir)
    print("ACCEPTED:", acceptance["accepted"])
    print("Policy:", acceptance["policy_version"])
    print(
        "Runs:",
        f"{acceptance['run_pass_count']}/{acceptance['run_count']}",
    )
    print(
        "Comparable pairs:",
        f"{acceptance['comparable_pair_count']}/"
        f"{acceptance['expected_pair_count']}",
    )
    print("Failures:", acceptance["failures"] or "None")
    print("Report:", md_path)
    print("JSON:", json_path)


if __name__ == "__main__":
    main()
