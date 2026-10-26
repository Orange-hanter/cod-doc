"""Публичные адреса сущностей по паре (kind, row_id) — ADO-124.

Журналы (ревизии, activity) хранят адрес сущности как `(entity_kind,
entity_id)`, где `entity_id` — внутренний `row_id`. Человеку и URL нужен
публичный ключ: `task_id`, `doc_key`, `story_id`, `adr_id`. Этот модуль
переводит одно в другое пачкой — один запрос на вид сущности, а не на строку
журнала.

Лист: импортирует только модели и домен. URL здесь не строятся — это дело
слоя представления; сервис отдаёт ключи.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.models import (
    ADRModel,
    DocumentModel,
    PlanModel,
    SectionModel,
    TaskModel,
    UserStoryModel,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sqlalchemy.orm import Session

# Нарратив истории длинный; в подпись идёт начало.
_STORY_TITLE_CHARS = 80


@dataclass(frozen=True)
class EntityRef:
    """Сущность журнала с публичным ключом.

    `ref` — публичный ключ вида: `task_id`, `doc_key`, `story_id`, `adr_id`,
    `scope` плана. У секции `ref` — `doc_key` её документа, а `anchor` —
    якорь самой секции.
    """

    kind: EntityKind
    row_id: int
    ref: str
    title: str
    anchor: str | None = None


def _tasks(session: Session, project_id: int, ids: set[int]) -> list[EntityRef]:
    rows = session.execute(
        select(TaskModel.row_id, TaskModel.task_id, TaskModel.title).where(
            TaskModel.project_id == project_id, TaskModel.row_id.in_(ids)
        )
    )
    return [EntityRef(EntityKind.TASK, r.row_id, r.task_id, r.title) for r in rows]


def _documents(session: Session, project_id: int, ids: set[int]) -> list[EntityRef]:
    rows = session.execute(
        select(DocumentModel.row_id, DocumentModel.doc_key, DocumentModel.title).where(
            DocumentModel.project_id == project_id, DocumentModel.row_id.in_(ids)
        )
    )
    return [EntityRef(EntityKind.DOCUMENT, r.row_id, r.doc_key, r.title) for r in rows]


def _sections(session: Session, project_id: int, ids: set[int]) -> list[EntityRef]:
    # У секции нет project_id — проект берётся у её документа.
    rows = session.execute(
        select(
            SectionModel.row_id, SectionModel.anchor, SectionModel.heading, DocumentModel.doc_key
        )
        .join(DocumentModel, DocumentModel.row_id == SectionModel.document_id)
        .where(DocumentModel.project_id == project_id, SectionModel.row_id.in_(ids))
    )
    return [
        EntityRef(EntityKind.SECTION, r.row_id, r.doc_key, r.heading, anchor=r.anchor) for r in rows
    ]


def _stories(session: Session, project_id: int, ids: set[int]) -> list[EntityRef]:
    rows = session.execute(
        select(UserStoryModel.row_id, UserStoryModel.story_id, UserStoryModel.narrative).where(
            UserStoryModel.project_id == project_id, UserStoryModel.row_id.in_(ids)
        )
    )
    return [
        EntityRef(EntityKind.STORY, r.row_id, r.story_id, r.narrative[:_STORY_TITLE_CHARS])
        for r in rows
    ]


def _adrs(session: Session, project_id: int, ids: set[int]) -> list[EntityRef]:
    rows = session.execute(
        select(ADRModel.row_id, ADRModel.adr_id, ADRModel.title).where(
            ADRModel.project_id == project_id, ADRModel.row_id.in_(ids)
        )
    )
    return [EntityRef(EntityKind.ADR, r.row_id, r.adr_id, r.title) for r in rows]


def _plans(session: Session, project_id: int, ids: set[int]) -> list[EntityRef]:
    rows = session.execute(
        select(PlanModel.row_id, PlanModel.scope).where(
            PlanModel.project_id == project_id, PlanModel.row_id.in_(ids)
        )
    )
    return [EntityRef(EntityKind.PLAN, r.row_id, r.scope, r.scope) for r in rows]


# Виды, у которых есть публичный ключ. Остальные (task_doc, link, module,
# секции истории и плана, разделы дерева) своей страницы не имеют.
RESOLVERS: dict[EntityKind, Callable[[Session, int, set[int]], list[EntityRef]]] = {
    EntityKind.TASK: _tasks,
    EntityKind.DOCUMENT: _documents,
    EntityKind.SECTION: _sections,
    EntityKind.STORY: _stories,
    EntityKind.ADR: _adrs,
    EntityKind.PLAN: _plans,
}


def resolve(
    session: Session,
    project_id: int,
    pairs: Iterable[tuple[EntityKind, int]],
) -> dict[tuple[EntityKind, int], EntityRef]:
    """Публичные ключи для пар `(kind, row_id)`; один запрос на вид.

    Пары, которых нет в ответе, либо удалены (вид из `RESOLVERS`), либо
    публичного ключа не имеют вовсе. Чужой проект не резолвится: `row_id`
    уникален на БД, а не на проект, и в hub-режиме совпадёт с чужой строкой.
    """
    by_kind: dict[EntityKind, set[int]] = defaultdict(set)
    for kind, row_id in pairs:
        if kind in RESOLVERS:
            by_kind[kind].add(row_id)
    found: dict[tuple[EntityKind, int], EntityRef] = {}
    for kind, ids in by_kind.items():
        for ref in RESOLVERS[kind](session, project_id, ids):
            found[(ref.kind, ref.row_id)] = ref
    return found


def existing_public_ids(
    session: Session,
    project_id: int,
    candidates: Iterable[str],
) -> dict[str, EntityKind]:
    """Какие из упомянутых в тексте ключей — живые задачи, истории, ADR (ADO-142).

    Проза ссылается на сущности по публичному ключу (`ADO-012`, `US-004`,
    `ADR-007`). Ссылкой становится только существующая в этом проекте
    запись: битая ссылка хуже текста. Один запрос на вид, а не на упоминание.

    Ключ, который есть у сущностей разных видов (префикс задач совпал с
    префиксом историй), в ответ не попадает: какую из них имел в виду
    автор, по тексту не понять, а ссылка не туда хуже текста.
    """
    ids = {c for c in candidates if c}
    if not ids:
        return {}
    found: dict[str, EntityKind] = {}
    ambiguous: set[str] = set()
    for kind, column, model_project in (
        (EntityKind.TASK, TaskModel.task_id, TaskModel.project_id),
        (EntityKind.STORY, UserStoryModel.story_id, UserStoryModel.project_id),
        (EntityKind.ADR, ADRModel.adr_id, ADRModel.project_id),
    ):
        rows = session.execute(
            select(column).where(model_project == project_id, column.in_(ids))
        ).scalars()
        for public_id in rows:
            if public_id in found:
                ambiguous.add(public_id)
            found.setdefault(public_id, kind)
    return {key: kind for key, kind in found.items() if key not in ambiguous}
