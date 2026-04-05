"""
Файловый шлюз доставки контекста агентам (MCP-подобный протокол).
Раздел 4 спецификации COD-DOC.
"""

from __future__ import annotations

import re
from pathlib import Path

from cod_doc.core.hash_calc import calc_hash

REF_PATTERN = re.compile(
    r"📁\s+(?P<path>\S+)\s+\|\s+🗃️\s+(?P<vec_id>\S+)\s+\|\s+🔑\s+sha:(?P<hash>[0-9a-f]{12})"
)
INLINE_REF = re.compile(r"📁\s+\S+\s+\|\s+🗃️\s+\S+\s+\|\s+🔑\s+sha:[0-9a-f]{12}")
PAGE_SIZE = 200  # строк


def parse_ref(ref: str) -> dict:
    m = REF_PATTERN.search(ref)
    if not m:
        raise ValueError(
            f"Неверный формат ссылки: {ref!r}\n"
            "Ожидается: 📁 /path | 🗃️ doc:id | 🔑 sha:12hex"
        )
    return m.groupdict()


def get_context(
    ref: str,
    repo_root: Path,
    depth: str = "L1",
    page: int = 1,
) -> dict:
    """
    Доставить содержимое файла по гибридной ссылке.

    Returns dict с полями: content, metadata, status, error?
    status: VALID | STALE | BROKEN | ERROR
    """
    try:
        parsed = parse_ref(ref)
    except ValueError as e:
        return {"content": None, "metadata": {}, "status": "ERROR", "error": str(e)}

    rel = parsed["path"].lstrip("/")
    expected_hash = parsed["hash"]
    vec_id = parsed["vec_id"]
    file_path = repo_root / rel

    if not file_path.exists():
        return {
            "content": None,
            "metadata": {"path": rel, "vec_id": vec_id},
            "status": "BROKEN",
            "error": f"Файл не найден: {rel}",
        }

    actual_hash = calc_hash(file_path)
    if actual_hash != expected_hash:
        return {
            "content": None,
            "metadata": {
                "path": rel,
                "vec_id": vec_id,
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
                "depth": depth,
            },
            "status": "STALE",
            "error": (
                f"Хэш устарел: ожидается sha:{expected_hash}, "
                f"актуально sha:{actual_hash}. Запросите новую ссылку у Оркестратора."
            ),
        }

    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    total_pages = max(1, (len(lines) + PAGE_SIZE - 1) // PAGE_SIZE)
    start = (page - 1) * PAGE_SIZE
    content = "".join(lines[start : start + PAGE_SIZE])

    deps: list[str] = []
    if depth == "L2":
        deps = [m.group(0) for m in INLINE_REF.finditer(file_path.read_text(encoding="utf-8"))]

    return {
        "content": content,
        "metadata": {
            "path": rel,
            "vec_id": vec_id,
            "hash": actual_hash,
            "depth": depth,
            "page": page,
            "total_pages": total_pages,
            "has_more": page < total_pages,
            "dependencies": deps,
        },
        "status": "VALID",
    }
