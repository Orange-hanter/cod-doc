"""Адаптер эмбеддера OpenRouter (ADO-071).

Что у OpenRouter не как у OpenAI — всё проверено живыми вызовами 2026-09-07:

* ``usage`` несёт лишние поля ``cost`` (USD), ``is_byok``, ``cost_details``,
  а в корне ответа есть ``provider`` и ``id`` — их отдаёт ``model_extra``,
  атрибутов в типах SDK для них нет;
* ошибка может приехать **телом при HTTP 200**;
* формат ошибки двойной вложенности: апстримный JSON лежит *строкой* внутри
  ``error.message``, а ``code`` — int, а не строка;
* каталог эмбеддеров **отдельный**: в ``/api/v1/models`` их ноль из 430,
  живут в ``/api/v1/embeddings/models`` (33 модели), и поля размерности там
  нет — размерности держим в :mod:`cod_doc.core.embeddings.catalog`;
* ``dimensions`` работает как Matryoshka-обрезка: ``qwen/qwen3-embedding-8b``
  отдаёт 4096 без параметра и ровно 2048 с ним.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from cod_doc.core.embeddings.base import CatalogEntry, EmbeddingCapabilities
from cod_doc.core.embeddings.catalog import known_dimensions
from cod_doc.core.embeddings.errors import EmbeddingError
from cod_doc.core.embeddings.openai_compat import OpenAICompatEmbeddingAdapter
from cod_doc.core.embeddings.settings import BACKEND_OPENROUTER, OPENROUTER_BASE_URL

if TYPE_CHECKING:
    from openai.types import CreateEmbeddingResponse

    from cod_doc.core.embeddings.settings import EmbeddingSettings

_CATALOG_PATH = "/embeddings/models"
_CATALOG_TIMEOUT_S = 20.0


class OpenRouterEmbeddingAdapter(OpenAICompatEmbeddingAdapter):
    """OpenRouter поверх общего OpenAI-совместимого транспорта."""

    name = BACKEND_OPENROUTER
    capabilities = EmbeddingCapabilities(
        dimensions_param=True,
        batching=True,
        cost_reporting=True,
        model_catalog=True,
        offline=False,
    )

    def __init__(self, settings: EmbeddingSettings) -> None:
        if not settings.api_key:
            raise EmbeddingError(
                "backend=openrouter: не задан ключ эмбеддера.",
                hint=(
                    "Ключ OpenRouter не наследуется от ключа LLM — это разные ключи. "
                    "Задайте embedding_api_key (env COD_DOC_EMBEDDING_API_KEY)."
                ),
            )
        super().__init__(settings)

    # ── причуды OpenRouter ───────────────────────────────────────────────────

    def _cost_of(self, response: CreateEmbeddingResponse) -> Decimal:
        extra = getattr(response.usage, "model_extra", None) or {}
        raw = extra.get("cost")
        if raw is None:
            return Decimal(0)
        try:
            return Decimal(str(raw))
        except (ArithmeticError, ValueError):
            return Decimal(0)

    def _provider_of(self, response: CreateEmbeddingResponse) -> str | None:
        extra = response.model_extra or {}
        provider = extra.get("provider")
        return str(provider) if provider else None

    def _check_body_error(self, response: CreateEmbeddingResponse) -> None:
        """OpenRouter умеет вернуть 200 с ошибкой в теле — ловим до разбора data."""
        extra = response.model_extra or {}
        if extra.get("error"):
            raise EmbeddingError.from_payload(extra)

    # ── каталог моделей ──────────────────────────────────────────────────────

    def list_models(self) -> list[CatalogEntry]:
        """Модели эмбеддингов OpenRouter (их нет в общем /models)."""
        import httpx

        url = f"{(self.settings.base_url or OPENROUTER_BASE_URL).rstrip('/')}{_CATALOG_PATH}"
        try:
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=_CATALOG_TIMEOUT_S,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise EmbeddingError.from_provider(
                exc,
                backend=self.settings.backend,
                model=self.settings.model,
                base_url=self.settings.base_url,
            ) from exc

        entries: list[CatalogEntry] = []
        for item in payload.get("data", []):
            model_id = str(item.get("id", ""))
            if not model_id:
                continue
            pricing = item.get("pricing") or {}
            entries.append(
                CatalogEntry(
                    model_id=model_id,
                    label=str(item.get("name", "")),
                    context_length=item.get("context_length"),
                    dimensions=known_dimensions(model_id),
                    prompt_per_million=_per_million(pricing.get("prompt")),
                )
            )
        return sorted(entries, key=lambda e: e.model_id)


def _per_million(raw: object) -> float | None:
    """Цена OpenRouter приходит за токен строкой — приводим к $/1M токенов."""
    if raw is None:
        return None
    try:
        return float(str(raw)) * 1_000_000
    except (TypeError, ValueError):
        return None
