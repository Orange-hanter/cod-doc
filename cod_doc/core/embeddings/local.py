"""Локальный эмбеддер на sentence-transformers (ADO-071).

Существует ради двух вещей: офлайн-режим без единого сетевого вызова (COD-043)
и единообразная диагностика — ``cod-doc embed status/probe`` обязан отвечать
на любом бэкенде, а не только на облачном.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from cod_doc.core.embeddings.base import EmbeddingBatch, EmbeddingCapabilities, ProbeResult
from cod_doc.core.embeddings.errors import EmbeddingError
from cod_doc.core.embeddings.settings import BACKEND_LOCAL, LOCAL_FALLBACK_MODEL

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cod_doc.core.embeddings.settings import EmbeddingSettings

_PROBE_TEXT = "cod-doc embedding probe"


def resolve_local_model(model: str) -> str:
    """Слаг ``vendor/model`` — это облачный маршрут, а не имя ST-модели."""
    return LOCAL_FALLBACK_MODEL if "/" in model else model


class LocalEmbeddingAdapter:
    """sentence-transformers на CPU."""

    name = BACKEND_LOCAL
    capabilities = EmbeddingCapabilities(
        dimensions_param=False,
        batching=True,
        cost_reporting=False,
        model_catalog=False,
        offline=True,
    )

    def __init__(self, settings: EmbeddingSettings) -> None:
        self.settings = settings
        self.model_name = resolve_local_model(settings.model)
        self._model: Any | None = None

    @classmethod
    def from_settings(cls, settings: EmbeddingSettings) -> LocalEmbeddingAdapter:
        return cls(settings)

    @property
    def model(self) -> Any:  # noqa: ANN401 — тип принадлежит опциональной зависимости
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError(
                    "Локальные эмбеддинги требуют sentence-transformers.",
                    hint="pip install 'cod-doc[embeddings-local]'",
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch(vectors=[], model=self.model_name)
        vectors = self.model.encode(list(texts))
        return EmbeddingBatch(
            vectors=[[float(x) for x in row] for row in vectors],
            model=self.model_name,
        )

    def probe(self) -> ProbeResult:
        started = time.monotonic()
        try:
            batch = self.embed([_PROBE_TEXT])
        except EmbeddingError as exc:
            return ProbeResult(
                ok=False,
                backend=self.settings.backend,
                model=self.model_name,
                dimensions=0,
                latency_s=round(time.monotonic() - started, 3),
                error=str(exc),
            )
        return ProbeResult(
            ok=True,
            backend=self.settings.backend,
            model=self.model_name,
            dimensions=batch.dimensions,
            latency_s=round(time.monotonic() - started, 3),
        )
