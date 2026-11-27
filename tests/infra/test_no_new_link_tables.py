"""REL-008: новые таблицы связей запрещены (RFC 39 §3.8).

Без запрета RFC добавил бы ещё один механизм связей, а не заменил существующие:
следующая фича опять завела бы `*_link` «потому что так быстрее». Гейт проходит
по метаданным `Base` и находит таблицы, похожие на связь:

- колонки `to_kind` + `to_ref`, либо пара `<x>_kind` + `<x>_uid`;
- два и больше FK на таблицы сущностей, а остальные колонки — метаданные ребра;
- имя `*_link`, `*_relation`, `*_edge`.

Найденная таблица вне `ALLOWLIST` роняет тест. `ALLOWLIST` — храповик, как
`per-file-ignores`: может только уменьшаться (запись без таблицы роняет тест),
а конец списка — `relation`/`relation_type`, куда всё сходится (фаза C RFC).
"""

from __future__ import annotations

import importlib
import pkgutil

from sqlalchemy import Table

import cod_doc.infra.models as models_pkg
from cod_doc.infra.models import Base
from cod_doc.infra.sync_registry import UID_REF_KIND_TABLES

# Колонки, которые не делают таблицу «сущностью»: идентичность и метаданные ребра.
_EDGE_META = frozenset(
    {
        "row_id", "uid", "id", "kind", "type", "relation", "reason", "note", "author", "ts",
        "at", "created", "created_at", "position", "rank", "resolved", "broken_reason",
        "last_checked", "met", "chosen", "label", "score", "state",
    }
)  # fmt: skip

#: Таблица → почему она пока есть. Только сокращать; цель — только `relation*`.
ALLOWLIST: dict[str, str] = {
    "relation": "цель RFC 39: единая таблица простых связей",
    "relation_type": "цель RFC 39: проектные типы отношений",
    "code_edge": "derived: граф зависимостей кода из снимка структуры, не связь сущностей проекта",
    "sync_op": "журнал репликации (entity_kind+entity_uid — адрес операции), не связь сущностей",
    "sync_conflict": "состояние репликации (адрес конфликтующей сущности), не связь сущностей",
    "story_link": "§2.1: история ↔ сущность; уйдёт в relation в фазе C",
    "scenario_link": "§2.1: сценарий ↔ сущность; уйдёт в relation в фазе C",
    "open_question_link": "§2.1: вопрос ↔ сущность; уйдёт в relation в фазе C",
    "dependency": "§2.2: ребро задача → задача; остаётся бэкендом relation (ready-множество)",
    "adr_relation": "§2.2: ребро ADR → ADR; остаётся бэкендом relation",
    "adr_supersedes": "§2.2: ADR заменяет ADR; остаётся бэкендом relation",
    "module_dependency": "§2.2: зависимость модулей; кандидат в relation_type модуля",
    "commit_link": "§2.4: задача ↔ коммит git, читается как committed_in (REL-011)",
    "approval_task_link": "согласование ↔ задача: часть протокола approval, не граф знаний",
    "approval_doc_revision_link": "согласование ↔ ревизия документа: часть протокола approval",
}


def _all_tables() -> dict[str, Table]:
    for mod in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"cod_doc.infra.models.{mod.name}")  # link_suggestion — лениво
    return dict(Base.metadata.tables)


def _link_like(table: Table) -> list[str]:
    """Признаки, по которым таблица похожа на связь; пусто — не похожа."""
    cols = {c.name for c in table.columns}
    reasons = []
    if {"to_kind", "to_ref"} <= cols:
        reasons.append("to_kind+to_ref")
    for c in sorted(cols):
        if c.endswith("_kind") and f"{c[: -len('_kind')]}_uid" in cols:
            reasons.append(f"{c}+{c[: -len('_kind')]}_uid")
    entity_tables = set(UID_REF_KIND_TABLES.values())
    fks = [
        fk for c in table.columns for fk in c.foreign_keys if fk.column.table.name in entity_tables
    ]
    fk_cols = {fk.parent.name for fk in fks}
    if len(fk_cols) >= 2 and not ({c.name for c in table.columns} - fk_cols - _EDGE_META):
        reasons.append("два FK на сущности + метаданные ребра")
    if table.name.endswith(("_link", "_relation", "_edge")):
        reasons.append("имя *_link/*_relation/*_edge")
    return reasons


def test_no_link_like_table_outside_the_allowlist() -> None:
    new = {
        t.name: reasons
        for t in _all_tables().values()
        if (reasons := _link_like(t)) and t.name not in ALLOWLIST
    }
    assert new == {}, (
        "новая таблица связи: используй relation (relate/relation_type_define, RFC 39), "
        f"а не заводи свою: {new}"
    )


def test_allowlist_only_shrinks() -> None:
    """Запись без таблицы или без признаков связи — мёртвая: убери её (храповик)."""
    tables = _all_tables()
    stale = sorted(
        name
        for name in ALLOWLIST
        if name not in tables
        or (name not in {"relation", "relation_type"} and not _link_like(tables[name]))
    )
    assert stale == []
    assert all(len(reason) >= 20 for reason in ALLOWLIST.values())


def test_detector_catches_a_fresh_link_table() -> None:
    """Смоук на сам детектор: свежая `*_link` с to_kind/to_ref обязана находиться."""
    from sqlalchemy import Column, Integer, MetaData, String

    probe = Table(
        "probe_link", MetaData(), Column("row_id", Integer, primary_key=True),
        Column("to_kind", String), Column("to_ref", String),
    )  # fmt: skip
    assert {"to_kind+to_ref", "имя *_link/*_relation/*_edge"} <= set(_link_like(probe))
