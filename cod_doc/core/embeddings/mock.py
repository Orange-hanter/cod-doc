"""Детерминированный эмбеддер для тестов (ADO-071).

Аналог ``agent/adapters/mock.py``: даёт воспроизводимые векторы без сети и
без опциональных зависимостей, чтобы тесты вокруг chroma и реестра не зависели
ни от провайдера, ни от torch.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING

from cod_doc.core.embeddings.base import EmbeddingBatch, EmbeddingCapabilities, ProbeResult
from cod_doc.core.embeddings.settings import BACKEND_MOCK

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cod_doc.core.embeddings.settings import EmbeddingSettings

MOCK_DIMENSIONS = 8


class MockEmbeddingAdapter:
    """Вектор выводится из sha256 текста — одинаковый вход даёт одинаковый выход."""

    name = BACKEND_MOCK
    capabilities = EmbeddingCapabilities(
        dimensions_param=True,
        batching=True,
        cost_reporting=False,
        model_catalog=False,
        offline=True,
    )

    def __init__(self, settings: EmbeddingSettings) -> None:
        self.settings = settings
        self.dimensions = settings.dimensions or MOCK_DIMENSIONS

    @classmethod
    def from_settings(cls, settings: EmbeddingSettings) -> MockEmbeddingAdapter:
        return cls(settings)

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(self.dimensions)]

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=[self._vector(t) for t in texts],
            model=self.settings.model,
            prompt_tokens=sum(len(t.split()) for t in texts),
        )

    def probe(self) -> ProbeResult:
        started = time.monotonic()
        batch = self.embed(["probe"])
        return ProbeResult(
            ok=True,
            backend=self.settings.backend,
            model=self.settings.model,
            dimensions=batch.dimensions,
            latency_s=round(time.monotonic() - started, 3),
        )
