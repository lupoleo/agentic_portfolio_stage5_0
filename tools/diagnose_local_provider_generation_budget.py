from __future__ import annotations

import inspect
import json
import re

from app.ai.local_provider import LocalProvider
from app.ai.models import AIRequest


KEYS = (
    "num_predict", "num_ctx", "options", "think", "timeout",
    "reasoning_mode", "temperature", "top_p", "seed", "format",
)


def _interesting_lines(source: str) -> list[str]:
    lines = source.splitlines()
    hits = []
    for i, line in enumerate(lines, start=1):
        low = line.lower()
        if any(key in low for key in KEYS):
            hits.append(f"{i:4d}: {line}")
    return hits


def _literal_payload_keys(source: str) -> list[str]:
    # Conservative diagnostic only: find JSON/dict-like quoted keys in source.
    keys = re.findall(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']\s*:', source)
    return sorted(set(keys))


def main() -> None:
    print("=" * 72)
    print("LOCAL PROVIDER GENERATION-BUDGET DIAGNOSTIC")
    print("=" * 72)

    provider_file = inspect.getsourcefile(LocalProvider)
    print(f"LocalProvider file:      {provider_file}")
    print(f"LocalProvider signature: {inspect.signature(LocalProvider)}")
    print(f"AIRequest signature:     {inspect.signature(AIRequest)}")

    source = inspect.getsource(LocalProvider)
    print("\n--- Relevant LocalProvider source lines ---")
    hits = _interesting_lines(source)
    print("\n".join(hits) if hits else "NONE")

    payload_keys = _literal_payload_keys(source)
    print("\n--- Literal dict/payload keys found in LocalProvider ---")
    print(", ".join(payload_keys) if payload_keys else "NONE")

    has_num_predict = bool(re.search(r"\bnum_predict\b", source))
    has_num_ctx = bool(re.search(r"\bnum_ctx\b", source))
    has_options = bool(re.search(r'["\']options["\']', source))
    has_timeout = bool(re.search(r"\btimeout\b", source, flags=re.I))
    has_think = bool(re.search(r"\bthink\b", source, flags=re.I))

    print("\n--- Static conclusions ---")
    print(f"Explicit num_predict in LocalProvider source: {has_num_predict}")
    print(f"Explicit num_ctx in LocalProvider source:     {has_num_ctx}")
    print(f"Explicit Ollama options payload:              {has_options}")
    print(f"Timeout handling present:                     {has_timeout}")
    print(f"Think/reasoning handling present:             {has_think}")

    request_fields = getattr(AIRequest, "model_fields", {})
    print("\n--- AIRequest fields ---")
    for name, field in request_fields.items():
        default = field.default
        print(f"{name}: annotation={field.annotation!r}, default={default!r}")

    budgetish = [
        name for name in request_fields
        if any(token in name.lower() for token in ("token", "predict", "context", "budget", "max_output"))
    ]
    print("\nAIRequest generation-budget-like fields:",
          ", ".join(budgetish) if budgetish else "NONE")

    print("\n--- Diagnostic verdict ---")
    if not has_num_predict and not budgetish:
        print("NO EXPLICIT OUTPUT TOKEN CEILING DETECTED in LocalProvider or AIRequest.")
        print("This supports the hypothesis that REASONING generation can run pathologically long.")
    elif has_num_predict:
        print("An explicit num_predict reference exists. Inspect the source lines above to determine")
        print("whether it is fixed, request-specific, or actually sent to Ollama.")
    else:
        print("A request-level budget-like field exists; inspect whether LocalProvider maps it")
        print("to Ollama num_predict/options.")

    print("\nNo Ollama inference was executed by this diagnostic.")


if __name__ == "__main__":
    main()
