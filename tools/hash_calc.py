#!/usr/bin/env python3
"""
hash_calc.py — Утилита вычисления и проверки SHA-256 хэшей для COD-DOC.

Использование:
  python tools/hash_calc.py calc <file>           # Вычислить хэш файла (12 символов)
  python tools/hash_calc.py check <file> <hash>   # Проверить совпадение хэша
  python tools/hash_calc.py update <master_md>    # Обновить все хэши в MASTER.md
"""

import hashlib
import re
import sys
from pathlib import Path


def calc_hash(file_path: str | Path) -> str:
    """Вычислить первые 12 символов SHA-256 для содержимого файла."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:12]


def check_hash(file_path: str | Path, expected: str) -> bool:
    """Проверить, совпадает ли хэш файла с ожидаемым."""
    actual = calc_hash(file_path)
    return actual == expected


# Шаблон гибридной ссылки: 📁 /path | 🗃️ doc:id | 🔑 sha:XXXXXXXXXXXX
LINK_PATTERN = re.compile(
    r"(📁\s+(?P<path>\S+)\s+\|\s+🗃️\s+(?P<vec_id>\S+)\s+\|\s+🔑\s+sha:)(?P<hash>[0-9a-f]{12})"
)


def update_master(master_path: str | Path) -> tuple[int, list[str]]:
    """
    Пересчитать хэши всех ссылок в MASTER.md.
    Возвращает (кол-во обновлённых, список предупреждений).
    """
    master = Path(master_path)
    repo_root = master.parent
    content = master.read_text(encoding="utf-8")

    updated = 0
    warnings: list[str] = []

    def replace_hash(m: re.Match) -> str:
        nonlocal updated
        rel_path = m.group("path")
        file_path = repo_root / rel_path.lstrip("/")
        prefix = m.group(1)

        if not file_path.exists():
            warnings.append(f"🔴 BROKEN: файл не найден → {rel_path}")
            return m.group(0)  # оставить как есть

        new_hash = calc_hash(file_path)
        old_hash = m.group("hash")
        if new_hash != old_hash:
            updated += 1
        return prefix + new_hash

    new_content = LINK_PATTERN.sub(replace_hash, content)
    master.write_text(new_content, encoding="utf-8")
    return updated, warnings


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "calc" and len(args) == 2:
        try:
            h = calc_hash(args[1])
            print(f"sha:{h}  {args[1]}")
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "check" and len(args) == 3:
        try:
            ok = check_hash(args[1], args[2].removeprefix("sha:"))
            status = "✅ VALID" if ok else "🔴 STALE"
            actual = calc_hash(args[1])
            print(f"{status}  expected={args[2]}  actual=sha:{actual}")
            sys.exit(0 if ok else 2)
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "update" and len(args) == 2:
        n, warns = update_master(args[1])
        for w in warns:
            print(w, file=sys.stderr)
        print(f"✅ Обновлено хэшей: {n}  предупреждений: {len(warns)}")

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
