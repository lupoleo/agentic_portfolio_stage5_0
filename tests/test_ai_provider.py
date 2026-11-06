import pytest

from app.ai.models import AIRequest, AIResponse, AITask
from app.ai.provider import AIModelProvider


class StubProvider(AIModelProvider):
    @property
    def provider_name(self) -> str:
        return "STUB"

    @property
    def model_name(self) -> str:
        return "stub-model"

    def infer(self, request: AIRequest) -> AIResponse:
        return AIResponse(
            provider=self.provider_name,
            model=self.model_name,
            content=f"handled:{request.task.value}",
            latency_ms=0.0,
        )


def test_provider_contract_can_be_implemented_without_cio_dependency():
    provider = StubProvider()
    request = AIRequest(
        task=AITask.CLASSIFICATION,
        prompt="Classify this.",
    )

    response = provider.infer(request)

    assert provider.provider_name == "STUB"
    assert provider.model_name == "stub-model"
    assert response.provider == "STUB"
    assert response.model == "stub-model"
    assert response.content == "handled:CLASSIFICATION"


def test_provider_contract_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        AIModelProvider()
