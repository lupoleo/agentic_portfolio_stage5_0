from __future__ import annotations

from abc import ABC, abstractmethod

from .models import AIRequest, AIResponse


class AIModelProvider(ABC):
    """Provider-neutral interface implemented by local and cloud models."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier, e.g. LOCAL_OLLAMA."""
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Concrete model identifier used by this provider."""
        raise NotImplementedError

    @abstractmethod
    def infer(self, request: AIRequest) -> AIResponse:
        """Run one inference request and return a normalized response."""
        raise NotImplementedError
