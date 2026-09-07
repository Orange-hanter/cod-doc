"""Провайдер эмбеддингов cod-doc — отдельный от LLM-провайдера (ADO-071).

Публичный вход: :func:`settings_from_config` собирает ``EmbeddingSettings``
из конфига, :func:`get_adapter_from_settings` даёт адаптер, а
``core/reindex.py`` превращает его в chroma-EF.

Модуль ``chroma_ef`` здесь намеренно **не** импортируется: он тянет chromadb,
а диагностике (``cod-doc embed status``) и резолву настроек векторное
хранилище не нужно. Его импортирует ``core/reindex.py`` ровно перед открытием
коллекции — там же, где регистрируется chroma-EF.
"""

from __future__ import annotations

from cod_doc.core.embeddings.base import (
    CatalogEntry,
    EmbeddingAdapter,
    EmbeddingBatch,
    EmbeddingCapabilities,
    ProbeResult,
    supports_catalog,
)
from cod_doc.core.embeddings.errors import (
    EmbeddingConfigError,
    EmbeddingDimensionMismatch,
    EmbeddingError,
)
from cod_doc.core.embeddings.registry import (
    get_adapter_from_settings,
    get_embedding_adapter,
    list_embedding_adapters,
    register_embedding_adapter,
)
from cod_doc.core.embeddings.settings import (
    BACKEND_LOCAL,
    BACKEND_MOCK,
    BACKEND_OPENAI,
    BACKEND_OPENROUTER,
    BUILTIN_BACKENDS,
    DEFAULT_EMBEDDING_MODEL,
    OPENROUTER_BASE_URL,
    EmbeddingSettings,
    settings_from_config,
)

__all__ = [
    "BACKEND_LOCAL",
    "BACKEND_MOCK",
    "BACKEND_OPENAI",
    "BACKEND_OPENROUTER",
    "BUILTIN_BACKENDS",
    "DEFAULT_EMBEDDING_MODEL",
    "OPENROUTER_BASE_URL",
    "CatalogEntry",
    "EmbeddingAdapter",
    "EmbeddingBatch",
    "EmbeddingCapabilities",
    "EmbeddingConfigError",
    "EmbeddingDimensionMismatch",
    "EmbeddingError",
    "EmbeddingSettings",
    "ProbeResult",
    "get_adapter_from_settings",
    "get_embedding_adapter",
    "list_embedding_adapters",
    "register_embedding_adapter",
    "settings_from_config",
    "supports_catalog",
]
