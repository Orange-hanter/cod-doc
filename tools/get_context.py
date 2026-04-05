#!/usr/bin/env python3
"""
get_context.py — Файловый шлюз доставки контекста агентам (COD-DOC).

Реализует MCP-подобный протокол раздела 4 спецификации.

Использование (CLI):
  python tools/get_context.py "📁 /specs/auth.md | 🗃️ doc:specs_auth | 🔑 sha:a1b2c3d4e5f6"
  python tools/get_context.py --ref "📁 /specs/auth.md | 🗃️ doc:specs_auth | 🔑 sha:a1b2c3d4e5f6"
  python tools/get_context.py --list          # Показать все доступные документы
  python tools/get_context.py --depth L2 ...  # Загрузить с зависимостями

Использование (Python API):
  from tools.get_context import get_context
  result = get_context("📁 /specs/auth.md | 🗃️ doc:specs_auth | 🔑 sha:a1b2c3d4e5f6")
"""

import hashlib
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER_MD = REPO_ROOT / "MASTER.md"

# Шаблон гибридной ссылки
REF_PATTERN = re.compile(
    r"📁\s+(?P<path>\S+)\s+\|\s+🗃️\s+(?P<vec_id>\S+)\s+\|\s+🔑\s+sha:(?P<hash>[0-9a-f]{12})"
)

# Шаблон обнаружения ссылок внутри файла (зависимости L2)
INLINE_REF_PATTERN = re.compile(
    r"📁\s+(?P<path>\S+)\s+\|\s+🗃️\s+\S+\s+\|\s+🔑\s+sha:[0-9a-f]{12}"
)

PAGE_SIZE = 200  # строк на страницу


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _calc_hash(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()[:12]


def _parse_ref(ref: str) -> dict:
    """Разобрать гибридную ссылку. Возвращает {'path', 'vec_id', 'hash'} или вызывает ValueError."""
    m = REF_PATTERN.search(ref)
    if not m:
        raise ValueError(
            f"Неверный формат ссылки. Ожидается: "
            f"📁 /path | 🗃️ doc:id | 🔑 sha:12chars\nПолучено: {ref!r}"
        )
    return m.groupdict()


def _read_paginated(path: Path, page: int = 1) -> tuple[str, int, bool]:
    """Прочитать страницу файла. Возвращает (content, total_pages, has_more)."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    total_pages = max(1, (len(lines) + PAGE_SIZE - 1) // PAGE_SIZE)
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    content = "".join(lines[start:end])
    return content, total_pages, page < total_pages


def _find_dependencies(file_path: Path) -> list[str]:
    """Найти все гибридные ссылки внутри файла (для L2)."""
    content = file_path.read_text(encoding="utf-8")
    return [m.group(0) for m in INLINE_REF_PATTERN.finditer(content)]


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

def get_context(
    ref: str,
    depth: str = "L1",
    page: int = 1,
) -> dict:
    """
    Доставить контекст по гибридной ссылке.

    Args:
        ref:   Гибридная ссылка вида «📁 /path | 🗃️ doc:id | 🔑 sha:hash».
        depth: "L1" — только файл, "L2" — файл + список зависимостей.
        page:  Номер страницы для больших файлов (начиная с 1).

    Returns:
        dict с полями content, metadata, status (совместим со спецификацией §4.2).
    """
    try:
        parsed = _parse_ref(ref)
    except ValueError as e:
        return {
            "content": None,
            "metadata": {},
            "status": "ERROR",
            "error": str(e),
        }

    rel_path = parsed["path"].lstrip("/")
    expected_hash = parsed["hash"]
    vec_id = parsed["vec_id"]
    file_path = REPO_ROOT / rel_path

    # --- Проверка существования ---
    if not file_path.exists():
        return {
            "content": None,
            "metadata": {"path": rel_path, "vec_id": vec_id},
            "status": "BROKEN",
            "error": f"Файл не найден: {rel_path}",
        }

    # --- Проверка хэша ---
    actual_hash = _calc_hash(file_path)
    hash_valid = actual_hash == expected_hash
    status = "VALID" if hash_valid else "STALE"

    if not hash_valid:
        return {
            "content": None,
            "metadata": {
                "path": rel_path,
                "vec_id": vec_id,
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
                "depth": depth,
            },
            "status": "STALE",
            "error": (
                f"Хэш устарел. Ожидается sha:{expected_hash}, "
                f"актуально sha:{actual_hash}. "
                "Запросите актуальную ссылку у Оркестратора."
            ),
        }

    # --- Чтение содержимого ---
    content, total_pages, has_more = _read_paginated(file_path, page)

    metadata: dict = {
        "path": rel_path,
        "vec_id": vec_id,
        "hash": actual_hash,
        "depth": depth,
        "page": page,
        "total_pages": total_pages,
        "has_more": has_more,
        "dependencies": [],
    }

    # --- Зависимости (L2) ---
    if depth == "L2":
        metadata["dependencies"] = _find_dependencies(file_path)

    return {
        "content": content,
        "metadata": metadata,
        "status": status,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _list_documents() -> list[dict]:
    """Найти все файлы в стандартных директориях и вернуть их с хэшами."""
    dirs = [REPO_ROOT / d for d in ("specs", "arch", "models", "docs")]
    results = []
    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                rel = f.relative_to(REPO_ROOT)
                h = _calc_hash(f)
                sanitized = str(rel).replace("/", "_").replace("\\", "_").replace(".", "_")
                results.append({
                    "path": f"/{rel}",
                    "vec_id": f"doc:{sanitized}",
                    "hash": h,
                    "ref": f"📁 /{rel} | 🗃️ doc:{sanitized} | 🔑 sha:{h}",
                })
    return results


def main() -> None:
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    # --list
    if "--list" in args:
        docs = _list_documents()
        if not docs:
            print("Документы не найдены.")
        for d in docs:
            print(d["ref"])
        sys.exit(0)

    # Опции
    depth = "L1"
    page = 1
    ref = None

    i = 0
    while i < len(args):
        if args[i] == "--depth" and i + 1 < len(args):
            depth = args[i + 1]
            i += 2
        elif args[i] == "--page" and i + 1 < len(args):
            page = int(args[i + 1])
            i += 2
        elif args[i] == "--ref" and i + 1 < len(args):
            ref = args[i + 1]
            i += 2
        else:
            ref = args[i]
            i += 1

    if not ref:
        print("ERROR: укажите гибридную ссылку.", file=sys.stderr)
        sys.exit(1)

    result = get_context(ref, depth=depth, page=page)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["status"] == "VALID" else 1)


if __name__ == "__main__":
    main()
