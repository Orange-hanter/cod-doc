"""SHA-256 хэширование файлов для COD-DOC."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

LINK_PATTERN = re.compile(
    r"(📁\s+(?P<path>\S+)\s+\|\s+🗃️\s+(?P<vec_id>\S+)\s+\|\s+🔑\s+sha:)(?P<hash>[0-9a-f]{12})"
)


def calc_hash(file_path: str | Path) -> str:
    """Первые 12 символов SHA-256 содержимого файла."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def check_hash(file_path: str | Path, expected: str) -> bool:
    return calc_hash(file_path) == expected.removeprefix("sha:")


def make_ref(file_path: Path, repo_root: Path) -> str:
    """Сгенерировать гибридную ссылку для файла."""
    rel = file_path.relative_to(repo_root)
    h = calc_hash(file_path)
    sanitized = str(rel).replace("/", "_").replace("\\", "_").replace(".", "_")
    vec_id = f"doc:{sanitized}"
    return f"📁 /{rel} | 🗃️ {vec_id} | 🔑 sha:{h}"


def update_hashes(master_path: Path) -> tuple[int, list[str]]:
    """
    Пересчитать хэши в MASTER.md.
    Возвращает (кол-во обновлённых, предупреждения).
    """
    master = Path(master_path)
    repo_root = master.parent
    content = master.read_text(encoding="utf-8")
    updated = 0
    warnings: list[str] = []

    def replace_hash(m: re.Match) -> str:
        nonlocal updated
        rel = m.group("path").lstrip("/")
        target = repo_root / rel
        prefix = m.group(1)
        if not target.exists():
            warnings.append(f"🔴 BROKEN: {rel}")
            return m.group(0)
        new_hash = calc_hash(target)
        if new_hash != m.group("hash"):
            updated += 1
        return prefix + new_hash

    new_content = LINK_PATTERN.sub(replace_hash, content)
    master.write_text(new_content, encoding="utf-8")
    return updated, warnings
