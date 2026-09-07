"""Мост «embeddings-адаптер → chromadb.EmbeddingFunction» (ADO-071).

Замерено на chromadb 1.5.9: конфиг EF в коллекции **не персистится**
(``collections.config_json_str`` остаётся ``{}``), и при открытии без явного
``embedding_function`` chroma молча подставляет ``DefaultEmbeddingFunction``
(384 измерения). Поэтому EF обязан передаваться на каждом открытии —
``get_collection`` в :mod:`cod_doc.core.reindex` так и делает.
``@register_embedding_function`` оставлен как страховка на случай, когда
chroma всё-таки восстановит EF по имени.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import numpy as np
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings, Space
from chromadb.utils.embedding_functions import register_embedding_function

from cod_doc.core.embeddings.registry import get_adapter_from_settings
from cod_doc.core.embeddings.settings import EmbeddingSettings

if TYPE_CHECKING:
    from cod_doc.core.embeddings.base import EmbeddingAdapter

_API_KEY_ENV_VAR = "COD_DOC_EMBEDDING_API_KEY"


@register_embedding_function
class AdapterEmbeddingFunction(EmbeddingFunction[Documents]):
    """chroma-обёртка над любым нашим адаптером."""

    def __init__(self, settings: EmbeddingSettings) -> None:
        self._settings = settings
        # Адаптер строится сразу, а не лениво: неизвестный backend и
        # незаполненный ключ обязаны падать при открытии коллекции. Отложи мы
        # это до первого __call__ — ошибка всплыла бы внутри поиска, где её
        # глушит fail-open, и поломка снова стала бы невидимой.
        self._adapter: EmbeddingAdapter = get_adapter_from_settings(settings)
        self._cost = Decimal(0)

    @property
    def adapter(self) -> EmbeddingAdapter:
        return self._adapter

    @property
    def consumed_cost_usd(self) -> Decimal:
        """Сколько потрачено за жизнь этого EF — читается после reindex."""
        return self._cost

    def __call__(self, input: Documents) -> Embeddings:
        batch = self.adapter.embed(list(input))
        self._cost += batch.cost_usd
        return [np.array(vector, dtype=np.float32) for vector in batch.vectors]

    @staticmethod
    def name() -> str:
        return "cod_doc_adapter"

    def default_space(self) -> Space:
        return "cosine"

    def get_config(self) -> dict[str, Any]:
        """Конфиг пишется в chroma.sqlite3 открытым текстом — ключа тут нет.

        Та же конвенция, что у стоковой ``OpenAIEmbeddingFunction``: наружу
        уходит имя переменной окружения, а не сам секрет.
        """
        return {
            "backend": self._settings.backend,
            "model_name": self._settings.model,
            "api_base": self._settings.base_url,
            "dimensions": self._settings.dimensions,
            "batch_size": self._settings.batch_size,
            "api_key_env_var": _API_KEY_ENV_VAR,
        }

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> EmbeddingFunction[Documents]:
        import os

        return AdapterEmbeddingFunction(
            EmbeddingSettings(
                backend=str(config.get("backend", "openai")),
                api_key=os.environ.get(str(config.get("api_key_env_var", _API_KEY_ENV_VAR)), ""),
                base_url=str(config.get("api_base", "")),
                model=str(config.get("model_name", "")),
                dimensions=config.get("dimensions"),
                batch_size=int(config.get("batch_size", EmbeddingSettings().batch_size)),
            )
        )

    def validate_config_update(
        self,
        old_config: dict[str, Any],
        new_config: dict[str, Any],
    ) -> None:
        """Смена модели или размерности делает старые векторы несравнимыми."""
        from cod_doc.core.embeddings.errors import EmbeddingDimensionMismatch

        for key in ("model_name", "dimensions", "backend"):
            if old_config.get(key) != new_config.get(key):
                raise EmbeddingDimensionMismatch(
                    f"Эмбеддер коллекции изменился ({key}: "
                    f"{old_config.get(key)!r} → {new_config.get(key)!r}).",
                    hint="cod-doc embed reset --yes, затем переиндексируйте проект.",
                )
