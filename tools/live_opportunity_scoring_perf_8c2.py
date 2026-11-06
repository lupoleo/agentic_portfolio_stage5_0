from __future__ import annotations

import argparse
import json
import time
from typing import Any

import tools.live_opportunity_scoring_8c2 as live
from app.ai.local_provider import LocalProvider as BaseLocalProvider


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _schema_chars(output_schema: Any) -> int:
    if output_schema is None:
        return 0
    try:
        schema = output_schema.model_json_schema()
        return len(json.dumps(schema, separators=(",", ":"), ensure_ascii=False))
    except Exception:
        return 0


def _estimate_tokens(chars: int) -> int:
    # Diagnostic approximation only; intentionally not presented as an exact
    # tokenizer count. Useful for comparing requests against a 4096 context.
    return max(1, round(chars / 4))


class InstrumentedLocalProvider(BaseLocalProvider):
    call_number = 0

    def infer(self, request):
        type(self).call_number += 1
        n = type(self).call_number

        metadata = dict(getattr(request, "metadata", {}) or {})
        prompt = getattr(request, "prompt", "") or ""
        prompt_chars = len(prompt)
        schema_chars = _schema_chars(getattr(request, "output_schema", None))
        combined_chars = prompt_chars + schema_chars

        repair = bool(metadata.get("repair"))
        task = _enum_value(getattr(request, "task", "UNKNOWN"))
        reasoning = _enum_value(getattr(request, "reasoning_mode", "UNKNOWN"))
        evidence_ids = metadata.get("evidence_ids") or []

        if repair:
            phase = "RESEARCH_REPAIR"
        elif "SCOR" in task.upper():
            phase = "OPPORTUNITY_SCORING"
        elif "RESEARCH" in task.upper():
            phase = "INITIAL_RESEARCH"
        else:
            phase = f"{task}"

        print("\n" + "=" * 72, flush=True)
        print(f"AI PERF CALL #{n}: {phase}", flush=True)
        print(f"task:                  {task}", flush=True)
        print(f"reasoning_mode:        {reasoning}", flush=True)
        print(f"repair:                {repair}", flush=True)
        print(f"evidence_id_count:     {len(evidence_ids)}", flush=True)
        print(f"prompt_chars:          {prompt_chars}", flush=True)
        print(f"schema_chars:          {schema_chars}", flush=True)
        print(f"combined_chars:        {combined_chars}", flush=True)
        print(
            f"approx_input_tokens*:  {_estimate_tokens(combined_chars)}",
            flush=True,
        )
        print(
            "* chars/4 diagnostic estimate, NOT an exact Qwen tokenizer count",
            flush=True,
        )
        print(f"START:                 {time.strftime('%H:%M:%S')}", flush=True)

        started = time.perf_counter()
        try:
            response = super().infer(request)
        except Exception as exc:
            elapsed = time.perf_counter() - started
            print(f"END ERROR:             {time.strftime('%H:%M:%S')}", flush=True)
            print(f"elapsed_seconds:       {elapsed:.2f}", flush=True)
            print(f"exception_type:        {type(exc).__name__}", flush=True)
            print("=" * 72 + "\n", flush=True)
            raise

        elapsed = time.perf_counter() - started
        structured = getattr(response, "structured_output", None)
        try:
            output_chars = len(
                json.dumps(structured, ensure_ascii=False, default=str)
            ) if structured is not None else 0
        except Exception:
            output_chars = 0

        print(f"END OK:                {time.strftime('%H:%M:%S')}", flush=True)
        print(f"elapsed_seconds:       {elapsed:.2f}", flush=True)
        print(f"structured_output_chars:{output_chars}", flush=True)
        print("=" * 72 + "\n", flush=True)
        return response


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "AI-8C.2 performance instrumentation wrapper around the existing "
            "live_opportunity_scoring_8c2 pipeline."
        )
    )
    parser.add_argument("--ticker", default="PATH")
    parser.add_argument("--max-news", type=int, default=None)
    args, unknown = parser.parse_known_args()

    # The existing live tool owns the validated pipeline. We only replace the
    # LocalProvider symbol it instantiates, leaving production code untouched.
    live.LocalProvider = InstrumentedLocalProvider

    forwarded = ["live_opportunity_scoring_8c2", "--ticker", args.ticker]
    if args.max_news is not None:
        forwarded += ["--max-news", str(args.max_news)]
    forwarded += unknown

    import sys
    old_argv = sys.argv
    try:
        sys.argv = forwarded
        live.main()
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    main()
