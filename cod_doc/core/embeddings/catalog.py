"""Curated размерности эмбеддинг-моделей (ADO-071).

Каталог OpenRouter (``/api/v1/embeddings/models``) размерность **не отдаёт** —
ни отдельным полем, ни в ``architecture``. Без неё нельзя ни подсказать
значение ``embedding_dimensions``, ни объяснить пользователю, почему коллекция
несовместима. Значения ниже замерены живыми вызовами 2026-09-07; там, где
модель поддерживает Matryoshka-обрезку, указана нативная размерность.

Список неполный по построению: это подсказка для UI и CLI, а не источник
истины. Неизвестная модель — просто ``None``, работать это не мешает.
``PRESETS`` — тот же список, ужатый до строк, которые страница настроек
показывает выпадающим списком.
"""

from __future__ import annotations

from dataclasses import dataclass

NATIVE_DIMENSIONS: dict[str, int] = {
    "openai/text-embedding-ada-002": 1536,
    "openai/text-embedding-3-small": 1536,
    "openai/text-embedding-3-large": 3072,
    "qwen/qwen3-embedding-8b": 4096,
    "qwen/qwen3-embedding-4b": 2560,
    "nvidia/nemotron-3-embed-1b:free": 2048,
    "liquid/lfm-2.5-embedding-350m:free": 1024,
    "voyageai/voyage-code-4": 1024,
    "google/gemini-embedding-2": 3072,
}

RECOMMENDED_MODEL = "qwen/qwen3-embedding-8b"
RECOMMENDED_DIMENSIONS = 2048
"""Домашний стандарт graphify / Orakul / стенда E9: у qwen3-embedding-8b
обрезка до 2048 замерена как lossless против нативных 4096, а индекс вдвое
меньше и косинус вдвое быстрее."""


def known_dimensions(model_id: str) -> int | None:
    """Нативная размерность модели, если она нам известна."""
    return NATIVE_DIMENSIONS.get(model_id)


@dataclass(frozen=True, slots=True)
class EmbeddingPreset:
    """Одна строка выпадающего списка на странице настроек.

    ``dimensions`` — значение, которое форма подставляет в поле размерности.
    ``None`` значит «оставить пустым»: пустое поле — нативная размерность, и
    подстановка нативного числа сменила бы отпечаток коллекции с ``@native``
    на ``@1536`` без реальной причины. Непустое значение — осознанная
    Matryoshka-обрезка (домашний стандарт Qwen — 2048 из 4096).
    """

    model_id: str
    label: str
    notes: str
    backends: tuple[str, ...]
    dimensions: int | None = None
    recommended: bool = False


PRESETS: tuple[EmbeddingPreset, ...] = (
    EmbeddingPreset(
        model_id=RECOMMENDED_MODEL,
        label="Qwen3 Embedding 8B",
        notes="Рекомендуется. Нативные 4096, форма ставит обрезку 2048.",
        backends=("openrouter",),
        dimensions=RECOMMENDED_DIMENSIONS,
        recommended=True,
    ),
    EmbeddingPreset(
        model_id="qwen/qwen3-embedding-4b",
        label="Qwen3 Embedding 4B",
        notes="Меньше Qwen. Нативные 2560, поле размерности остаётся пустым.",
        backends=("openrouter",),
    ),
    EmbeddingPreset(
        model_id="google/gemini-embedding-2",
        label="Gemini Embedding 2",
        notes="Нативные 3072.",
        backends=("openrouter",),
    ),
    EmbeddingPreset(
        model_id="openai/text-embedding-3-small",
        label="OpenAI Embedding 3 small",
        notes="Дешёвая общая модель. Нативные 1536.",
        backends=("openai", "openrouter"),
    ),
    EmbeddingPreset(
        model_id="openai/text-embedding-3-large",
        label="OpenAI Embedding 3 large",
        notes="Точнее small, дороже. Нативные 3072.",
        backends=("openai", "openrouter"),
    ),
    EmbeddingPreset(
        model_id="openai/text-embedding-ada-002",
        label="Ada 002",
        notes="Прежний дефолт. Нативные 1536.",
        backends=("openai", "openrouter"),
    ),
    EmbeddingPreset(
        model_id="all-MiniLM-L6-v2",
        label="MiniLM L6",
        notes="Быстрая локальная модель на CPU, без ключа.",
        backends=("local",),
    ),
    EmbeddingPreset(
        model_id="paraphrase-multilingual-MiniLM-L12-v2",
        label="Multilingual MiniLM",
        notes="Локальная модель, лучше покрывает русский текст.",
        backends=("local",),
    ),
)


def matching_preset(model_id: str, backend: str) -> EmbeddingPreset | None:
    """Пресет, если эта модель предлагается для данного бэкенда."""
    for preset in PRESETS:
        if preset.model_id == model_id and backend in preset.backends:
            return preset
    return None
