"""
Переиндексация документов проекта в ChromaDB.
vector_id формат: doc:{sanitized_relative_path}
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger("cod_doc.core.reindex")

INDEX_DIRS = ("specs", "arch", "models", "docs")
INDEX_EXTENSIONS = {".md", ".yaml", ".yml", ".json", ".txt"}
CHUNK_SIZE = 8000  # символов — лимит ChromaDB на документ


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


def get_collection(chroma_path: str):
    """Получить или создать ChromaDB коллекцию. Ленивый импорт."""
    try:
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except ImportError as e:
        raise ImportError(
            "chromadb не установлен. Выполните: pip install chromadb sentence-transformers"
        ) from e

    client = chromadb.PersistentClient(path=chroma_path)
    ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    return client.get_or_create_collection(
        name="cod_doc",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


def reindex_project(
    repo_root: Path,
    chroma_path: str,
    single_file: Path | None = None,
) -> dict:
    """
    Проиндексировать файлы проекта в ChromaDB.
    Возвращает {'indexed': int, 'errors': list[str]}.
    """
    collection = get_collection(chroma_path)
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

    # Upsert пакетами по 50
    for i in range(0, len(batch_ids), 50):
        collection.upsert(
            ids=batch_ids[i : i + 50],
            documents=batch_docs[i : i + 50],
            metadatas=batch_metas[i : i + 50],
        )
        indexed += min(50, len(batch_ids) - i)
        logger.debug(f"Upserted {indexed}/{len(batch_ids)}")

    return {"indexed": indexed, "errors": errors}


def search_documents(
    query: str,
    chroma_path: str,
    project_root: str | None = None,
    n_results: int = 5,
) -> list[dict]:
    """
    Семантический поиск по проиндексированным документам.

    Returns список dict: {path, score, snippet, hash}.
    """
    collection = get_collection(chroma_path)
    where = {"project": project_root} if project_root else None

    try:
        results = collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        logger.warning(f"ChromaDB query error: {e}")
        return []

    hits = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for doc, meta, dist in zip(docs, metas, distances):
        hits.append({
            "path": meta.get("path", ""),
            "hash": meta.get("hash", ""),
            "score": round(1 - dist, 4),  # cosine similarity
            "snippet": doc[:300],
        })

    return hits
