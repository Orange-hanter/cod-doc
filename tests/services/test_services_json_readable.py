"""Сервисы сериализуют JSON с ``ensure_ascii=False`` (ARG-010).

Diff ревизии и прочие JSON-поля, которые пишут сервисы, читаются человеком —
в ленте ревизий, в ``revision_get``, в карточках. С ``ensure_ascii=True``
(дефолт ``json.dumps``) вместо «Хранение» в них ложится ``\\u0425…``; так
было у ADR, задач, историй и сценариев, пока флаг не проставили руками в
каждом месте. Тест ловит новый ``json.dumps`` без флага, иначе следующий
сервис молча вернёт escape-коды.
"""

from __future__ import annotations

import ast
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[2] / "cod_doc" / "services"

#: Где escape-коды нужны намеренно. Обоснование обязательно.
ALLOWLIST: dict[str, str] = {
    "approval_service.py": "канонический JSON под хэш предложения: байты должны "
    "совпадать между записью и проверкой, читаемость не цель",
}


def _offenders() -> list[str]:
    found: list[str] = []
    for path in sorted(SERVICES.rglob("*.py")):
        rel = path.relative_to(SERVICES).as_posix()
        if rel in ALLOWLIST:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "dumps"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "json"
            ):
                continue
            flag = next((k for k in node.keywords if k.arg == "ensure_ascii"), None)
            if not (
                flag is not None
                and isinstance(flag.value, ast.Constant)
                and flag.value.value is False
            ):
                found.append(f"{rel}:{node.lineno}")
    return found


def test_services_dump_json_readably() -> None:
    assert _offenders() == [], (
        "json.dumps без ensure_ascii=False в cod_doc/services — кириллица уйдёт в "
        "\\u-escape; добавь флаг или внеси файл в ALLOWLIST с обоснованием"
    )


def test_allowlist_is_live() -> None:
    """Запись allowlist указывает на существующий файл: мёртвая запись прячет регресс."""
    assert all((SERVICES / rel).is_file() for rel in ALLOWLIST)
