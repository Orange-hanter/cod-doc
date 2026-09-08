"""Контракт embeddings-адаптера (ADO-071).

Форма скопирована с ``agent/adapters/base.py``: нейтральные dataclass-типы,
``runtime_checkable`` Protocol, dataclass возможностей и предикат для
опциональной фичи (``supports_catalog`` — близнец ``supports_streaming``).
Слой сознательно отдельный от LLM-адаптеров: у эмбеддера синхронный контракт
(chroma зовёт ``EmbeddingFunction.__call__`` синхронно) и своя семантика
ошибок.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cod_doc.core.embeddings.settings import EmbeddingSettings


@dataclass(frozen=True, slots=True)
class EmbeddingCapabilities:
    """Что умеет конкретный бэкенд.

    Проверяется кодом поверх адаптера, чтобы не городить isinstance-ветки.
    """

    dimensions_param: bool = False
    """Умеет обрезать вектор на своей стороне (Matryoshka, параметр `dimensions`)."""

    batching: bool = True
    """Принимает список текстов одним запросом."""

    cost_reporting: bool = False
    """Возвращает стоимость вызова (`usage.cost` у OpenRouter)."""

    model_catalog: bool = False
    """Умеет перечислить доступные модели (`list_models`)."""

    offline: bool = False
    """Не ходит в сеть — ключ и endpoint не нужны."""


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """Результат одного логического вызова эмбеддера (может быть склеен из батчей)."""

    vectors: list[list[float]]
    model: str = ""
    provider: str | None = None
    prompt_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal(0))

    @property
    def dimensions(self) -> int:
        """Фактическая размерность — то, что реально вернул провайдер."""
        return len(self.vectors[0]) if self.vectors else 0


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Итог живой проверки доступности эмбеддера (`cod-doc embed probe`)."""

    ok: bool
    backend: str
    model: str
    dimensions: int
    latency_s: float
    cost_usd: Decimal = field(default_factory=lambda: Decimal(0))
    provider: str | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """Строка каталога моделей эмбеддера."""

    model_id: str
    label: str = ""
    context_length: int | None = None
    dimensions: int | None = None
    prompt_per_million: float | None = None


@runtime_checkable
class EmbeddingAdapter(Protocol):
    """Backend-agnostic интерфейс эмбеддера.

    Реализуй протокол и зарегистрируй фабрику в :mod:`.registry`, чтобы
    добавить провайдера, не трогая ни ``core/reindex.py``, ни вызывающих.
    """

    name: str
    capabilities: EmbeddingCapabilities

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        """Векторизовать тексты. Батчинг и ретраи — внутри адаптера."""
        ...

    def probe(self) -> ProbeResult:
        """Один живой вызов: размерность, задержка, стоимость."""
        ...


def supports_catalog(adapter: EmbeddingAdapter) -> bool:
    """Умеет ли адаптер перечислять модели.

    Опциональная возможность объявляется флагом + отдельным методом, а не
    расширением Protocol — тот же приём, что ``supports_streaming`` у LLM.
    """
    return adapter.capabilities.model_catalog and hasattr(adapter, "list_models")


def build_settings_probe(
    settings: EmbeddingSettings,
    *,
    error: str,
) -> ProbeResult:
    """Собрать отрицательный ProbeResult, не ходя в сеть."""
    return ProbeResult(
        ok=False,
        backend=settings.backend,
        model=settings.model,
        dimensions=0,
        latency_s=0.0,
        error=error,
    )
