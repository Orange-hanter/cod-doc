"""ADO-071: мост адаптер → chromadb.EmbeddingFunction."""

from __future__ import annotations

import pytest

from cod_doc.core.embeddings import EmbeddingError, EmbeddingSettings
from cod_doc.core.embeddings.chroma_ef import AdapterEmbeddingFunction


def _ef(**kwargs) -> AdapterEmbeddingFunction:
    return AdapterEmbeddingFunction(EmbeddingSettings(backend="mock", **kwargs))


def test_ef_is_registered_under_stable_name() -> None:
    from chromadb.utils.embedding_functions import known_embedding_functions

    assert AdapterEmbeddingFunction.name() == "cod_doc_adapter"
    assert "cod_doc_adapter" in known_embedding_functions


def test_ef_is_not_legacy() -> None:
    """Без name/get_config/build_from_config chroma считает EF legacy."""
    assert _ef().is_legacy() is False


def test_get_config_omits_api_key() -> None:
    ef = AdapterEmbeddingFunction(
        EmbeddingSettings(backend="openrouter", api_key="sk-or-secret", base_url="https://x")
    )
    config = ef.get_config()
    assert "sk-or-secret" not in str(config)
    assert config["api_key_env_var"] == "COD_DOC_EMBEDDING_API_KEY"


def test_build_from_config_roundtrip(monkeypatch) -> None:
    monkeypatch.setenv("COD_DOC_EMBEDDING_API_KEY", "sk-from-env")
    original = AdapterEmbeddingFunction(
        EmbeddingSettings(
            backend="openrouter",
            api_key="sk-or",
            base_url="https://openrouter.ai/api/v1",
            model="qwen/qwen3-embedding-8b",
            dimensions=2048,
        )
    )
    restored = AdapterEmbeddingFunction.build_from_config(original.get_config())
    assert isinstance(restored, AdapterEmbeddingFunction)
    assert restored.get_config()["model_name"] == "qwen/qwen3-embedding-8b"
    assert restored.get_config()["dimensions"] == 2048


def test_default_space_is_cosine() -> None:
    assert _ef().default_space() == "cosine"


def test_call_returns_float32_vectors() -> None:
    vectors = _ef()(["hello", "world"])
    assert len(vectors) == 2
    assert vectors[0].dtype.name == "float32"


def test_cost_is_accumulated_across_calls() -> None:
    ef = _ef()
    ef(["a"])
    ef(["b"])
    assert ef.consumed_cost_usd == 0  # mock бесплатен, но счётчик существует


def test_unknown_backend_fails_at_construction() -> None:
    """Ошибка конфига обязана падать при открытии коллекции, а не в поиске."""
    with pytest.raises(EmbeddingError):
        AdapterEmbeddingFunction(EmbeddingSettings(backend="nope"))


def test_validate_config_update_rejects_model_change() -> None:
    ef = _ef()
    old = ef.get_config()
    new = dict(old, model_name="other-model")
    with pytest.raises(EmbeddingError, match="изменился"):
        ef.validate_config_update(old, new)
