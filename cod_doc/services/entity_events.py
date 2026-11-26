"""Удаление сущности — событие для графа связей (RFC 39 §3.5, по образцу ADR-018).

Сервис, который удаляет строку сущности (документ, секцию, вопрос, секцию
плана…), не знает о рёбрах `relation`: он публикует `EntityDeleted`, а
`relation_service` снимает рёбра обоих направлений. Связывает их
`event_wiring.install()`.

Шина — та же, что у `doc_events` (синхронный вызов в той же сессии и
транзакции; публикация без подписчика падает `MissingSubscriberError`):
второй реестр обработчиков ничего бы не добавил.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cod_doc.infra.sync_registry import UID_REF_KIND_TABLES
from cod_doc.services.doc_events import MissingSubscriberError, publish, subscribe

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.domain.relations import RefKind

__all__ = [
    "KIND_BY_TABLE",
    "EntityDeleted",
    "MissingSubscriberError",
    "publish_deleted",
    "subscribe",
]

#: Таблица → значение `RefKind` (обратное к `UID_REF_KIND_TABLES`).
KIND_BY_TABLE: dict[str, str] = {table: kind for kind, table in UID_REF_KIND_TABLES.items()}


@dataclass(frozen=True, slots=True)
class EntityDeleted:
    """Строка сущности вот-вот удалена (публикуется до `session.delete`).

    ``key`` — публичный ключ для журнала (`None` — путь без него, например
    tombstone из sync); ``author`` — кто удаляет (`None` — применение чужого
    удаления: рёбра снимаются молча, след остался у автора на его реплике).
    """

    session: Session
    project_id: int
    kind: RefKind
    uid: str | None
    key: str | None
    author: str | None


def publish_deleted(
    session: Session,
    project_id: int,
    kind: RefKind,
    uid: str | None,
    *,
    key: str | None,
    author: str | None,
) -> None:
    """Опубликовать `EntityDeleted`; без подписчика — `MissingSubscriberError`."""
    publish(EntityDeleted(session, project_id, kind, uid, key, author))
