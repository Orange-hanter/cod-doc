"""ADO-071: реестр embeddings-адаптеров (аналог tests/test_adapters.py для LLM)."""

from __future__ import annotations

import json

import pytest

from cod_doc.core.embeddings import (
    EmbeddingAdapter,
    EmbeddingBatch,
    EmbeddingCapabilities,
    EmbeddingError,
    EmbeddingSettings,
    get_adapter_from_settings,
    get_embedding_adapter,
    list_embedding_adapters,
    register_embedding_adapter,
    supports_catalog,
)
from cod_doc.core.embeddings.base import ProbeResult
from cod_doc.core.embeddings.registry import reset_plugins_for_tests


def test_builtin_adapters_registered() -> None:
    names = list_embedding_adapters()
    assert {"openai", "openrouter", "local", "mock"} <= set(names)


def test_unknown_adapter_raises_with_allowed_list() -> None:
    with pytest.raises(EmbeddingError) as excinfo:
        get_embedding_adapter("nope", EmbeddingSettings(backend="nope"))
    message = str(excinfo.value)
    assert "nope" in message
    assert "openrouter" in message


def test_mock_adapter_conforms_to_protocol() -> None:
    adapter = get_adapter_from_settings(EmbeddingSettings(backend="mock"))
    assert isinstance(adapter, EmbeddingAdapter)
    batch = adapter.embed(["a", "b"])
    assert isinstance(batch, EmbeddingBatch)
    assert len(batch.vectors) == 2
    assert batch.dimensions > 0


def test_mock_adapter_is_deterministic() -> None:
    adapter = get_adapter_from_settings(EmbeddingSettings(backend="mock"))
    assert adapter.embed(["same"]).vectors == adapter.embed(["same"]).vectors


def test_openrouter_adapter_declares_dimensions_and_catalog() -> None:
    adapter = get_adapter_from_settings(
        EmbeddingSettings(backend="openrouter", api_key="sk-or", base_url="https://x")
    )
    assert adapter.capabilities.dimensions_param is True
    assert adapter.capabilities.cost_reporting is True
    assert supports_catalog(adapter) is True


def test_mock_adapter_has_no_catalog() -> None:
    adapter = get_adapter_from_settings(EmbeddingSettings(backend="mock"))
    assert supports_catalog(adapter) is False


def test_custom_adapter_can_be_registered() -> None:
    class CustomAdapter:
        name = "custom"
        capabilities = EmbeddingCapabilities(offline=True)

        def __init__(self, settings: EmbeddingSettings) -> None:
            self.settings = settings

        @classmethod
        def from_settings(cls, settings: EmbeddingSettings) -> CustomAdapter:
            return cls(settings)

        def embed(self, texts):
            return EmbeddingBatch(vectors=[[1.0] for _ in texts], model="custom")

        def probe(self) -> ProbeResult:
            return ProbeResult(ok=True, backend="custom", model="custom", dimensions=1, latency_s=0)

    register_embedding_adapter("custom", CustomAdapter.from_settings)
    try:
        adapter = get_embedding_adapter("custom", EmbeddingSettings(backend="custom"))
        assert adapter.embed(["x"]).vectors == [[1.0]]
    finally:
        from cod_doc.core.embeddings import registry

        registry._REGISTRY.pop("custom", None)


def test_broken_plugin_warns_and_does_not_break_registry(tmp_path, monkeypatch) -> None:
    """Плагины — best-effort: битый файл не должен ронять импорт (как у LLM)."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "embeddings.json").write_text(
        json.dumps([{"name": "ghost", "module": "no_such_module", "class": "X"}])
    )
    monkeypatch.setenv("COD_DOC_HOME", str(home))
    reset_plugins_for_tests()
    try:
        with pytest.warns(UserWarning, match="embeddings.json"):
            names = list_embedding_adapters()
        assert "openai" in names  # встроенные на месте
        assert "ghost" not in names
    finally:
        reset_plugins_for_tests()


def test_plugin_is_registered_from_config_dir(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "embeddings.json").write_text(
        json.dumps(
            [
                {
                    "name": "plugged",
                    "module": "cod_doc.core.embeddings.mock",
                    "class": "MockEmbeddingAdapter",
                }
            ]
        )
    )
    monkeypatch.setenv("COD_DOC_HOME", str(home))
    reset_plugins_for_tests()
    try:
        assert "plugged" in list_embedding_adapters()
        adapter = get_embedding_adapter("plugged", EmbeddingSettings(backend="plugged"))
        assert adapter.embed(["x"]).vectors
    finally:
        from cod_doc.core.embeddings import registry

        registry._REGISTRY.pop("plugged", None)
        reset_plugins_for_tests()
