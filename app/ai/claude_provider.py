"""Claude provider for the provider-neutral ``AIModelProvider`` interface.

Stage 5.0 replaces the local Ollama model with Claude (architecture §5.4)
without changing any research or scoring contract: callers keep building the
same ``AIRequest`` and receive the same ``AIResponse``.

Behaviour
---------
* Model routing by task: fast tasks (relevance, triage, classification,
  sentiment, extraction) go to the fast model, research and reasoning to the
  research model. The model that actually answered is recorded per response.
* ``ReasoningMode`` maps to Claude's effort level; thinking stays adaptive.
* JSON requests with an ``output_schema`` use structured outputs
  (``messages.parse``), so the response is schema-valid by construction and is
  validated again with the same Pydantic model as in 4.0.
* Refusals and truncated outputs raise typed errors; nothing is invented.
* Only ``DataSensitivity.PUBLIC`` requests are sent to the cloud (decision G8):
  portfolio or account data must stay in deterministic local code.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
from pydantic import ValidationError

from .models import AIRequest, AIResponse, AITask, DataSensitivity, ReasoningMode, ResponseFormat
from .provider import AIModelProvider

PROVIDER_NAME = "ANTHROPIC_CLAUDE"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

FAST_TASKS = frozenset({AITask.RELEVANCE, AITask.TRIAGE, AITask.CLASSIFICATION, AITask.SENTIMENT, AITask.EXTRACTION})

# Models whose refusals can be rescued by the server-side fallback on the Claude API.
MODELS_WITH_SERVER_FALLBACK = frozenset({"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"})


class ClaudeProviderError(RuntimeError):
    """Base error raised by ClaudeProvider."""


class ClaudeProviderPolicyError(ClaudeProviderError):
    """The request may not leave the machine (non-public data)."""


class ClaudeProviderConnectionError(ClaudeProviderError):
    """The Claude API could not be reached."""


class ClaudeProviderTimeoutError(ClaudeProviderError):
    """The Claude API did not answer in time."""


class ClaudeProviderResponseError(ClaudeProviderError):
    """The Claude API returned an error or an unusable response."""


class ClaudeProviderRefusalError(ClaudeProviderResponseError):
    """Claude declined the request (``stop_reason == "refusal"``)."""


class ClaudeProviderValidationError(ClaudeProviderResponseError):
    """Structured output did not satisfy the requested schema."""


@dataclass(frozen=True)
class ClaudeModelRouting:
    """Which model serves which task, and how hard it thinks."""

    research_model: str = "claude-sonnet-5-5"
    fast_model: str = "claude-haiku-5-5"
    fast_effort: str = "low"
    reasoning_effort: str = "high"
    max_tokens_fast: int = 4_096
    max_tokens_reasoning: int = 16_000
    task_overrides: dict[AITask, str] = field(default_factory=dict)

    def model_for(self, request: AIRequest) -> str:
        if request.task in self.task_overrides:
            return self.task_overrides[request.task]
        return self.fast_model if request.task in FAST_TASKS else self.research_model

    def effort_for(self, request: AIRequest) -> str:
        return self.reasoning_effort if request.reasoning_mode is ReasoningMode.REASONING else self.fast_effort

    def max_tokens_for(self, request: AIRequest) -> int:
        if request.reasoning_mode is ReasoningMode.REASONING or request.response_format is ResponseFormat.JSON:
            return self.max_tokens_reasoning
        return self.max_tokens_fast


class ClaudeProvider(AIModelProvider):
    def __init__(
        self,
        routing: ClaudeModelRouting | None = None,
        *,
        client: Any | None = None,
        timeout_seconds: float = 600.0,
        max_retries: int = 2,
        server_fallback: bool = True,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._routing = routing or ClaudeModelRouting()
        # The client resolves credentials itself (ANTHROPIC_API_KEY or an `ant auth login` profile).
        self._client = client or anthropic.Anthropic(timeout=timeout_seconds, max_retries=max_retries)
        self._server_fallback = server_fallback

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAME

    @property
    def model_name(self) -> str:
        return self._routing.research_model

    @property
    def routing(self) -> ClaudeModelRouting:
        return self._routing

    def infer(self, request: AIRequest) -> AIResponse:
        if request.sensitivity is not DataSensitivity.PUBLIC:
            raise ClaudeProviderPolicyError(
                f"{request.sensitivity.value} data is not sent to the cloud model; only PUBLIC evidence is allowed"
            )

        model = self._routing.model_for(request)
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": self._routing.max_tokens_for(request),
            "output_config": {"effort": self._routing.effort_for(request)},
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if self._server_fallback and model in MODELS_WITH_SERVER_FALLBACK:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        else:
            params["betas"] = []

        structured = request.response_format is ResponseFormat.JSON and request.output_schema is not None
        if request.response_format is ResponseFormat.JSON and not structured:
            params["messages"][0]["content"] = request.prompt + "\n\nReturn only one JSON object, with no other text."

        started = time.perf_counter()
        try:
            if structured:
                response = self._client.beta.messages.parse(output_format=request.output_schema, **params)
            else:
                response = self._client.beta.messages.create(**params)
        except ValidationError as exc:
            raise ClaudeProviderValidationError(
                f"Claude structured output failed {request.output_schema.__name__} validation: {exc}"
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise ClaudeProviderTimeoutError("Timed out while waiting for Claude") from exc
        except anthropic.APIConnectionError as exc:
            raise ClaudeProviderConnectionError(f"Claude API unreachable: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ClaudeProviderResponseError(f"Claude API returned HTTP {exc.status_code}: {exc.message}") from exc
        latency_ms = (time.perf_counter() - started) * 1000.0

        return self._normalize(request, response, latency_ms)

    def _normalize(self, request: AIRequest, response: Any, latency_ms: float) -> AIResponse:
        served_by = getattr(response, "model", None) or self._routing.model_for(request)
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            raise ClaudeProviderRefusalError(f"Claude ({served_by}) declined the request; category={category}")
        if stop_reason == "max_tokens":
            raise ClaudeProviderResponseError(
                f"Claude ({served_by}) output was truncated at max_tokens={self._routing.max_tokens_for(request)}"
            )

        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        usage = _usage(response)

        if request.response_format is ResponseFormat.TEXT:
            if not text.strip():
                raise ClaudeProviderResponseError("Claude returned an empty text response")
            return AIResponse(provider=PROVIDER_NAME, model=served_by, content=text, latency_ms=latency_ms, usage=usage)

        if request.output_schema is not None:
            parsed = getattr(response, "parsed_output", None)
            if parsed is None:
                raise ClaudeProviderResponseError("Claude returned no structured output")
            try:
                validated = request.output_schema.model_validate(
                    parsed.model_dump(mode="json") if hasattr(parsed, "model_dump") else parsed
                )
            except ValidationError as exc:
                raise ClaudeProviderValidationError(
                    f"Claude structured output failed {request.output_schema.__name__} validation: {exc}"
                ) from exc
            structured = validated.model_dump(mode="json")
        else:
            try:
                structured = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ClaudeProviderResponseError(f"Claude returned invalid JSON ({len(text)} chars)") from exc
            if not isinstance(structured, dict):
                raise ClaudeProviderResponseError("Structured Claude output must be a JSON object")

        return AIResponse(
            provider=PROVIDER_NAME,
            model=served_by,
            content=text or json.dumps(structured),
            structured_output=structured,
            latency_ms=latency_ms,
            usage=usage,
        )


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    out: dict[str, int] = {}
    if usage is None:
        return out
    for source, target in (
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
        ("cache_read_input_tokens", "cache_read_tokens"),
        ("cache_creation_input_tokens", "cache_write_tokens"),
    ):
        value = getattr(usage, source, None)
        if isinstance(value, int) and not isinstance(value, bool):
            out[target] = value
    return out
