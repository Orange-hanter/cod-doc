"""AdapterRegistry — select and instantiate LLM adapters (PCA-302, proposal 10).

Built-in adapters are registered at import time. External adapters can
be loaded from ``~/.cod-doc/adapters.json`` (Phase 2 plugin mechanism —
format: ``[{"name": "my-adapter", "module": "my_pkg.adapter", "class": "MyAdapter"}]``).

Usage::

    from cod_doc.agent.adapters.registry import get_adapter

    adapter = get_adapter("openai_compat", config)
    adapter = get_adapter("anthropic", config)
    adapter = get_adapter("mock", config)   # tests only
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from cod_doc.agent.adapters.base import LLMAdapter
    from cod_doc.config import Config


# Dict of name → factory(config) → adapter instance.
_REGISTRY: dict[str, Callable[[Any], LLMAdapter]] = {}


def register_adapter(name: str, factory: Callable[[Any], LLMAdapter]) -> None:
    """Register a factory under ``name``.  Overwrites any prior registration."""
    _REGISTRY[name] = factory


def list_adapters() -> list[str]:
    """Return the names of all registered adapters."""
    return sorted(_REGISTRY)


def get_adapter(name: str, config: Config) -> LLMAdapter:
    """Instantiate the adapter registered under ``name``.

    Raises
    ------
    KeyError
        If ``name`` is not in the registry (and the external plugin file
        doesn't define it either).
    """
    _load_plugins()
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown LLM adapter {name!r}. "
            f"Available: {list_adapters()}. "
            "Check 'llm_adapter' in your config or ~/.cod-doc/adapters.json."
        )
    return _REGISTRY[name](config)


def get_adapter_from_config(config: Config) -> LLMAdapter:
    """Select the adapter named in ``config.llm_adapter`` (default 'openai_compat')."""
    name = getattr(config, "llm_adapter", "openai_compat") or "openai_compat"
    return get_adapter(name, config)


# --------------------------------------------------------------------------- #
# Plugin loader (Phase 2 extension point)                                      #
# --------------------------------------------------------------------------- #

_plugins_loaded = False


def _load_plugins() -> None:
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    # ADO-068: через config_dir(), а не Path.home() — иначе COD_DOC_HOME
    # игнорируется и тесты читают реестр адаптеров пользователя.
    from cod_doc.config import config_dir

    plugin_file = config_dir() / "adapters.json"
    if not plugin_file.exists():
        return
    import json

    try:
        entries = json.loads(plugin_file.read_text())
        for entry in entries:
            name = entry["name"]
            module_path = entry["module"]
            class_name = entry["class"]
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)

            def _factory(cfg: Any, adapter_cls: Any = cls) -> LLMAdapter:
                return cast("LLMAdapter", adapter_cls.from_config(cfg))

            register_adapter(name, _factory)
    except Exception as exc:  # plugin loading is best-effort (covered by test_degraded_paths)
        import warnings

        warnings.warn(f"Failed to load adapter plugins from {plugin_file}: {exc}", stacklevel=2)


# --------------------------------------------------------------------------- #
# Built-in registrations                                                        #
# --------------------------------------------------------------------------- #


def _openai_factory(config: Config) -> LLMAdapter:
    from cod_doc.agent.adapters.openai_compat import OpenAICompatAdapter

    return OpenAICompatAdapter(api_key=config.api_key, base_url=config.base_url)


def _anthropic_factory(config: Config) -> LLMAdapter:
    from cod_doc.agent.adapters.anthropic import AnthropicAdapter

    # The Anthropic adapter uses its own API key; fall back to the generic
    # api_key when a dedicated anthropic_api_key is not configured.
    api_key = getattr(config, "anthropic_api_key", None) or config.api_key
    return AnthropicAdapter(api_key=api_key)


def _mock_factory(config: Config) -> LLMAdapter:
    from cod_doc.agent.adapters.mock import MockAdapter

    return MockAdapter()


register_adapter("openai_compat", _openai_factory)
register_adapter("anthropic", _anthropic_factory)
register_adapter("mock", _mock_factory)
