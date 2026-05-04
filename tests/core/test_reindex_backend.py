"""COD-043: backend selection for ChromaDB embeddings (openai vs local)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from cod_doc.core import reindex


def _install_fake_chromadb_module(monkeypatch, *, captured: dict[str, Any]) -> None:
    """Stub out the chromadb top-level + embedding_functions submodule.

    We only need to record what get_or_create_collection was called with.
    Tests use this to verify backend wiring without needing chromadb installed.
    """
    fake_collection = object()

    class FakeClient:
        def __init__(self, path: str) -> None:
            captured["chroma_path"] = path

        def get_or_create_collection(self, **kwargs: Any) -> Any:
            captured["collection_kwargs"] = kwargs
            return fake_collection

    fake_chromadb = types.ModuleType("chromadb")
    fake_chromadb.PersistentClient = FakeClient  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb)


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
        chroma_path="/tmp/x",
        api_key="sk-test",
        base_url="https://x",
        embedding_model="openai/text-embedding-ada-002",
        embedding_backend="openai",
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

    with pytest.raises(ValueError, match="api_key обязателен"):
        reindex.get_collection(
            chroma_path="/tmp/x",
            api_key="",
            base_url="https://x",
            embedding_model="openai/text-embedding-ada-002",
            embedding_backend="openai",
        )


def test_local_backend_uses_sentence_transformer(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(monkeypatch, captured=captured)
    _install_openai_ef(monkeypatch, captured=captured)

    coll = reindex.get_collection(
        chroma_path="/tmp/x",
        api_key="",  # not required for local
        base_url="ignored",
        embedding_model="all-MiniLM-L6-v2",
        embedding_backend="local",
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
        chroma_path="/tmp/x",
        api_key="",
        base_url="",
        embedding_model="openai/text-embedding-ada-002",
        embedding_backend="local",
    )
    assert captured["st_ef_kwargs"]["model_name"] == "all-MiniLM-L6-v2"


def test_unknown_backend_raises(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_chromadb_module(monkeypatch, captured=captured)
    _install_openai_ef(monkeypatch, captured=captured)

    with pytest.raises(ValueError, match="Unknown embedding_backend"):
        reindex.get_collection(
            chroma_path="/tmp/x",
            api_key="sk",
            base_url="x",
            embedding_model="m",
            embedding_backend="weird",
        )


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
            chroma_path="/tmp/x",
            api_key="",
            base_url="",
            embedding_model="all-MiniLM-L6-v2",
            embedding_backend="local",
        )


def test_default_backend_is_openai() -> None:
    """Config defaults preserve historical behaviour."""
    from cod_doc.config import Config

    cfg = Config()
    assert cfg.embedding_backend == "openai"
