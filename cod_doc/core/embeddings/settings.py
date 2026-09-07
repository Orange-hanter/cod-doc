"""Настройки эмбеддера — value-объект, независимый от LLM-конфига (ADO-071).

До этого модуля эмбеддер брал те же ``api_key`` и ``base_url``, что и чат.
Как только LLM-провайдер менялся (Ollama Cloud, Anthropic), эмбеддер уезжал
на чужой или пустой ключ, а все три потребителя глушат ошибку — поиск молча
возвращал пустоту. ``EmbeddingSettings`` разрывает эту связь: у эмбеддера
свой backend, ключ, endpoint, модель и размерность.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cod_doc.config import Config

# ── Имена бэкендов ───────────────────────────────────────────────────────────

BACKEND_OPENAI = "openai"  # generic OpenAI-совместимый endpoint (историческое имя)
BACKEND_OPENROUTER = "openrouter"
BACKEND_LOCAL = "local"  # sentence-transformers, офлайн
BACKEND_MOCK = "mock"  # детерминированный, для тестов

BUILTIN_BACKENDS: frozenset[str] = frozenset(
    {BACKEND_OPENAI, BACKEND_OPENROUTER, BACKEND_LOCAL, BACKEND_MOCK}
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
"""Дефолтный endpoint OpenRouter. Эмбеддинги живут по тому же /v1, что и чат,
но каталог моделей — отдельный (``/v1/embeddings/models``): в общем
``/v1/models`` эмбеддеров нет ни одного."""

DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-ada-002"
DEFAULT_BATCH_SIZE = 128
DEFAULT_MAX_RETRIES = 6
DEFAULT_TIMEOUT_S = 60.0

LOCAL_FALLBACK_MODEL = "all-MiniLM-L6-v2"
"""Слаг вида ``vendor/model`` — это маршрут облачного провайдера, а не имя
модели sentence-transformers. При backend=local подставляем CPU-модель."""


@dataclass(frozen=True, slots=True)
class EmbeddingSettings:
    """Разрешённая конфигурация эмбеддера — всё, что нужно адаптеру.

    ``chroma_path`` сюда НЕ входит: он относится к хранилищу (и служит ключом
    кэша клиента), а не к векторизатору.
    """

    backend: str = BACKEND_OPENAI
    api_key: str = ""
    base_url: str = ""
    model: str = DEFAULT_EMBEDDING_MODEL
    dimensions: int | None = None
    batch_size: int = DEFAULT_BATCH_SIZE
    max_retries: int = DEFAULT_MAX_RETRIES
    timeout: float = DEFAULT_TIMEOUT_S

    @property
    def collection_signature(self) -> str:
        """Отпечаток векторизатора, который пишется в metadata коллекции.

        Формат повторяет домашний стандарт соседних проектов:
        ``openrouter:qwen/qwen3-embedding-8b@2048``.
        """
        return f"{self.backend}:{self.model}@{self.dimensions or 'native'}"

    @property
    def needs_api_key(self) -> bool:
        """Офлайн-бэкендам ключ не нужен, сетевым — обязателен."""
        return self.backend not in (BACKEND_LOCAL, BACKEND_MOCK)

    @property
    def is_usable(self) -> bool:
        """Можно ли вообще пытаться эмбеддить (для тихой деградации поиска)."""
        return bool(self.api_key) if self.needs_api_key else True

    def redacted(self) -> dict[str, str | int | bool | None]:
        """Безопасное представление для логов, CLI и диагностики — без ключа."""
        return {
            "backend": self.backend,
            "base_url": self.base_url,
            "model": self.model,
            "dimensions": self.dimensions,
            "batch_size": self.batch_size,
            "api_key_set": bool(self.api_key),
        }


def settings_from_config(cfg: Config) -> EmbeddingSettings:
    """Собрать настройки эмбеддера из ``Config``.

    Правила резолва — суть ADO-071:

    * ``openai`` (generic) — обратная совместимость: выделенные поля, а если
      они пусты, то общие ``api_key``/``base_url``, как было до разделения.
    * ``openrouter`` — ключ **не наследуется** от LLM: ключ OpenRouter и ключ
      чат-провайдера это разные ключи, и молчаливое наследование дало бы 401,
      неотличимый от бага cod-doc. Endpoint по умолчанию — OpenRouter.
    * ``local`` / ``mock`` — сеть не нужна, ключ и URL игнорируются.

    Поля читаются через ``getattr``: старый ``config.yaml`` без новых ключей
    обязан грузиться (правило репозитория).
    """
    backend = str(getattr(cfg, "embedding_backend", BACKEND_OPENAI) or BACKEND_OPENAI)
    model = str(getattr(cfg, "embedding_model", DEFAULT_EMBEDDING_MODEL) or DEFAULT_EMBEDDING_MODEL)
    dedicated_key = str(getattr(cfg, "embedding_api_key", "") or "")
    dedicated_base = str(getattr(cfg, "embedding_base_url", "") or "")
    dimensions = getattr(cfg, "embedding_dimensions", None)
    batch_size = int(getattr(cfg, "embedding_batch_size", DEFAULT_BATCH_SIZE) or DEFAULT_BATCH_SIZE)

    if backend == BACKEND_OPENROUTER:
        api_key = dedicated_key
        base_url = dedicated_base or OPENROUTER_BASE_URL
    elif backend in (BACKEND_LOCAL, BACKEND_MOCK):
        api_key = ""
        base_url = ""
    else:
        api_key = dedicated_key or str(getattr(cfg, "api_key", "") or "")
        base_url = dedicated_base or str(getattr(cfg, "base_url", "") or "")

    return EmbeddingSettings(
        backend=backend,
        api_key=api_key,
        base_url=base_url,
        model=model,
        dimensions=int(dimensions) if dimensions else None,
        batch_size=batch_size,
    )
