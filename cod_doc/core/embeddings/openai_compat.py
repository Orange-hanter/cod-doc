"""Адаптер эмбеддера для любого OpenAI-совместимого endpoint (ADO-071).

Здесь живёт транспорт, общий для generic-OpenAI и OpenRouter: батчинг,
ретраи, перевод ошибок, чтение usage. Специфика OpenRouter (каталог моделей,
`usage.cost`, ошибка в теле при HTTP 200) — в наследнике
:mod:`cod_doc.core.embeddings.openrouter`.

Почему не стоковая ``chromadb.utils.embedding_functions.OpenAIEmbeddingFunction``:
она форвардит ``dimensions`` только когда в имени модели есть подстрока
``text-embedding-3`` (см. её ``__call__``), поэтому Matryoshka-обрезка
``qwen/qwen3-embedding-8b@2048`` — домашний стандарт — через неё недостижима.
Плюс у неё нет ни батчинга по нашему размеру, ни разбора ошибок провайдера.
"""

from __future__ import annotations

import time
from decimal import Decimal
from functools import partial
from typing import TYPE_CHECKING

from cod_doc.core.embeddings.base import (
    EmbeddingBatch,
    EmbeddingCapabilities,
    ProbeResult,
)
from cod_doc.core.embeddings.errors import EmbeddingConfigError, EmbeddingError, sync_retry

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from openai import OpenAI
    from openai.types import CreateEmbeddingResponse

    from cod_doc.core.embeddings.settings import EmbeddingSettings

_PROBE_TEXT = "cod-doc embedding probe"


class OpenAICompatEmbeddingAdapter:
    """Эмбеддер поверх OpenAI-совместимого ``/embeddings``."""

    name = "openai"
    capabilities = EmbeddingCapabilities(
        dimensions_param=True,
        batching=True,
        cost_reporting=False,
        model_catalog=False,
        offline=False,
    )

    def __init__(self, settings: EmbeddingSettings) -> None:
        if not settings.api_key:
            raise EmbeddingConfigError(
                f"backend={settings.backend}: не задан ключ эмбеддера.",
                hint=(
                    "Заполните embedding_api_key (или api_key для backend=openai), "
                    "либо переключите embedding_backend на 'local'."
                ),
            )
        self.settings = settings
        self._client: OpenAI | None = None

    @classmethod
    def from_settings(cls, settings: EmbeddingSettings) -> OpenAICompatEmbeddingAdapter:
        return cls(settings)

    # ── транспорт ────────────────────────────────────────────────────────────

    @property
    def client(self) -> OpenAI:
        """Ленивый клиент: SDK импортируется только когда эмбеддер реально нужен."""
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url or None,
                timeout=self.settings.timeout,
                max_retries=0,  # ретраим сами: sync_retry знает нашу классификацию
            )
        return self._client

    def _chunks(self, texts: Sequence[str]) -> Iterator[Sequence[str]]:
        size = max(1, self.settings.batch_size)
        for start in range(0, len(texts), size):
            yield texts[start : start + size]

    def _request(self, chunk: Sequence[str]) -> CreateEmbeddingResponse:
        params: dict[str, object] = {"model": self.settings.model, "input": list(chunk)}
        if self.settings.dimensions:
            # Безусловно, а не только для text-embedding-3: ровно этого не умеет
            # стоковая EF, и ровно это нужно для Matryoshka-моделей.
            params["dimensions"] = self.settings.dimensions
        try:
            return self.client.embeddings.create(**params)  # type: ignore[arg-type]
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError.from_provider(
                exc,
                backend=self.settings.backend,
                model=self.settings.model,
                base_url=self.settings.base_url,
            ) from exc

    def _vectors_of(self, response: CreateEmbeddingResponse) -> list[list[float]]:
        # Порядок в ``data`` формально не гарантирован — сортируем по index,
        # иначе документы разъедутся с векторами при батче.
        rows = sorted(response.data, key=lambda item: item.index)
        return [list(row.embedding) for row in rows]

    def _cost_of(self, response: CreateEmbeddingResponse) -> Decimal:
        return Decimal(0)

    def _provider_of(self, response: CreateEmbeddingResponse) -> str | None:
        return None

    def _check_body_error(self, response: CreateEmbeddingResponse) -> None:
        """У generic-провайдера ошибок в теле при 200 не бывает."""

    # ── публичный контракт ───────────────────────────────────────────────────

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch(vectors=[], model=self.settings.model)

        vectors: list[list[float]] = []
        prompt_tokens = 0
        total_tokens = 0
        cost = Decimal(0)
        provider: str | None = None

        for chunk in self._chunks(texts):
            response = sync_retry(partial(self._request, chunk), attempts=self.settings.max_retries)
            self._check_body_error(response)
            vectors.extend(self._vectors_of(response))
            prompt_tokens += response.usage.prompt_tokens
            total_tokens += response.usage.total_tokens
            cost += self._cost_of(response)
            provider = provider or self._provider_of(response)

        return EmbeddingBatch(
            vectors=vectors,
            model=self.settings.model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
        )

    def probe(self) -> ProbeResult:
        started = time.monotonic()
        try:
            batch = self.embed([_PROBE_TEXT])
        except EmbeddingError as exc:
            return ProbeResult(
                ok=False,
                backend=self.settings.backend,
                model=self.settings.model,
                dimensions=0,
                latency_s=round(time.monotonic() - started, 3),
                error=str(exc),
            )
        return ProbeResult(
            ok=True,
            backend=self.settings.backend,
            model=self.settings.model,
            dimensions=batch.dimensions,
            latency_s=round(time.monotonic() - started, 3),
            cost_usd=batch.cost_usd,
            provider=batch.provider,
        )
