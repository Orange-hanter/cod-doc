#!/usr/bin/env python3
"""
reindex.py — Переиндексация документов в ChromaDB + обновление хэшей в MASTER.md.

Использование:
  python tools/reindex.py              # Полная переиндексация
  python tools/reindex.py --dry-run    # Показать план без изменений
  python tools/reindex.py --file /specs/auth.md  # Переиндексировать один файл

Зависимости: chromadb, sentence-transformers (см. requirements.txt)
"""

import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_MD = REPO_ROOT / "MASTER.md"
CHROMA_PATH = REPO_ROOT / ".chroma"

# Директории для индексации
INDEX_DIRS = ["specs", "arch", "models", "docs"]
INDEX_EXTENSIONS = {".md", ".yaml", ".yml", ".json", ".txt"}


def _calc_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _sanitize_id(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT)
    return "doc:" + str(rel).replace("/", "_").replace("\\", "_").replace(".", "_")


def _collect_files() -> list[Path]:
    files = []
    for d in INDEX_DIRS:
        target = REPO_ROOT / d
        if not target.exists():
            continue
        for f in sorted(target.rglob("*")):
            if f.is_file() and f.suffix in INDEX_EXTENSIONS:
                files.append(f)
    return files


def reindex(dry_run: bool = False, single_file: str | None = None) -> None:
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError:
        print(
            "ERROR: chromadb не установлен.\n"
            "Выполните: pip install chromadb sentence-transformers",
            file=sys.stderr,
        )
        sys.exit(1)

    files = _collect_files()
    if single_file:
        target = REPO_ROOT / single_file.lstrip("/")
        files = [f for f in files if f == target]
        if not files:
            print(f"ERROR: файл не найден или не индексируется: {single_file}", file=sys.stderr)
            sys.exit(1)

    print(f"📂 Найдено файлов для индексации: {len(files)}")

    if dry_run:
        for f in files:
            h = _calc_hash(f)
            vid = _sanitize_id(f)
            print(f"  {vid}  sha:{h}  {f.relative_to(REPO_ROOT)}")
        print("(dry-run: изменений не внесено)")
        return

    # --- ChromaDB ---
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    collection = client.get_or_create_collection(
        name="cod_doc",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    ids, documents, metadatas = [], [], []
    for f in files:
        content = f.read_text(encoding="utf-8", errors="replace")
        h = _calc_hash(f)
        vid = _sanitize_id(f)
        rel = str(f.relative_to(REPO_ROOT))
        ids.append(vid)
        documents.append(content[:8000])  # ChromaDB лимит
        metadatas.append({"path": rel, "hash": h})

    # Upsert пакетами по 100
    batch = 100
    for i in range(0, len(ids), batch):
        collection.upsert(
            ids=ids[i : i + batch],
            documents=documents[i : i + batch],
            metadatas=metadatas[i : i + batch],
        )
        print(f"  ✅ Upserted {min(i + batch, len(ids))}/{len(ids)}")

    print(f"\n✅ Переиндексировано: {len(ids)} документов → {CHROMA_PATH}")

    # --- Обновить хэши в MASTER.md ---
    print("🔄 Обновление хэшей в MASTER.md...")
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from hash_calc import update_master  # noqa: PLC0415
    n, warns = update_master(MASTER_MD)
    for w in warns:
        print(f"  {w}", file=sys.stderr)
    print(f"✅ Хэшей обновлено: {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Только показать план")
    parser.add_argument("--file", metavar="PATH", help="Переиндексировать один файл")
    args = parser.parse_args()
    reindex(dry_run=args.dry_run, single_file=args.file)


if __name__ == "__main__":
    main()
