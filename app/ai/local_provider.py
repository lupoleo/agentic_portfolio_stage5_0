from __future__ import annotations

import json
import socket
import time
from collections.abc import Callable
from typing import Any
from urllib import error, request

from pydantic import ValidationError

from .models import (
    AIRequest,
    AIResponse,
    AITask,
    ReasoningMode,
    ResponseFormat,
)
from .provider import AIModelProvider


class LocalProviderError(RuntimeError):
    """Base error raised by LocalProvider."""


class LocalProviderConnectionError(LocalProviderError):
    """Raised when the local Ollama server cannot be reached."""


class LocalProviderTimeoutError(LocalProviderError):
    """Raised when the local Ollama request times out."""


class LocalProviderResponseError(LocalProviderError):
    """Raised when Ollama returns an invalid or unusable response."""


class LocalProviderValidationError(LocalProviderResponseError):
    """Raised when structured output violates the requested schema."""


Transport = Callable[[str, bytes, float], dict[str, Any]]


def _urllib_transport(
    url: str,
    body: bytes,
    timeout_seconds: float,
) -> dict[str, Any]:
    http_request = request.Request(
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(
            http_request,
            timeout=timeout_seconds,
        ) as response:
            raw = response.read().decode("utf-8")

    except error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""

        message = f"Ollama returned HTTP {exc.code}"
        if detail:
            message += f": {detail}"
        raise LocalProviderResponseError(message) from exc

    except (TimeoutError, socket.timeout) as exc:
        raise LocalProviderTimeoutError(
            "Timed out while waiting for Ollama"
        ) from exc

    except (error.URLError, ConnectionError, OSError) as exc:
        reason = getattr(exc, "reason", exc)

        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise LocalProviderTimeoutError(
                "Timed out while waiting for Ollama"
            ) from exc

        raise LocalProviderConnectionError(
            f"Unable to reach Ollama: {reason}"
        ) from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalProviderResponseError(
            "Ollama returned invalid JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise LocalProviderResponseError(
            "Ollama response must be a JSON object"
        )

    return payload


class LocalProvider(AIModelProvider):
    """Local AI provider backed by the Ollama HTTP API."""

    def __init__(
        self,
        model_name: str = "qwen3:8b",
        base_url: str = "http://localhost:11434",
        timeout_seconds: float = 60.0,
        fast_num_predict: int = 512,
        reasoning_num_predict: int = 2048,
        structured_reasoning_num_predict: int = 4096,
        research_reasoning_num_predict: int = 12288,
        transport: Transport | None = None,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name must not be blank")
        if not base_url.strip():
            raise ValueError("base_url must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if fast_num_predict <= 0:
            raise ValueError("fast_num_predict must be greater than zero")
        if reasoning_num_predict <= 0:
            raise ValueError(
                "reasoning_num_predict must be greater than zero"
            )
        if structured_reasoning_num_predict <= 0:
            raise ValueError(
                "structured_reasoning_num_predict must be greater than zero"
            )
        if research_reasoning_num_predict <= 0:
            raise ValueError(
                "research_reasoning_num_predict must be greater than zero"
            )

        self._model_name = model_name.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = float(timeout_seconds)
        self._fast_num_predict = int(fast_num_predict)
        self._reasoning_num_predict = int(reasoning_num_predict)
        self._structured_reasoning_num_predict = int(
            structured_reasoning_num_predict
        )
        self._research_reasoning_num_predict = int(
            research_reasoning_num_predict
        )
        self._transport = transport or _urllib_transport

    @property
    def provider_name(self) -> str:
        return "LOCAL_OLLAMA"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def fast_num_predict(self) -> int:
        return self._fast_num_predict

    @property
    def reasoning_num_predict(self) -> int:
        return self._reasoning_num_predict

    @property
    def structured_reasoning_num_predict(self) -> int:
        return self._structured_reasoning_num_predict

    @property
    def research_reasoning_num_predict(self) -> int:
        return self._research_reasoning_num_predict

    def infer(self, ai_request: AIRequest) -> AIResponse:
        if (
            ai_request.task is AITask.RESEARCH
            and ai_request.reasoning_mode is ReasoningMode.REASONING
            and ai_request.response_format is ResponseFormat.JSON
        ):
            num_predict = self.research_reasoning_num_predict
        elif (
            ai_request.reasoning_mode is ReasoningMode.REASONING
            and ai_request.response_format is ResponseFormat.JSON
        ):
            num_predict = self.structured_reasoning_num_predict
        elif ai_request.reasoning_mode is ReasoningMode.REASONING:
            num_predict = self.reasoning_num_predict
        else:
            num_predict = self.fast_num_predict

        payload: dict[str, Any] = {
            "model": self.model_name,
            "prompt": ai_request.prompt,
            "stream": False,
            "think": (
                ai_request.reasoning_mode
                is ReasoningMode.REASONING
            ),
            "options": {
                "num_predict": num_predict,
            },
        }

        if ai_request.response_format is ResponseFormat.JSON:
            if ai_request.output_schema is not None:
                payload["format"] = (
                    ai_request.output_schema.model_json_schema()
                )
            else:
                payload["format"] = "json"

        body = json.dumps(payload).encode("utf-8")
        url = f"{self.base_url}/api/generate"

        started = time.perf_counter()
        response_payload = self._transport(
            url,
            body,
            self.timeout_seconds,
        )
        transport_retries = 0

        if self._should_retry_incomplete_structured_response(
            response_payload,
            ai_request,
        ):
            retry_payload = dict(payload)
            retry_payload["prompt"] = (
                ai_request.prompt
                + "\n\nCRITICAL COMPACT RETRY: The previous structured "
                "generation was incomplete. Return ONLY the requested JSON. "
                "Be extremely concise: no repeated evidence, no article "
                "summaries, context fields at most 3 short sentences, "
                "bull/bear cases at most 2 short sentences, and list fields "
                "at most 5 short items."
            )
            retry_payload["think"] = False
            retry_options = dict(retry_payload.get("options", {}))
            retry_options["num_predict"] = min(num_predict, 6144)
            retry_payload["options"] = retry_options

            retry_body = json.dumps(retry_payload).encode("utf-8")
            response_payload = self._transport(
                url,
                retry_body,
                self.timeout_seconds,
            )
            transport_retries = 1

        latency_ms = (time.perf_counter() - started) * 1000.0

        normalized = self._normalize_response(
            response_payload=response_payload,
            ai_request=ai_request,
            latency_ms=latency_ms,
        )
        if transport_retries:
            usage = dict(normalized.usage)
            usage["transport_retries"] = transport_retries
            normalized = normalized.model_copy(update={"usage": usage})
        return normalized

    @staticmethod
    def _should_retry_incomplete_structured_response(
        response_payload: dict[str, Any],
        ai_request: AIRequest,
    ) -> bool:
        if ai_request.response_format is not ResponseFormat.JSON:
            return False

        raw_content = response_payload.get("response")
        if not isinstance(raw_content, str):
            return False

        try:
            json.loads(raw_content)
            return False
        except json.JSONDecodeError:
            pass

        return (
            response_payload.get("done") is False
            or response_payload.get("done_reason") == "length"
        )

    @staticmethod
    def _response_diagnostics(
        response_payload: dict[str, Any],
        raw_content: str,
    ) -> str:
        fields: list[str] = []

        for key in (
            "done",
            "done_reason",
            "prompt_eval_count",
            "eval_count",
        ):
            value = response_payload.get(key)
            if value is not None:
                fields.append(f"{key}={value!r}")

        fields.append(f"response_chars={len(raw_content)}")
        return ", ".join(fields)

    def _normalize_response(
        self,
        response_payload: dict[str, Any],
        ai_request: AIRequest,
        latency_ms: float,
    ) -> AIResponse:
        error_message = response_payload.get("error")
        if error_message:
            raise LocalProviderResponseError(
                f"Ollama error: {error_message}"
            )

        raw_content = response_payload.get("response")
        if not isinstance(raw_content, str):
            raise LocalProviderResponseError(
                "Ollama response is missing string field 'response'"
            )

        usage: dict[str, int] = {}

        prompt_tokens = response_payload.get("prompt_eval_count")
        completion_tokens = response_payload.get("eval_count")

        if isinstance(prompt_tokens, int) and not isinstance(
            prompt_tokens, bool
        ):
            usage["prompt_tokens"] = prompt_tokens

        if isinstance(completion_tokens, int) and not isinstance(
            completion_tokens, bool
        ):
            usage["completion_tokens"] = completion_tokens

        if ai_request.response_format is ResponseFormat.JSON:
            try:
                parsed_output = json.loads(raw_content)
            except json.JSONDecodeError as exc:
                diagnostics = self._response_diagnostics(
                    response_payload,
                    raw_content,
                )
                raise LocalProviderResponseError(
                    "Ollama returned invalid structured JSON "
                    f"({diagnostics})"
                ) from exc

            if not isinstance(parsed_output, dict):
                raise LocalProviderResponseError(
                    "Structured Ollama output must be a JSON object"
                )

            structured_output = parsed_output

            if ai_request.output_schema is not None:
                try:
                    validated = ai_request.output_schema.model_validate(
                        parsed_output
                    )
                except ValidationError as exc:
                    raise LocalProviderValidationError(
                        "Ollama structured output failed "
                        f"{ai_request.output_schema.__name__} validation: "
                        f"{exc}"
                    ) from exc

                structured_output = validated.model_dump(
                    mode="json"
                )

            return AIResponse(
                provider=self.provider_name,
                model=self.model_name,
                content=raw_content,
                structured_output=structured_output,
                latency_ms=latency_ms,
                usage=usage,
            )

        if not raw_content.strip():
            raise LocalProviderResponseError(
                "Ollama returned an empty text response"
            )

        return AIResponse(
            provider=self.provider_name,
            model=self.model_name,
            content=raw_content,
            latency_ms=latency_ms,
            usage=usage,
        )
