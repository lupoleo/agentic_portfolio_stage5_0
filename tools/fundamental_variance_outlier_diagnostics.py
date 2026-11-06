from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


BEGIN = "=== AI-8C.3d.1 VARIANCE JSON ==="
END = "=== END AI-8C.3d.1 VARIANCE JSON ==="


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_variance_payload(text: str) -> dict | None:
    pattern = re.escape(BEGIN) + r"\s*(\{.*?\})\s*" + re.escape(END)
    match = re.search(pattern, text, flags=re.DOTALL)
    return json.loads(match.group(1)) if match else None


def _extract_component(text: str, component: str) -> dict:
    name = component.upper()
    pattern = (
        rf"  {name}\s+score=(?P<score>[^\n]+)\n"
        rf"\s+evidence=(?P<evidence>[^\n]+)\n"
        rf"\s+rationale=(?P<rationale>.*?)(?=\n  [A-Z]+\s+score=|\n=== SCORABILITY)"
    )
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return {}
    score_text = match.group("score").strip()
    return {
        "score": None if score_text == "NULL" else float(score_text),
        "evidence": match.group("evidence").strip(),
        "rationale": " ".join(match.group("rationale").split()),
    }


def build_report(artifact_dir: Path, threshold: float = 5.0) -> dict:
    variance = _load_json(artifact_dir / "semantic_variance.json")
    outliers = []

    for pair in variance.get("pairs", []):
        fundamental = pair.get("component_deltas", {}).get("fundamental", {})
        abs_delta = fundamental.get("abs_delta")
        if (
            pair.get("classification") != "TRUE_SEMANTIC_VARIANCE"
            or abs_delta is None
            or abs_delta <= threshold
        ):
            continue

        ticker = pair["ticker"]
        run_a = int(pair["run_a"])
        run_b = int(pair["run_b"])
        a_text = (artifact_dir / f"{ticker}_run{run_a}.stdout.txt").read_text(
            encoding="utf-8"
        )
        b_text = (artifact_dir / f"{ticker}_run{run_b}.stdout.txt").read_text(
            encoding="utf-8"
        )
        a_payload = _parse_variance_payload(a_text)
        b_payload = _parse_variance_payload(b_text)

        outliers.append(
            {
                "ticker": ticker,
                "run_a": run_a,
                "run_b": run_b,
                "fundamental_abs_delta": abs_delta,
                "fundamental_run_a": fundamental.get("run_a"),
                "fundamental_run_b": fundamental.get("run_b"),
                "input_equivalent": pair.get("input_equivalent"),
                "input_checks": pair.get("input_checks", {}),
                "run_a_component": _extract_component(a_text, "fundamental"),
                "run_b_component": _extract_component(b_text, "fundamental"),
                "selected_evidence_ids_equal": (
                    a_payload is not None
                    and b_payload is not None
                    and a_payload.get("selected_evidence_ids")
                    == b_payload.get("selected_evidence_ids")
                ),
                "semantic_dimensions_equal": (
                    a_payload is not None
                    and b_payload is not None
                    and a_payload.get("evidence_semantic_dimensions")
                    == b_payload.get("evidence_semantic_dimensions")
                ),
                "scorability_equal": (
                    a_payload is not None
                    and b_payload is not None
                    and a_payload.get("scorability")
                    == b_payload.get("scorability")
                ),
            }
        )

    return {
        "diagnostic_version": "ai-8c.3d.2b-fundamental-outlier-v1",
        "threshold": threshold,
        "outlier_count": len(outliers),
        "outliers": outliers,
    }


def _render(report: dict) -> str:
    lines = [
        "=" * 72,
        "AI-8C.3d.2b FUNDAMENTAL VARIANCE OUTLIER DIAGNOSTICS",
        "=" * 72,
        f"Threshold: > {report['threshold']}",
        f"Outliers: {report['outlier_count']}",
        "",
    ]
    for item in report["outliers"]:
        lines += [
            f"{item['ticker']} run {item['run_a']} vs run {item['run_b']}",
            f"  Fundamental: {item['fundamental_run_a']} -> "
            f"{item['fundamental_run_b']}  |delta|={item['fundamental_abs_delta']}",
            f"  Input equivalent: {item['input_equivalent']}",
            f"  Evidence IDs equal: {item['selected_evidence_ids_equal']}",
            f"  Semantic dimensions equal: {item['semantic_dimensions_equal']}",
            f"  Scorability equal: {item['scorability_equal']}",
            f"  Run A rationale: {item['run_a_component'].get('rationale')}",
            f"  Run B rationale: {item['run_b_component'].get('rationale')}",
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--threshold", type=float, default=5.0)
    args = parser.parse_args()

    report = build_report(args.artifact_dir, args.threshold)
    md = args.artifact_dir / "fundamental_variance_outliers.md"
    js = args.artifact_dir / "fundamental_variance_outliers.json"
    md.write_text(_render(report), encoding="utf-8")
    js.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(_render(report))
    print(f"Report: {md}")
    print(f"JSON: {js}")


if __name__ == "__main__":
    main()
