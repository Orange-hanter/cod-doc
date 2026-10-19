"""Применение journal-операций чужого changeset'а (RFC 29 §3.7, Q-010; CLO-024).

Journal (``revision``) не сливается: строки объединяются по глобальному id
(``sync_registry.JOURNAL_IDS``). Зовёт ``apply._apply_op`` до слияния по полям
— у журнала нет ни LWW, ни стратегий ``merge``, и ``pull`` не сверяет его с
хвостом (``merge.check``): две реплики не могут написать одну ревизию.

Что делает применение ревизии:

1. **Уже есть** строка с этим ``revision_id`` в проекте — ничего: объединение
   идемпотентно. В другом проекте этой БД — SYNC-102.
2. **Недоверенный ввод** — как у ``apply``: только ``upsert`` без ``base``
   (журнал дописывается), только колонки ``SHIPPED_COLUMNS``, обязательные
   поля на месте, ``project_id`` — uid проекта pull'а, вид сущности —
   ``EntityKind`` таблицы с колонкой ``uid``. Нарушение — SYNC-102.
3. **Сущность ревизии** ищется по uid в проекте pull'а. Нет её (удалена этой
   репликой раньше) — ревизия не пишется: реестр держит ревизию удалённой
   сущности local на любой реплике (решение 19, fail-closed), в дайджест она
   не входит ни там, ни здесь.
4. **Гейт реестра**: ревизия, которую реестр этой реплики держит local
   (сущность в restricted-документе или с придержанными колонками), не
   пишется — SYNC-102, как правка local-строки у ``apply._require_shipped``.
   ``diff`` проходит ``sync_capture.sanitize_diff``, как при захвате.
5. **Запись** — ``revision_service.append_foreign``: свои ``revision_id``,
   ``author``, ``reason``, ``at``; ``parent_revision_id`` — локальная голова
   сущности. Присланный ``parent_revision_id`` игнорируется: он описывает
   историю реплики-источника.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import inspect, select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra import sync_withdraw as cap_withdraw
from cod_doc.infra.models import RevisionModel
from cod_doc.services import revision_service
from cod_doc.services.sync_service import apply
from cod_doc.services.sync_service._values import from_wire
from cod_doc.services.sync_service.errors import ObjectMissingError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.orm import Session

    from cod_doc.services.sync_service._local import ProjectRef
    from cod_doc.services.sync_service.changeset import Op

__all__ = ["apply_op", "apply_row"]

_TABLE: Final = "revision"
_REQUIRED: Final = frozenset({"project_id", "entity_kind", "entity_id", "author", "at", "diff"})


def _reject(uid: str, why: str) -> ObjectMissingError:
    return ObjectMissingError(f"{_TABLE} {uid}: {why}")


def _check_fields(uid: str, fields: Mapping[str, object], info: cap.TableInfo) -> None:
    unknown = sorted(set(fields) - info.shipped)
    if unknown:
        raise _reject(uid, f"колонки {unknown} не уезжают — remote их не пишет")
    missing = sorted(c for c in _REQUIRED if fields.get(c) is None)
    if missing:
        raise _reject(uid, f"нет обязательных полей {missing}")


def _entity_table(uid: str, fields: Mapping[str, object]) -> tuple[EntityKind, str]:
    try:
        kind = EntityKind(str(fields["entity_kind"]))
    except ValueError:
        raise _reject(uid, "незнакомый вид сущности") from None
    table = reg.ENTITY_KIND_TABLES.get(kind)
    if table is None or table not in reg.UID_TABLES:
        raise _reject(uid, f"вид сущности {kind.value!r} без uid — такие ревизии не уезжают")
    return kind, table


def _existing(session: Session, project: ProjectRef, uid: str) -> bool:
    found = session.scalars(
        select(RevisionModel.project_id).where(RevisionModel.revision_id == uid)
    ).first()
    if found is None:
        return False
    if found != project.row_id:
        raise _reject(uid, "ревизия с этим id принадлежит другому проекту этой БД")
    return True


def apply_op(session: Session, project: ProjectRef, op: Op) -> None:
    """Применить journal-операцию ``op`` в транзакции changeset'а."""
    if op.kind != "upsert" or op.base:
        raise _reject(op.entity_uid, "журнал только дописывается — правка или удаление строки")
    apply_row(session, project, op.entity_uid, op.fields)


def apply_row(
    session: Session, project: ProjectRef, revision_id: str, fields: Mapping[str, object]
) -> bool:
    """Дописать ревизию ``revision_id`` с полями в форме changeset'а (операция, снапшот).

    ``False`` — не записана: уже есть или её сущности на реплике нет (п. 1, 3).
    """
    info = cap.table_info(inspect(RevisionModel))
    assert info is not None  # revision в CAPTURED_TABLES
    _check_fields(revision_id, fields, info)
    if _existing(session, project, revision_id):
        return False
    if fields["project_id"] != project.uid:
        raise _reject(revision_id, "ревизия чужого проекта")
    kind, table = _entity_table(revision_id, fields)
    target = apply.entity_by_uid(session, project, table, str(fields["entity_id"]))
    if target is None:
        return False  # ревизия удалённой сущности local на обеих репликах (п. 3)
    entity_id = int(getattr(target[0], "row_id"))  # noqa: B009
    row = {
        "revision_id": revision_id,
        "project_id": project.row_id,
        "entity_kind": kind.value,
        "entity_id": entity_id,
    }
    if cap.shipped_fields(session, _TABLE, row) is None and not _before_withdrawal(
        session, project, table, *target
    ):
        raise _reject(revision_id, "ревизия local на этой реплике (restricted и т. п.)")
    at = from_wire(_TABLE, "at", fields["at"])
    if not isinstance(at, datetime):
        raise _reject(revision_id, "at — не время")
    revision_service.append_foreign(
        session,
        revision_id=revision_id,
        project_id=project.row_id,
        entity_kind=kind,
        entity_id=entity_id,
        author=str(fields["author"]),
        at=at,
        diff=str(cap.sanitize_diff(kind.value, str(fields["diff"]))),
        reason=_text(fields.get("reason")),
        commit_sha=_text(fields.get("commit_sha")),
    )
    return True


def _before_withdrawal(
    session: Session, project: ProjectRef, table: str, entity: object, info: cap.TableInfo
) -> bool:
    """Ревизия сущности, которую держит local только отозванный документ (CLO-046).

    Записана до отзыва и у получателей уже есть: отвергнуть — остановить pull
    владельца, пропустить — разойтись с получателями после понижения.
    """
    row = {c: getattr(entity, a) for c, a in info.columns}
    return cap_withdraw.ships_but_for_withdrawal(session, project.row_id, table, row)


def _text(value: object) -> str | None:
    return None if value is None else str(value)
