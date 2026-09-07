"""ADO-071: транспорт OpenRouter — причуды, замеренные живьём, закреплены тестами.

Сети здесь нет: подменяется клиент openai-SDK.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from cod_doc.core.embeddings.errors import EmbeddingError, _deepest_message, sync_retry
from cod_doc.core.embeddings.openrouter import OpenRouterEmbeddingAdapter
from cod_doc.core.embeddings.settings import EmbeddingSettings


class _Row:
    def __init__(self, index: int, embedding: list[float]) -> None:
        self.index = index
        self.embedding = embedding


class _Usage:
    def __init__(self, prompt_tokens: int = 1, total_tokens: int = 1, cost: Any = None) -> None:
        self.prompt_tokens = prompt_tokens
        self.total_tokens = total_tokens
        self.model_extra = {} if cost is None else {"cost": cost}


class _Response:
    def __init__(self, rows: list[_Row], *, extra: dict[str, Any] | None = None, cost=None) -> None:
        self.data = rows
        self.usage = _Usage(cost=cost)
        self.model_extra = extra or {}


class _FakeEmbeddings:
    def __init__(self, responses: list[Any], calls: list[dict[str, Any]]) -> None:
        self._responses = responses
        self._calls = calls

    def create(self, **kwargs: Any) -> Any:
        self._calls.append(kwargs)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeClient:
    def __init__(self, responses: list[Any], calls: list[dict[str, Any]]) -> None:
        self.embeddings = _FakeEmbeddings(responses, calls)


def _adapter(
    responses: list[Any], calls: list[dict[str, Any]], **kwargs: Any
) -> OpenRouterEmbeddingAdapter:
    settings = EmbeddingSettings(
        backend="openrouter",
        api_key="sk-or-test",
        base_url="https://openrouter.ai/api/v1",
        model=kwargs.pop("model", "qwen/qwen3-embedding-8b"),
        max_retries=kwargs.pop("max_retries", 3),
        **kwargs,
    )
    adapter = OpenRouterEmbeddingAdapter(settings)
    adapter._client = _FakeClient(responses, calls)  # type: ignore[assignment]
    return adapter


class _HttpError(Exception):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status
        self.body = body


def test_dimensions_forwarded_for_non_text_embedding_3_model() -> None:
    """Ровно то, чего не умеет стоковая chroma-EF: dimensions для любой модели."""
    calls: list[dict[str, Any]] = []
    adapter = _adapter([_Response([_Row(0, [0.1] * 2048)])], calls, dimensions=2048)
    adapter.embed(["hello"])
    assert calls[0]["dimensions"] == 2048
    assert calls[0]["model"] == "qwen/qwen3-embedding-8b"


def test_dimensions_omitted_when_none() -> None:
    calls: list[dict[str, Any]] = []
    adapter = _adapter([_Response([_Row(0, [0.1])])], calls)
    adapter.embed(["hello"])
    assert "dimensions" not in calls[0]


def test_data_is_sorted_by_index() -> None:
    calls: list[dict[str, Any]] = []
    rows = [_Row(2, [3.0]), _Row(0, [1.0]), _Row(1, [2.0])]
    adapter = _adapter([_Response(rows)], calls)
    batch = adapter.embed(["a", "b", "c"])
    assert batch.vectors == [[1.0], [2.0], [3.0]]


def test_batches_are_split_by_batch_size() -> None:
    calls: list[dict[str, Any]] = []
    responses = [_Response([_Row(0, [1.0]), _Row(1, [2.0])]), _Response([_Row(0, [3.0])])]
    adapter = _adapter(responses, calls, batch_size=2)
    batch = adapter.embed(["a", "b", "c"])
    assert len(calls) == 2
    assert calls[0]["input"] == ["a", "b"]
    assert calls[1]["input"] == ["c"]
    assert len(batch.vectors) == 3


def test_usage_cost_extracted_when_present() -> None:
    calls: list[dict[str, Any]] = []
    adapter = _adapter([_Response([_Row(0, [1.0])], cost=6e-08)], calls)
    assert adapter.embed(["a"]).cost_usd == Decimal("6E-8")


def test_usage_cost_defaults_to_zero_when_absent() -> None:
    calls: list[dict[str, Any]] = []
    adapter = _adapter([_Response([_Row(0, [1.0])])], calls)
    assert adapter.embed(["a"]).cost_usd == Decimal(0)


def test_provider_is_read_from_model_extra() -> None:
    calls: list[dict[str, Any]] = []
    adapter = _adapter([_Response([_Row(0, [1.0])], extra={"provider": "DeepInfra"})], calls)
    assert adapter.embed(["a"]).provider == "DeepInfra"


def test_error_in_200_body_raises() -> None:
    """OpenRouter умеет вернуть 200 с ошибкой в теле."""
    calls: list[dict[str, Any]] = []
    body = {"error": {"message": "upstream exploded", "code": 500}}
    adapter = _adapter([_Response([_Row(0, [1.0])], extra=body)], calls)
    with pytest.raises(EmbeddingError, match="upstream exploded"):
        adapter.embed(["a"])


def test_nested_openrouter_error_message_is_unwrapped() -> None:
    raw = {
        "message": 'HTTP 400: {"object":"error","message":"dimensions must be one of 2048"}',
        "code": 400,
    }
    assert _deepest_message(raw) == "dimensions must be one of 2048"


def test_dimensions_not_supported_400_gives_actionable_hint() -> None:
    calls: list[dict[str, Any]] = []
    err = _HttpError(
        400,
        {
            "error": {
                "message": 'HTTP 400: {"message":"dimensions must be one of 2048"}',
                "code": 400,
            }
        },
    )
    adapter = _adapter([err], calls, dimensions=1024)
    with pytest.raises(EmbeddingError) as excinfo:
        adapter.embed(["a"])
    assert "dimensions" in str(excinfo.value)
    assert "embedding_dimensions" in excinfo.value.hint


def test_404_hints_provider_has_no_embeddings_route() -> None:
    """Кейс Ollama Cloud: чат есть, эмбеддингов нет."""
    calls: list[dict[str, Any]] = []
    err = _HttpError(404, {"error": 'path "/v1/embeddings" not found'})
    adapter = _adapter([err], calls)
    with pytest.raises(EmbeddingError) as excinfo:
        adapter.embed(["a"])
    assert "нет маршрута /embeddings" in excinfo.value.hint
    assert excinfo.value.retryable is False


def test_401_is_not_retryable_and_explains_separate_key() -> None:
    calls: list[dict[str, Any]] = []
    err = _HttpError(401, {"error": {"message": "No auth credentials found", "code": 401}})
    adapter = _adapter([err], calls)
    with pytest.raises(EmbeddingError) as excinfo:
        adapter.embed(["a"])
    assert excinfo.value.retryable is False
    assert "не наследуется" in excinfo.value.hint
    assert len(calls) == 1  # без ретраев


def test_429_is_retryable_and_eventually_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("cod_doc.core.embeddings.errors.time.sleep", lambda _: None)
    calls: list[dict[str, Any]] = []
    err = _HttpError(429, {"error": {"message": "rate limited", "code": 429}})
    adapter = _adapter([err, _Response([_Row(0, [1.0])])], calls)
    assert adapter.embed(["a"]).vectors == [[1.0]]
    assert len(calls) == 2


def test_retry_exhaustion_raises_last_error(monkeypatch) -> None:
    monkeypatch.setattr("cod_doc.core.embeddings.errors.time.sleep", lambda _: None)
    calls: list[dict[str, Any]] = []
    errors = [
        _HttpError(429, {"error": {"message": "rate limited", "code": 429}}) for _ in range(3)
    ]
    adapter = _adapter(errors, calls, max_retries=3)
    with pytest.raises(EmbeddingError, match="rate limited"):
        adapter.embed(["a"])
    assert len(calls) == 3


def test_empty_input_returns_empty_without_network() -> None:
    calls: list[dict[str, Any]] = []
    adapter = _adapter([], calls)
    batch = adapter.embed([])
    assert batch.vectors == []
    assert calls == []


def test_missing_dedicated_key_is_rejected_up_front() -> None:
    with pytest.raises(EmbeddingError) as excinfo:
        OpenRouterEmbeddingAdapter(EmbeddingSettings(backend="openrouter", api_key=""))
    assert "не наследуется" in excinfo.value.hint


def test_sync_retry_does_not_retry_config_errors() -> None:
    attempts = {"n": 0}

    def call() -> None:
        attempts["n"] += 1
        raise EmbeddingError("bad request", retryable=False, status_code=400)

    with pytest.raises(EmbeddingError):
        sync_retry(call, attempts=5, base_delay=0)
    assert attempts["n"] == 1


def test_probe_reports_dimension_and_cost() -> None:
    calls: list[dict[str, Any]] = []
    adapter = _adapter([_Response([_Row(0, [0.1] * 2048)], cost=6e-08)], calls, dimensions=2048)
    result = adapter.probe()
    assert result.ok is True
    assert result.dimensions == 2048
    assert result.cost_usd == Decimal("6E-8")


def test_probe_reports_error_without_raising() -> None:
    calls: list[dict[str, Any]] = []
    adapter = _adapter([_HttpError(404, {"error": "nope"})], calls)
    result = adapter.probe()
    assert result.ok is False
    assert "нет маршрута /embeddings" in result.error
