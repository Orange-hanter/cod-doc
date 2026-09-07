"""COD-043 + ADO-071: выбор embeddings-бэкенда и идентичность коллекции."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

# Импорт до подмены chromadb: chroma_ef тянет настоящие chromadb.api.types,
# а тесты ниже подсовывают в sys.modules фейковый top-level модуль.
import cod_doc.core.embeddings.chroma_ef as _chroma_ef  # noqa: F401
from cod_doc.core import reindex
from cod_doc.core.embeddings import EmbeddingError, EmbeddingSettings
from cod_doc.core.embeddings.errors import EmbeddingDimensionMismatch


def _install_fake_chromadb_module(
    monkeypatch,
    *,
    captured: dict[str, Any],
    existing_metadata: dict[str, Any] | None = None,
    count: int = 0,
) -> None:
    """Stub out the chromadb top-level + embedding_functions submodule.

    We only need to record what get_or_create_collection was called with.
    Tests use this to verify backend wiring without needing chromadb installed.
    """

    class FakeCollection:
        def __init__(self, metadata: dict[str, Any]) -> None:
            self.metadata = metadata

        def count(self) -> int:
            return count

    class FakeClient:
        def __init__(self, path: str) -> None:
            captured["chroma_path"] = path

        def get_or_create_collection(self, **kwargs: Any) -> Any:
            captured["collection_kwargs"] = kwargs
            # chroma игнорирует metadata у существующей коллекции — эмулируем.
            metadata = existing_metadata if existing_metadata is not None else kwargs["metadata"]
            return FakeCollection(metadata)

    fake_chromadb = types.ModuleType("chromadb")
    fake_chromadb.PersistentClient = FakeClient  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb)
    monkeypatch.setattr(reindex, "_client_cache", {})


def _install_openai_ef(monkeypatch, *, captured: dict[str, Any]) -> None:
    class FakeOpenAIEF:
        def __init__(self, **kwargs: Any) -> None:
            captured["openai_ef_kwargs"] = kwargs

    fake_ef_mod = types.ModuleType("chromadb.utils.embedding_functions")
    fake_ef_mod.OpenAIEmbeddingFunction = FakeOpenAIEF  # type: ignore[attr-defined]
    # Caller may also import SentenceTransformerEmbeddingFunction even with
    # backend=openai; provide a stub anyway.

    class FakeStEF:
        def __init__(self, **kwargs: Any) -> None:
            captured["st_ef_kwargs"] = kwargs

    fake_ef_mod.SentenceTransformerEmbeddingFunction = FakeStEF  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "chromadb.utils.embedding_functions", fake_ef_mod)


def test_openai_backend_uses_openai_ef(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(monkeypatch, captured=captured)
    _install_openai_ef(monkeypatch, captured=captured)

    coll = reindex.get_collection(
        "/tmp/x",
        EmbeddingSettings(
            backend="openai",
            api_key="sk-test",
            base_url="https://x",
            model="openai/text-embedding-ada-002",
        ),
    )
    assert coll is not None
    assert captured["chroma_path"] == "/tmp/x"
    # Used the OpenAI EF, not the SentenceTransformer one.
    assert captured["openai_ef_kwargs"]["api_key"] == "sk-test"
    assert captured["openai_ef_kwargs"]["api_base"] == "https://x"
    assert captured["openai_ef_kwargs"]["model_name"] == "openai/text-embedding-ada-002"
    assert "st_ef_kwargs" not in captured


def test_openai_backend_requires_api_key(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(monkeypatch, captured=captured)
    _install_openai_ef(monkeypatch, captured=captured)

    with pytest.raises(EmbeddingError, match="api_key обязателен"):
        reindex.get_collection(
            "/tmp/x",
            EmbeddingSettings(backend="openai", api_key="", base_url="https://x"),
        )


def test_local_backend_uses_sentence_transformer(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(monkeypatch, captured=captured)
    _install_openai_ef(monkeypatch, captured=captured)

    coll = reindex.get_collection(
        "/tmp/x",
        EmbeddingSettings(backend="local", model="all-MiniLM-L6-v2"),
    )
    assert coll is not None
    assert captured["st_ef_kwargs"]["model_name"] == "all-MiniLM-L6-v2"
    # OpenAI EF was NOT instantiated.
    assert "openai_ef_kwargs" not in captured


def test_local_backend_falls_back_to_default_model_for_openai_slug(monkeypatch) -> None:
    """When the user keeps the OpenAI default slug but switches to local,
    we substitute a known-good sentence-transformers id instead of crashing."""
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(monkeypatch, captured=captured)
    _install_openai_ef(monkeypatch, captured=captured)

    reindex.get_collection(
        "/tmp/x",
        EmbeddingSettings(backend="local", model="openai/text-embedding-ada-002"),
    )
    assert captured["st_ef_kwargs"]["model_name"] == "all-MiniLM-L6-v2"


def test_unknown_backend_raises_with_allowed_values(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(monkeypatch, captured=captured)
    _install_openai_ef(monkeypatch, captured=captured)

    with pytest.raises(EmbeddingError) as excinfo:
        reindex.get_collection("/tmp/x", EmbeddingSettings(backend="weird", api_key="sk"))
    message = str(excinfo.value)
    assert "weird" in message
    assert "openrouter" in message  # подсказка перечисляет допустимые


def test_local_backend_missing_dep_surfaces_install_hint(monkeypatch) -> None:
    """If sentence-transformers isn't installed (the EF class doesn't exist),
    the route raises ImportError pointing at the optional extra."""
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(monkeypatch, captured=captured)

    # Provide an embedding_functions module WITHOUT SentenceTransformerEmbeddingFunction.
    fake_ef_mod = types.ModuleType("chromadb.utils.embedding_functions")
    monkeypatch.setitem(sys.modules, "chromadb.utils.embedding_functions", fake_ef_mod)

    with pytest.raises(ImportError, match="embeddings-local"):
        reindex.get_collection(
            "/tmp/x",
            EmbeddingSettings(backend="local", model="all-MiniLM-L6-v2"),
        )


def test_default_backend_is_openai() -> None:
    """Config defaults preserve historical behaviour."""
    from cod_doc.config import Config

    cfg = Config()
    assert cfg.embedding_backend == "openai"


# ── ADO-071: openrouter, подпись коллекции, перевод ошибки размерности ────────


def test_openrouter_backend_uses_adapter_ef(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(monkeypatch, captured=captured)
    _install_openai_ef(monkeypatch, captured=captured)

    reindex.get_collection(
        "/tmp/x",
        EmbeddingSettings(
            backend="openrouter",
            api_key="sk-or-test",
            model="qwen/qwen3-embedding-8b",
            dimensions=2048,
        ),
    )
    ef = captured["collection_kwargs"]["embedding_function"]
    assert type(ef).__name__ == "AdapterEmbeddingFunction"
    # Стоковая EF не использовалась: она не умеет dimensions для этой модели.
    assert "openai_ef_kwargs" not in captured


def test_openrouter_backend_requires_dedicated_key(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(monkeypatch, captured=captured)
    _install_openai_ef(monkeypatch, captured=captured)

    with pytest.raises(EmbeddingError) as excinfo:
        reindex.get_collection("/tmp/x", EmbeddingSettings(backend="openrouter", api_key=""))
    assert "не наследуется" in str(excinfo.value)


def test_collection_created_with_signature_metadata(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(monkeypatch, captured=captured)
    _install_openai_ef(monkeypatch, captured=captured)

    reindex.get_collection(
        "/tmp/x",
        EmbeddingSettings(
            backend="openai",
            api_key="sk",
            model="openai/text-embedding-3-small",
            dimensions=512,
        ),
    )
    metadata = captured["collection_kwargs"]["metadata"]
    assert metadata[reindex.SIGNATURE_KEY] == "openai:openai/text-embedding-3-small@512"
    assert metadata["hnsw:space"] == "cosine"


def test_signature_mismatch_on_nonempty_collection_raises(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(
        monkeypatch,
        captured=captured,
        existing_metadata={reindex.SIGNATURE_KEY: "openrouter:qwen/qwen3-embedding-8b@2048"},
        count=42,
    )
    _install_openai_ef(monkeypatch, captured=captured)

    with pytest.raises(EmbeddingDimensionMismatch) as excinfo:
        reindex.get_collection("/tmp/x", EmbeddingSettings(backend="openai", api_key="sk"))
    assert "embed reset" in str(excinfo.value)


def test_signature_mismatch_on_empty_collection_is_allowed(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(
        monkeypatch,
        captured=captured,
        existing_metadata={reindex.SIGNATURE_KEY: "openrouter:other@2048"},
        count=0,
    )
    _install_openai_ef(monkeypatch, captured=captured)

    assert reindex.get_collection("/tmp/x", EmbeddingSettings(backend="openai", api_key="sk"))


def test_legacy_collection_without_signature_is_accepted(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(
        monkeypatch,
        captured=captured,
        existing_metadata={"hnsw:space": "cosine"},
        count=17,
    )
    _install_openai_ef(monkeypatch, captured=captured)

    assert reindex.get_collection("/tmp/x", EmbeddingSettings(backend="openai", api_key="sk"))


def test_chroma_dimension_error_is_translated() -> None:
    from chromadb.errors import InvalidArgumentError

    raw = InvalidArgumentError("Collection expecting embedding with dimension of 1536, got 2048")
    translated = reindex._translate_chroma_error(raw, EmbeddingSettings(backend="openrouter"))
    assert isinstance(translated, EmbeddingDimensionMismatch)
    assert "embed reset" in str(translated)


def test_unrelated_chroma_error_is_not_relabelled() -> None:
    from chromadb.errors import InvalidArgumentError

    raw = InvalidArgumentError("Expected a name containing 3-512 characters")
    assert reindex._translate_chroma_error(raw, EmbeddingSettings()) is raw
