"""Ссылка на сущность `kind:key` — формат ввода и вывода (RFC 39 §3.1).

Чистый домен, без БД. В хранилище конец ребра адресуется `uid` строки-цели;
`kind:key` нужен человеку и агенту. Перевод между ними — `services/entity_ref.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RefKind(StrEnum):
    TASK = "task"
    STORY = "story"
    CRITERION = "criterion"
    DOCUMENT = "document"
    SECTION = "section"
    ADR = "adr"
    QUESTION = "question"
    SCENARIO = "scenario"
    PLAN = "plan"
    PLAN_SECTION = "plan_section"
    FINDING = "finding"
    MODULE = "module"
    CODE = "code"
    URL = "url"


#: Виды, у которых есть строка в БД (а значит, `uid`). `code`/`url` — литералы.
ENTITY_KINDS: frozenset[RefKind] = frozenset(RefKind) - {RefKind.CODE, RefKind.URL}

#: Пример ключа каждого вида — для сообщений об ошибках.
KEY_EXAMPLES: dict[RefKind, str] = {
    RefKind.TASK: "ADO-012",
    RefKind.STORY: "US-007",
    RefKind.CRITERION: "US-064#2",
    RefKind.DOCUMENT: "docs/system/MASTER.md",
    RefKind.SECTION: "docs/system/MASTER.md#registry",
    RefKind.ADR: "ADR-007",
    RefKind.QUESTION: "Q-003",
    RefKind.SCENARIO: "SCN-001",
    RefKind.PLAN: "relation-graph-2026-12",
    RefKind.PLAN_SECTION: "relation-graph-2026-12#A",
    RefKind.FINDING: "3f2b8c1e-0000-4000-8000-000000000000",
    RefKind.MODULE: "core",
    RefKind.CODE: "cod_doc/services/task_service.py::complete",
    RefKind.URL: "https://example.com/spec",
}


class RefSyntaxError(ValueError):
    """Строка не разбирается как `kind:key`; сообщение несёт ожидаемый формат."""


@dataclass(frozen=True)
class EntityRef:
    kind: RefKind
    key: str

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.key}"


def parse_ref(raw: str) -> EntityRef:
    """Разобрать `kind:key`; `RefSyntaxError` с примером формата при ошибке."""
    kind_s, sep, key = raw.partition(":")
    try:
        kind = RefKind(kind_s)
    except ValueError:
        allowed = ", ".join(k.value for k in RefKind)
        msg = f"ссылка {raw!r}: неизвестный вид {kind_s!r}; допустимо: {allowed}. Формат: kind:key"
        raise RefSyntaxError(msg) from None
    if not sep or not key or key != key.strip():
        msg = (
            f"ссылка {raw!r}: пустой ключ или пробелы по краям. "
            f"Формат: {kind.value}:<key>, например {kind.value}:{KEY_EXAMPLES[kind]}"
        )
        raise RefSyntaxError(msg)
    return EntityRef(kind, key)
