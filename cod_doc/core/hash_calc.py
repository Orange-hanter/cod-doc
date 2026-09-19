"""SHA-256 хэширование файлов для COD-DOC."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

LINK_PATTERN = re.compile(
    r"(📁\s+(?P<path>\S+)\s+\|\s+🗃️\s+(?P<vec_id>\S+)\s+\|\s+🔑\s+sha:)(?P<hash>[0-9a-f]{12})"
)

#: Строка сводной таблицы реестра:
#: ``| 12 | MCP-интеграция | `doc:docs_mcp-integration_md` | `689bb19233e9` | … |``
#:
#: ADO-180: у документа два представления — блок со ссылкой и строка таблицы,
#: но `update_hashes` обновлял только первое. У 7 записей из 16 они разошлись,
#: и ВО ВСЕХ семи правдой была ссылка: таблица велась руками и отставала.
#: Поэтому колонка хэша в таблице становится производной. Колонки статуса и
#: даты остаются ручными — они несут смысл, которого нет в блоке.
#: Все группы именованные намеренно: нумерованные `m.group(3)` здесь считают и
#: именованные тоже, из-за чего «закрывающий бэктик» оказывался хэшем, и замена
#: удваивала его, съедая бэктик. Поймано тестом согласованности из этой же
#: задачи.
TABLE_ROW_PATTERN = re.compile(
    r"(?P<prefix>\|[^|\n]*\|[^|\n]*\|\s*`(?P<vec_id>doc:[\w-]+)`\s*\|\s*`)"
    r"(?P<hash>[0-9a-f]{12})"
    r"(?P<suffix>`)"
)


def calc_hash(file_path: str | Path) -> str:
    """Первые 12 символов SHA-256 содержимого файла."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def check_hash(file_path: str | Path, expected: str) -> bool:
    return calc_hash(file_path) == expected.removeprefix("sha:")


def check_stale_refs(
    master_path: str | Path, *, repo_root: Path | None = None
) -> list[dict[str, str]]:
    """Пройти реестр гибридных ссылок MASTER.md и вернуть находки.

    ``BROKEN`` — файла по пути из ссылки нет на диске. ``STALE`` — файл есть,
    но его sha256[:12] разошёлся с записанным в реестре; ``actual`` несёт
    фактический хэш. Записи, где хэш совпал, не возвращаются.

    CUR-016: логика жила приватной ``routine_service._check_stale_refs`` и у
    второго потребителя (doc card куратора) не было способа её позвать, кроме
    импорта приватного имени через слой. Теперь это публичная точка входа, а
    routine — её первый вызывающий.
    """
    master = Path(master_path)
    root = repo_root if repo_root is not None else master.parent
    content = master.read_text(encoding="utf-8") if master.exists() else ""

    findings: list[dict[str, str]] = []
    for m in LINK_PATTERN.finditer(content):
        rel = m.group("path").lstrip("/")
        expected = m.group("hash")
        target = root / rel
        if not target.exists():
            findings.append({"path": rel, "status": "BROKEN", "expected": expected})
        elif not check_hash(target, expected):
            findings.append(
                {
                    "path": rel,
                    "status": "STALE",
                    "expected": expected,
                    "actual": calc_hash(target),
                }
            )
    return findings


def make_ref(file_path: Path, repo_root: Path) -> str:
    """Сгенерировать гибридную ссылку для файла."""
    rel = file_path.relative_to(repo_root)
    h = calc_hash(file_path)
    sanitized = str(rel).replace("/", "_").replace("\\", "_").replace(".", "_")
    vec_id = f"doc:{sanitized}"
    return f"📁 /{rel} | 🗃️ {vec_id} | 🔑 sha:{h}"


def update_hashes(master_path: Path) -> tuple[int, list[str]]:
    """
    Пересчитать хэши в MASTER.md — и в блоках со ссылкой, и в таблице реестра.

    Возвращает (кол-во обновлённых, предупреждения).

    ADO-180: раньше обновлялись только блоки. Таблица велась руками и молча
    отставала — на момент правки 7 записей из 16 расходились, причём во всех
    семи правдой была ссылка. Теперь колонка хэша в таблице производная: она
    берётся из блока того же документа, а строки без блока (RFC 23, RFC 24)
    остаются нетронутыми.
    """
    master = Path(master_path)
    repo_root = master.parent
    content = master.read_text(encoding="utf-8")
    updated = 0
    warnings: list[str] = []

    #: doc-key -> актуальный хэш, собранный при обходе блоков.
    fresh: dict[str, str] = {}

    def replace_link_hash(m: re.Match[str]) -> str:
        nonlocal updated
        rel = m.group("path").lstrip("/")
        target = repo_root / rel
        prefix = m.group(1)
        if not target.exists():
            warnings.append(f"🔴 BROKEN: {rel}")
            # Хэш отсутствующего файла сохраняем как есть: файл может быть под
            # .gitignore (models/domain.md) и просто не выложен в воркtree.
            fresh[m.group("vec_id")] = m.group("hash")
            return m.group(0)
        new_hash = calc_hash(target)
        if new_hash != m.group("hash"):
            updated += 1
        fresh[m.group("vec_id")] = new_hash
        return prefix + new_hash

    def replace_table_hash(m: re.Match[str]) -> str:
        nonlocal updated
        new_hash = fresh.get(m.group("vec_id"))
        if new_hash is None:
            # Строка таблицы без блока со ссылкой — трогать нечем.
            return m.group(0)
        if new_hash != m.group("hash"):
            updated += 1
        return m.group("prefix") + new_hash + m.group("suffix")

    # Порядок важен: сначала блоки наполняют `fresh`, затем таблица его читает.
    content = LINK_PATTERN.sub(replace_link_hash, content)
    content = TABLE_ROW_PATTERN.sub(replace_table_hash, content)

    master.write_text(content, encoding="utf-8")
    return updated, warnings
