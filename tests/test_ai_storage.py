from datetime import datetime, timezone

from app.ai import (
    AIRequest,
    AIResponse,
    AITask,
    FinancialSentimentResult,
    ResponseFormat,
    build_inference_record,
)
from app.cio.storage import Stage3Store


def _record(
    inference_id: str,
    timestamp: datetime,
    *,
    task: AITask = AITask.CLASSIFICATION,
    provider: str = "LOCAL_OLLAMA",
    model: str = "qwen3:8b",
    snapshot_id: str | None = None,
    risk_state_id: str | None = None,
):
    request = AIRequest(
        task=task,
        prompt=f"Prompt for {inference_id}",
        response_format=ResponseFormat.JSON,
        output_schema=FinancialSentimentResult,
    )
    response = AIResponse(
        provider=provider,
        model=model,
        content=(
            '{"sentiment":"NEGATIVE","confidence":0.9,'
            '"requires_deep_research":false}'
        ),
        structured_output={
            "sentiment": "NEGATIVE",
            "confidence": 0.9,
            "requires_deep_research": False,
        },
        latency_ms=10.0,
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    return build_inference_record(
        request,
        response,
        portfolio_snapshot_id=snapshot_id,
        risk_state_id=risk_state_id,
        timestamp=timestamp,
        inference_id=inference_id,
    )


def test_save_and_get_ai_inference(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    record = _record(
        "AI-001",
        datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc),
    )

    store.save_ai_inference(record)

    loaded = store.get_ai_inference("AI-001")
    assert loaded == record


def test_save_ai_inference_is_upsert(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    timestamp = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)

    first = _record("AI-001", timestamp, model="qwen3:8b")
    second = _record("AI-001", timestamp, model="qwen3:14b")

    store.save_ai_inference(first)
    store.save_ai_inference(second)

    loaded = store.get_ai_inference("AI-001")
    assert loaded is not None
    assert loaded.model == "qwen3:14b"
    assert len(store.list_ai_inferences()) == 1


def test_list_ai_inferences_is_most_recent_first(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")

    older = _record(
        "AI-001",
        datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc),
    )
    newer = _record(
        "AI-002",
        datetime(2026, 8, 30, 19, 0, tzinfo=timezone.utc),
    )

    store.save_ai_inference(older)
    store.save_ai_inference(newer)

    assert [
        item.inference_id for item in store.list_ai_inferences()
    ] == ["AI-002", "AI-001"]


def test_list_ai_inferences_supports_filters(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")
    timestamp = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)

    store.save_ai_inference(
        _record(
            "AI-001",
            timestamp,
            provider="LOCAL_OLLAMA",
            model="qwen3:8b",
            snapshot_id="SNAP-1",
            risk_state_id="RISK-1",
        )
    )
    store.save_ai_inference(
        _record(
            "AI-002",
            timestamp,
            provider="OPENAI",
            model="frontier",
            snapshot_id="SNAP-2",
            risk_state_id="RISK-2",
        )
    )

    result = store.list_ai_inferences(
        provider="LOCAL_OLLAMA",
        model="qwen3:8b",
        portfolio_snapshot_id="SNAP-1",
        risk_state_id="RISK-1",
    )

    assert [item.inference_id for item in result] == ["AI-001"]


def test_get_latest_ai_inference_supports_task_filter(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")

    store.save_ai_inference(
        _record(
            "AI-001",
            datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc),
            task=AITask.CLASSIFICATION,
        )
    )
    store.save_ai_inference(
        _record(
            "AI-002",
            datetime(2026, 8, 30, 19, 0, tzinfo=timezone.utc),
            task=AITask.SENTIMENT,
        )
    )

    latest = store.get_latest_ai_inference(
        task=AITask.CLASSIFICATION.value
    )

    assert latest is not None
    assert latest.inference_id == "AI-001"


def test_get_missing_ai_inference_returns_none(tmp_path):
    store = Stage3Store(tmp_path / "stage3.db")

    assert store.get_ai_inference("MISSING") is None
