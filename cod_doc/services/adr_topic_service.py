"""ARG-008 (RFC 34 §3.4): полки реестра ADR.

Полка — тема, о чём решение: «Хранение», «Агент». У ADR одна полка или ни
одной («Без темы» — это ``adr.topic_id IS NULL``, а не отдельная строка).
Человек заводит полки и задаёт им порядок; агент (секция C) только выбирает
из существующих по составу «входит / не входит».

Полка ADR и раздел дерева документации — разные нарезки: раздел отвечает
«куда класть документ», полка — «о чём решение» (RFC 34 §5). Поэтому своя
таблица и свой сервис, а не ``doc_node``.

Каждая запись — ревизия ``EntityKind.ADR_TOPIC`` и activity-событие одной
транзакцией (ADO-040). Привязку ADR к полке пишет ``adr_service.set_topic``:
это мутация решения, её ревизия — на ADR.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.models import ADRModel, ADRTopicModel
from cod_doc.services import activity_service
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: Предел длины названия полки — колонка ``String(64)``.
NAME_MAX = 64
#: Предел длины «входит» / «не входит». Состав полки — пара фраз, которую
#: куратор читает в промпте классификации; длинный текст раздувает и промпт,
#: и каждую ревизию с activity-событием.
SCOPE_MAX = 500


class ADRTopicNotFoundError(LookupError):
    pass


class ADRTopicExistsError(ValueError):
    pass


def _diff(op: str, **fields: object) -> str:
    return json.dumps({"op": op, **fields}, ensure_ascii=False)


def _clean_name(name: str) -> str:
    clean = " ".join(name.split())
    if not clean:
        raise ValueError("topic name must not be empty")
    if len(clean) > NAME_MAX:
        raise ValueError(f"topic name is longer than {NAME_MAX} characters")
    return clean


def _clean_scope(field: str, value: str) -> str:
    clean = value.strip()
    if len(clean) > SCOPE_MAX:
        raise ValueError(f"topic {field} is longer than {SCOPE_MAX} characters")
    return clean


def list_for_project(session: Session, project_id: int) -> list[ADRTopicModel]:
    """Полки проекта в заданном человеком порядке."""
    return list(
        session.execute(
            select(ADRTopicModel)
            .where(ADRTopicModel.project_id == project_id)
            .order_by(ADRTopicModel.position, ADRTopicModel.row_id)
        ).scalars()
    )


def get(session: Session, project_id: int, name: str) -> ADRTopicModel | None:
    return session.execute(
        select(ADRTopicModel).where(
            ADRTopicModel.project_id == project_id, ADRTopicModel.name == name
        )
    ).scalar_one_or_none()


def require(session: Session, project_id: int, name: str) -> ADRTopicModel:
    topic = get(session, project_id, name)
    if topic is None:
        raise ADRTopicNotFoundError(f"ADR topic {name!r} not found")
    return topic


def names_by_id(session: Session, project_id: int) -> dict[int, str]:
    """Имена полок проекта одним запросом: ``{row_id: name}`` — для списков ADR."""
    return {
        int(row_id): str(name)
        for row_id, name in session.execute(
            select(ADRTopicModel.row_id, ADRTopicModel.name).where(
                ADRTopicModel.project_id == project_id
            )
        ).all()
    }


def adr_counts(session: Session, project_id: int) -> dict[int, int]:
    """Сколько ADR лежит на каждой полке: ``{topic row_id: n}``."""
    return {
        int(topic_id): int(n)
        for topic_id, n in session.execute(
            select(ADRModel.topic_id, func.count())
            .where(ADRModel.project_id == project_id, ADRModel.topic_id.is_not(None))
            .group_by(ADRModel.topic_id)
        ).all()
        if topic_id is not None  # отфильтровано в WHERE; проверка — для типов
    }


def topic_to_dict(topic: ADRTopicModel, *, adr_count: int | None = None) -> dict[str, object]:
    out: dict[str, object] = {
        "name": topic.name,
        "includes": topic.includes,
        "excludes": topic.excludes,
        "position": topic.position,
    }
    if adr_count is not None:
        out["adr_count"] = adr_count
    return out


def _write(
    session: Session,
    project_id: int,
    topic: ADRTopicModel,
    *,
    op: str,
    event: str,
    author: str,
    diff_fields: dict[str, object],
    summary: str,
    reason: str | None,
) -> None:
    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.ADR_TOPIC,
        entity_id=topic.row_id,
        author=author,
        diff=_diff(op, name=topic.name, **diff_fields),
        reason=reason or op,
    )
    activity_service.emit_for_write(
        session,
        project_id,
        event,
        author,
        scope_kind=EntityKind.ADR_TOPIC.value,
        scope_id=topic.name,
        payload={"name": topic.name, **diff_fields},
        summary=summary,
    )


def create(
    session: Session,
    *,
    project_id: int,
    name: str,
    includes: str = "",
    excludes: str = "",
    author: str = "human",
    reason: str | None = None,
) -> ADRTopicModel:
    """Завести полку. Встаёт в конец: перед «Без темы», которая всегда последняя."""
    clean = _clean_name(name)
    if get(session, project_id, clean) is not None:
        raise ADRTopicExistsError(f"ADR topic {clean!r} already exists")
    last = session.execute(
        select(func.max(ADRTopicModel.position)).where(ADRTopicModel.project_id == project_id)
    ).scalar_one()
    now = datetime.now(UTC)
    topic = ADRTopicModel(
        project_id=project_id,
        name=clean,
        includes=_clean_scope("includes", includes),
        excludes=_clean_scope("excludes", excludes),
        position=0 if last is None else int(last) + 1,
        created=now,
        last_updated=now,
    )
    session.add(topic)
    session.flush()
    _write(
        session,
        project_id,
        topic,
        op="create",
        event="adr.topic_created",
        author=author,
        diff_fields={"position": topic.position},
        summary=f"ADR topic «{clean}» created",
        reason=reason,
    )
    return topic


def update(
    session: Session,
    *,
    project_id: int,
    name: str,
    new_name: str | None = None,
    includes: str | None = None,
    excludes: str | None = None,
    author: str = "human",
    reason: str | None = None,
) -> ADRTopicModel:
    """Переименовать полку или поправить её состав. ``None`` — «не менять»."""
    topic = require(session, project_id, name)
    changed: dict[str, object] = {}
    if new_name is not None:
        clean = _clean_name(new_name)
        if clean != topic.name:
            if get(session, project_id, clean) is not None:
                raise ADRTopicExistsError(f"ADR topic {clean!r} already exists")
            changed["renamed"] = {"old": topic.name, "new": clean}
            topic.name = clean
    for field, value in (("includes", includes), ("excludes", excludes)):
        if value is None:
            continue
        clean = _clean_scope(field, value)
        if clean != getattr(topic, field):
            setattr(topic, field, clean)
            changed[field] = {"changed": True}
    if not changed:
        return topic
    topic.last_updated = datetime.now(UTC)
    session.flush()
    _write(
        session,
        project_id,
        topic,
        op="update",
        event="adr.topic_updated",
        author=author,
        diff_fields=changed,
        summary=f"ADR topic «{topic.name}» updated",
        reason=reason,
    )
    return topic


def move(
    session: Session,
    *,
    project_id: int,
    name: str,
    position: int,
    author: str = "human",
    reason: str | None = None,
) -> ADRTopicModel:
    """Поставить полку на ``position`` (с нуля); остальные сдвигаются, порядок без дыр.

    Позиция за концом списка зажимается в последнюю.
    """
    if position < 0:
        raise ValueError("position must be >= 0")
    topics = list_for_project(session, project_id)
    topic = next((t for t in topics if t.name == name), None)
    if topic is None:
        raise ADRTopicNotFoundError(f"ADR topic {name!r} not found")
    old = topics.index(topic)
    new = min(position, len(topics) - 1)
    if new == old and topic.position == old:
        return topic
    topics.pop(old)
    topics.insert(new, topic)
    for i, t in enumerate(topics):
        t.position = i
    topic.last_updated = datetime.now(UTC)
    session.flush()
    _write(
        session,
        project_id,
        topic,
        op="move",
        event="adr.topic_moved",
        author=author,
        diff_fields={"old": old, "new": new},
        summary=f"ADR topic «{topic.name}» moved {old} → {new}",
        reason=reason,
    )
    return topic


def delete(
    session: Session,
    *,
    project_id: int,
    name: str,
    author: str = "human",
    reason: str | None = None,
) -> int:
    """Удалить полку. Её ADR уходят в «Без темы»; возвращает их число.

    Внешнего ключа ``adr.topic_id`` в БД нет (миграция 0046), поэтому
    ``SET NULL`` делается здесь, через ``adr_service.set_topic``: у каждого
    решения остаётся ревизия о том, что полку у него сняли.
    """
    from cod_doc.services import adr_service

    topic = require(session, project_id, name)
    adr_ids = list(
        session.execute(
            select(ADRModel.adr_id)
            .where(ADRModel.project_id == project_id, ADRModel.topic_id == topic.row_id)
            .order_by(ADRModel.adr_id)
        ).scalars()
    )
    for adr_id in adr_ids:
        adr_service.set_topic(
            session,
            project_id=project_id,
            adr_id=adr_id,
            topic=None,
            author=author,
            reason=reason or f"topic «{name}» deleted",
        )
    _write(
        session,
        project_id,
        topic,
        op="delete",
        event="adr.topic_deleted",
        author=author,
        diff_fields={"unshelved": adr_ids},
        summary=f"ADR topic «{name}» deleted, {len(adr_ids)} ADR(s) to «no topic»",
        reason=reason,
    )
    session.delete(topic)
    session.flush()
    # Порядок без дыр: оставшиеся полки — 0..n-1.
    for i, t in enumerate(list_for_project(session, project_id)):
        t.position = i
    session.flush()
    return len(adr_ids)
