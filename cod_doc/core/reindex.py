"""
Переиндексация документов проекта в ChromaDB.
vector_id формат: doc:{sanitized_relative_path}

ADO-071: провайдер эмбеддингов больше не наследует ключ и endpoint у LLM —
всё, что нужно векторизатору, приезжает одним объектом ``EmbeddingSettings``.
"""

from __future__ import annotations

import hashlib
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypedDict

from cod_doc.core.embeddings.errors import EmbeddingDimensionMismatch, EmbeddingError
from cod_doc.core.embeddings.settings import (
    BACKEND_LOCAL,
    BACKEND_OPENAI,
    EmbeddingSettings,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("cod_doc.core.reindex")

INDEX_DIRS = ("specs", "arch", "models", "docs")
INDEX_EXTENSIONS = {".md", ".yaml", ".yml", ".json", ".txt"}
CHUNK_SIZE = 8000  # символов — лимит ChromaDB на документ
UPSERT_BATCH_SIZE = 50

COLLECTION_NAME = "cod_doc"
SIGNATURE_KEY = "cod_doc:embedding"
"""Отпечаток эмбеддера в metadata коллекции. chroma игнорирует metadata при
открытии существующей коллекции, поэтому подпись нельзя незаметно переписать."""


class ReindexResult(TypedDict):
    """Итог переиндексации: сколько записали, что сломалось, сколько стоило."""

    indexed: int
    errors: list[str]
    cost_usd: str


class SearchHit(TypedDict):
    """Одно попадание семантического поиска."""

    path: str
    hash: str
    score: float
    snippet: str


_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    """WARNING один раз на процесс, дальше DEBUG.

    Поиск зовётся из daemon-цикла: при сломанном ключе прежний код писал
    предупреждение на каждый вызов, и настоящая поломка тонула в шуме — ровно
    поэтому её никто не замечал месяцами.
    """
    if key in _warned:
        logger.debug(message)
        return
    _warned.add(key)
    logger.warning(message)


def _calc_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _sanitize_id(path: Path, repo_root: Path) -> str:
    rel = path.relative_to(repo_root)
    return "doc:" + str(rel).replace("/", "_").replace("\\", "_").replace(".", "_")


def _collect_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for d in INDEX_DIRS:
        target = repo_root / d
        if not target.exists():
            continue
        for f in sorted(target.rglob("*")):
            if f.is_file() and f.suffix in INDEX_EXTENSIONS:
                files.append(f)
    return files


def _build_embedding_function(settings: EmbeddingSettings) -> Any:
    """Выбрать chroma-EF под настройки эмбеддера.

    - ``local``: sentence-transformers через стоковую EF (офлайн, COD-043);
    - ``openai``: стоковая ``OpenAIEmbeddingFunction`` — обратная совместимость
      с коллекциями, построенными до ADO-071;
    - всё остальное (``openrouter``, внешние плагины): наш адаптер, который
      умеет ``dimensions`` для любых моделей, батч, ретраи и разбор ошибок
      провайдера.
    """
    if settings.backend == BACKEND_LOCAL:
        try:
            from chromadb.utils.embedding_functions import (
                SentenceTransformerEmbeddingFunction,
            )
        except ImportError as exc:
            raise ImportError(
                "Local embeddings require sentence-transformers. Install with: "
                "pip install 'cod-doc[embeddings-local]'"
            ) from exc
        from cod_doc.core.embeddings.local import resolve_local_model

        return SentenceTransformerEmbeddingFunction(model_name=resolve_local_model(settings.model))

    if settings.backend == BACKEND_OPENAI:
        from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

        if not settings.api_key:
            raise EmbeddingError(
                "api_key обязателен для embedding_backend='openai' (OpenAI-совместимый endpoint).",
                hint=(
                    "Задайте embedding_api_key (или общий api_key), либо "
                    "переключите embedding_backend на 'openrouter' / 'local'."
                ),
            )
        return OpenAIEmbeddingFunction(
            api_key=settings.api_key,
            api_base=settings.base_url,
            model_name=settings.model,
        )

    # Импорт здесь, а не в пакете: chroma_ef тянет chromadb, а диагностике и
    # резолву настроек векторное хранилище не нужно.
    from cod_doc.core.embeddings.chroma_ef import AdapterEmbeddingFunction

    return AdapterEmbeddingFunction(settings)


# COD-077: cache PersistentClient instances keyed by chroma_path. Without
# this, every search reopens the on-disk index — chromadb's docs explicitly
# recommend reusing the client. We cache process-wide; the typical workload
# is a handful of distinct chroma_path values per server lifetime.
_client_cache: dict[str, Any] = {}


def _assert_signature(collection: Any, settings: EmbeddingSettings) -> None:
    """Проверить, что коллекцию строил тот же эмбеддер.

    Пустую коллекцию и коллекцию без подписи (создана до ADO-071) пропускаем:
    терять нечего, подпись проставится при следующем создании.
    """
    stored = (collection.metadata or {}).get(SIGNATURE_KEY)
    if stored in (None, settings.collection_signature):
        return
    if collection.count() == 0:
        return
    raise EmbeddingDimensionMismatch(
        f"Коллекция {COLLECTION_NAME!r} построена эмбеддером {stored!r}, "
        f"а конфиг требует {settings.collection_signature!r}: векторы несовместимы.",
        hint="cod-doc embed reset --yes, затем переиндексируйте проект.",
    )


def get_collection(chroma_path: str, settings: EmbeddingSettings) -> Any:
    """Получить или создать ChromaDB коллекцию под заданный эмбеддер."""
    try:
        import chromadb  # noqa: F401
    except ImportError as e:
        raise ImportError("chromadb не установлен. Выполните: pip install chromadb") from e

    import chromadb as _chromadb

    ef = _build_embedding_function(settings)
    client = _client_cache.get(chroma_path)
    if client is None:
        client = _chromadb.PersistentClient(path=chroma_path)
        _client_cache[chroma_path] = client
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={
            "hnsw:space": "cosine",
            SIGNATURE_KEY: settings.collection_signature,
        },
    )
    _assert_signature(collection, settings)
    return collection


def _translate_chroma_error(exc: Exception, settings: EmbeddingSettings) -> Exception:
    """Сырая ошибка размерности chroma → внятная ошибка с подсказкой.

    Для legacy-коллекций без подписи это единственный сигнал рассинхрона.
    Ловим по классу и переспрашиваем текст, чтобы не мислейблить посторонние
    ``InvalidArgumentError``.
    """
    from chromadb.errors import InvalidArgumentError

    if isinstance(exc, InvalidArgumentError) and "dimension" in str(exc):
        return EmbeddingDimensionMismatch(
            f"Размерность векторов не совпадает с коллекцией: {exc}",
            hint=(
                f"Текущий эмбеддер — {settings.collection_signature}. "
                "cod-doc embed reset --yes, затем переиндексируйте проект."
            ),
        )
    return exc


def reindex_project(
    repo_root: Path,
    chroma_path: str,
    settings: EmbeddingSettings,
    *,
    single_file: Path | None = None,
) -> ReindexResult:
    """
    Проиндексировать файлы проекта в ChromaDB.

    В отличие от поиска, здесь ошибки эмбеддера **не глушатся**: явная
    переиндексация, молча записавшая ноль документов, — худший исход.
    """
    collection = get_collection(chroma_path, settings)
    files = [single_file] if single_file else _collect_files(repo_root)
    indexed = 0
    errors: list[str] = []

    batch_ids, batch_docs, batch_metas = [], [], []

    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")[:CHUNK_SIZE]
            h = _calc_hash(f)
            vid = _sanitize_id(f, repo_root)
            rel = str(f.relative_to(repo_root))
            batch_ids.append(vid)
            batch_docs.append(content)
            batch_metas.append({"path": rel, "hash": h, "project": str(repo_root)})
        except Exception as e:
            errors.append(f"{f}: {e}")

    for i in range(0, len(batch_ids), UPSERT_BATCH_SIZE):
        try:
            collection.upsert(
                ids=batch_ids[i : i + UPSERT_BATCH_SIZE],
                documents=batch_docs[i : i + UPSERT_BATCH_SIZE],
                metadatas=batch_metas[i : i + UPSERT_BATCH_SIZE],
            )
        except Exception as exc:
            raise _translate_chroma_error(exc, settings) from exc
        indexed += min(UPSERT_BATCH_SIZE, len(batch_ids) - i)
        logger.debug(f"Upserted {indexed}/{len(batch_ids)}")

    cost = getattr(collection._embedding_function, "consumed_cost_usd", Decimal(0))
    return {"indexed": indexed, "errors": errors, "cost_usd": str(cost)}


def search_documents(
    query: str,
    chroma_path: str,
    settings: EmbeddingSettings,
    *,
    project_root: str | None = None,
    n_results: int = 5,
) -> list[SearchHit]:
    """
    Семантический поиск по проиндексированным документам.

    Fail-open: любая ошибка бэкенда — пустой список, потому что поиск
    вызывается из read-only путей (ContextService L3, link-suggest), где
    падение хуже пустоты. Громкий сигнал даёт ``cod-doc embed status/probe``.
    """
    try:
        collection = get_collection(chroma_path, settings)
    except Exception as e:
        _warn_once(f"collection:{settings.collection_signature}", f"ChromaDB unavailable: {e}")
        return []

    where = {"project": project_root} if project_root else None

    try:
        results = collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        _warn_once(
            f"query:{settings.collection_signature}",
            f"ChromaDB query error: {_translate_chroma_error(e, settings)}",
        )
        return []

    hits: list[SearchHit] = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for doc, meta, dist in zip(docs, metas, distances, strict=False):
        hits.append(
            {
                "path": meta.get("path", ""),
                "hash": meta.get("hash", ""),
                "score": round(1 - dist, 4),  # cosine similarity
                "snippet": doc[:300],
            }
        )

    return hits
