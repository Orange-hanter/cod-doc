"""ADO-071: правила резолва настроек эмбеддера."""

from __future__ import annotations

from cod_doc.config import Config
from cod_doc.core.embeddings import (
    OPENROUTER_BASE_URL,
    EmbeddingSettings,
    settings_from_config,
)


def _cfg(**kwargs) -> Config:
    base = {
        "api_key": "sk-llm",
        "base_url": "https://ollama.com/v1",
        "embedding_model": "openai/text-embedding-ada-002",
    }
    base.update(kwargs)
    return Config(**base)


def test_defaults_match_legacy_openai_behaviour() -> None:
    """Без новых полей эмбеддер работает ровно как до разделения."""
    settings = settings_from_config(_cfg())
    assert settings.backend == "openai"
    assert settings.api_key == "sk-llm"
    assert settings.base_url == "https://ollama.com/v1"
    assert settings.model == "openai/text-embedding-ada-002"
    assert settings.dimensions is None


def test_openai_backend_still_falls_back_to_llm_key() -> None:
    settings = settings_from_config(_cfg(embedding_base_url="https://proxy/v1"))
    assert settings.api_key == "sk-llm"  # ключ унаследован
    assert settings.base_url == "https://proxy/v1"  # endpoint переопределён


def test_dedicated_key_wins_over_llm_key() -> None:
    settings = settings_from_config(_cfg(embedding_api_key="sk-emb"))
    assert settings.api_key == "sk-emb"


def test_openrouter_backend_does_not_inherit_llm_api_key() -> None:
    """Главное правило ADO-071: ключ OpenRouter ≠ ключ чат-провайдера."""
    settings = settings_from_config(_cfg(embedding_backend="openrouter"))
    assert settings.api_key == ""
    assert settings.is_usable is False


def test_openrouter_backend_defaults_base_url() -> None:
    settings = settings_from_config(_cfg(embedding_backend="openrouter", embedding_api_key="sk-or"))
    assert settings.base_url == OPENROUTER_BASE_URL
    assert settings.is_usable is True


def test_local_backend_ignores_credentials() -> None:
    settings = settings_from_config(_cfg(embedding_backend="local", embedding_api_key="sk-emb"))
    assert settings.api_key == ""
    assert settings.base_url == ""
    assert settings.is_usable is True  # офлайн-бэкенду ключ не нужен


def test_collection_signature_format() -> None:
    settings = EmbeddingSettings(
        backend="openrouter", model="qwen/qwen3-embedding-8b", dimensions=2048
    )
    assert settings.collection_signature == "openrouter:qwen/qwen3-embedding-8b@2048"


def test_collection_signature_native_when_dimensions_unset() -> None:
    settings = EmbeddingSettings(backend="openai", model="m")
    assert settings.collection_signature == "openai:m@native"


def test_redacted_never_contains_api_key() -> None:
    settings = EmbeddingSettings(backend="openrouter", api_key="sk-or-secret")
    dumped = settings.redacted()
    assert "sk-or-secret" not in str(dumped)
    assert dumped["api_key_set"] is True


def test_from_config_reads_missing_fields_via_getattr() -> None:
    """Старый config.yaml без новых полей обязан грузиться."""

    class LegacyConfig:
        api_key = "sk-llm"
        base_url = "https://openrouter.ai/api/v1"
        embedding_model = "openai/text-embedding-ada-002"
        embedding_backend = "openai"

    settings = settings_from_config(LegacyConfig())  # type: ignore[arg-type]
    assert settings.api_key == "sk-llm"
    assert settings.batch_size > 0


def test_dimensions_are_normalised_to_int() -> None:
    settings = settings_from_config(_cfg(embedding_dimensions=2048))
    assert settings.dimensions == 2048
