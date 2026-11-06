"""Builds the AI provider used by the Stage 5.0 runtimes.

Claude is the default (architecture §5.4). A ``claude-*`` model name selects
``ClaudeProvider`` with that model as the research model; the fast tasks keep
the routing's fast model. ``LocalProvider`` (Ollama) stays reachable only by an
explicit ``ollama:<model>`` name, for offline comparison runs.
"""

from __future__ import annotations

from .claude_provider import ClaudeModelRouting, ClaudeProvider
from .provider import AIModelProvider

DEFAULT_MODEL = "claude-sonnet-5-5"
OLLAMA_PREFIX = "ollama:"


def build_provider(model_name: str = DEFAULT_MODEL, *, timeout_seconds: float = 600.0) -> AIModelProvider:
    name = model_name.strip()
    if name.startswith("claude-"):
        return ClaudeProvider(ClaudeModelRouting(research_model=name), timeout_seconds=timeout_seconds)
    if name.startswith(OLLAMA_PREFIX) and len(name) > len(OLLAMA_PREFIX):
        from .local_provider import LocalProvider

        return LocalProvider(model_name=name[len(OLLAMA_PREFIX):], timeout_seconds=timeout_seconds)
    raise ValueError(
        f"unknown model {model_name!r}: use a claude-* model, or ollama:<model> for a local comparison run"
    )
