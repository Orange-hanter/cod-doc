"""Реестр embeddings-адаптеров (ADO-071).

Устройство скопировано с ``agent/adapters/registry.py``: словарь фабрик,
ленивые импорты SDK внутри фабрик, внешние плагины из
``config_dir()/embeddings.json`` (формат
``[{"name": "...", "module": "...", "class": "..."}]``, инстанс через
``cls.from_settings``), best-effort загрузка через ``warnings.warn``.

Смысл ровно тот же, что у LLM-адаптеров: добавить провайдера эмбеддингов
можно, не трогая ни ``core/reindex.py``, ни шесть вызывающих.

Usage::

    from cod_doc.core.embeddings import get_adapter_from_settings, settings_from_config

    adapter = get_adapter_from_settings(settings_from_config(Config.load()))
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, cast

from cod_doc.core.embeddings.errors import EmbeddingConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

    from cod_doc.core.embeddings.base import EmbeddingAdapter
    from cod_doc.core.embeddings.settings import EmbeddingSettings


_REGISTRY: dict[str, Callable[[EmbeddingSettings], EmbeddingAdapter]] = {}


def register_embedding_adapter(
    name: str, factory: Callable[[EmbeddingSettings], EmbeddingAdapter]
) -> None:
    """Зарегистрировать фабрику под именем. Перезаписывает прежнюю регистрацию."""
    _REGISTRY[name] = factory


def list_embedding_adapters() -> list[str]:
    """Имена всех зарегистрированных адаптеров (включая внешние плагины)."""
    _load_plugins()
    return sorted(_REGISTRY)


def get_embedding_adapter(name: str, settings: EmbeddingSettings) -> EmbeddingAdapter:
    """Создать адаптер по имени.

    Неизвестное имя — структурная ошибка конфига: raise со списком допустимых
    (правило репозитория), а не тихий фолбэк на дефолт.
    """
    _load_plugins()
    factory = _REGISTRY.get(name)
    if factory is None:
        raise EmbeddingConfigError(
            f"Неизвестный embedding_backend {name!r}.",
            hint=(
                f"Допустимые: {', '.join(sorted(_REGISTRY))}. "
                "Внешние адаптеры регистрируются в ~/.cod-doc/embeddings.json."
            ),
        )
    return factory(settings)


def get_adapter_from_settings(settings: EmbeddingSettings) -> EmbeddingAdapter:
    """Адаптер, выбранный полем ``embedding_backend``."""
    return get_embedding_adapter(settings.backend, settings)


# --------------------------------------------------------------------------- #
# Внешние плагины                                                               #
# --------------------------------------------------------------------------- #

_plugins_loaded = False


def _load_plugins() -> None:
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    # ADO-068: через config_dir(), а не Path.home() — иначе COD_DOC_HOME
    # игнорируется и тесты читают реестр пользователя.
    from cod_doc.config import config_dir

    plugin_file = config_dir() / "embeddings.json"
    if not plugin_file.exists():
        return
    import json

    try:
        entries = json.loads(plugin_file.read_text())
        for entry in entries:
            module = importlib.import_module(entry["module"])
            adapter_cls = getattr(module, entry["class"])
            # Регистрируем сразу связанный classmethod — так в замыкание не
            # утекает переменная цикла (B023), а mypy видит конкретный тип.
            factory = cast(
                "Callable[[EmbeddingSettings], EmbeddingAdapter]", adapter_cls.from_settings
            )
            register_embedding_adapter(entry["name"], factory)
    except Exception as exc:  # плагины — best-effort, как у LLM-адаптеров
        import warnings

        warnings.warn(
            f"Failed to load embedding adapter plugins from {plugin_file}: {exc}",
            stacklevel=2,
        )


def reset_plugins_for_tests() -> None:
    """Сбросить флаг однократной загрузки плагинов (только для тестов)."""
    global _plugins_loaded
    _plugins_loaded = False


# --------------------------------------------------------------------------- #
# Встроенные регистрации                                                        #
# --------------------------------------------------------------------------- #


def _openai_factory(settings: EmbeddingSettings) -> EmbeddingAdapter:
    from cod_doc.core.embeddings.openai_compat import OpenAICompatEmbeddingAdapter

    return OpenAICompatEmbeddingAdapter(settings)


def _openrouter_factory(settings: EmbeddingSettings) -> EmbeddingAdapter:
    from cod_doc.core.embeddings.openrouter import OpenRouterEmbeddingAdapter

    return OpenRouterEmbeddingAdapter(settings)


def _local_factory(settings: EmbeddingSettings) -> EmbeddingAdapter:
    from cod_doc.core.embeddings.local import LocalEmbeddingAdapter

    return LocalEmbeddingAdapter(settings)


def _mock_factory(settings: EmbeddingSettings) -> EmbeddingAdapter:
    from cod_doc.core.embeddings.mock import MockEmbeddingAdapter

    return MockEmbeddingAdapter(settings)


register_embedding_adapter("openai", _openai_factory)
register_embedding_adapter("openrouter", _openrouter_factory)
register_embedding_adapter("local", _local_factory)
register_embedding_adapter("mock", _mock_factory)
