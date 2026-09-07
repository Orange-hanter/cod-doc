"""Curated размерности эмбеддинг-моделей (ADO-071).

Каталог OpenRouter (``/api/v1/embeddings/models``) размерность **не отдаёт** —
ни отдельным полем, ни в ``architecture``. Без неё нельзя ни подсказать
значение ``embedding_dimensions``, ни объяснить пользователю, почему коллекция
несовместима. Значения ниже замерены живыми вызовами 2026-09-07; там, где
модель поддерживает Matryoshka-обрезку, указана нативная размерность.

Список неполный по построению: это подсказка для UI и CLI, а не источник
истины. Неизвестная модель — просто ``None``, работать это не мешает.
"""

from __future__ import annotations

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
