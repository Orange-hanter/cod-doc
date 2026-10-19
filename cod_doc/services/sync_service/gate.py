"""Гейт отправки: restricted не покидает реплику (RFC 29 §3.2, Q-015; CLO-033).

Три рубежа, от раннего к позднему:

1. **Захват** (``sync_capture``, CLO-006): строка restricted-документа и его
   тела не пишется в журнал; гейт пересверяется на commit'е транзакции.
2. **Упаковка** (``shippable_tail``): хвост журнала пересматривается по
   *текущим* меткам перед тем, как лечь в changeset. Между commit'ом и push
   документ мог стать restricted; если он ни разу не уезжал, отзыва нет
   (решение 20 реестра), и без этого шага хвост его тела уехал бы первым же
   push'ем. Снапшот этот шаг не требует: ``sync_capture.shipped_rows`` судит
   строки по текущему состоянию.
3. **Растяжка** (``check_ops``, ``check_row``): перед кодированием changeset'а
   и в каждой строке снапшота — нет ли там uid restricted-документа или
   строки его тела, ни как сущности, ни как значения. Сработала — гейт выше
   что-то пропустил: ``RestrictedLeakError`` (``SYNC-001``), отправка не
   состоится. Единственное законное исключение — операция отзыва (CLO-046):
   ``upsert`` документа ровно с ``sensitivity``.

Экспорт неотправленного (``export_pending``, CLO-032) обязан брать хвост той же
``shippable_tail`` и проверять его той же ``check_ops``, что и push (``push._pack``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cod_doc.infra import sync_registry as reg
from cod_doc.infra import sync_withdraw
from cod_doc.infra.sync_capture import RestrictedLeakError
from cod_doc.services.sync_service import _local

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sqlalchemy.orm import Session

    from cod_doc.services.sync_service.changeset import Op

__all__ = [
    "Restricted",
    "RestrictedLeakError",
    "check_ops",
    "check_row",
    "restricted",
    "shippable_tail",
]

logger = logging.getLogger(__name__)

_DOCUMENT = "document"
_UPSERT = "upsert"


@dataclass(frozen=True, slots=True)
class Restricted:
    """Что держит реплика из-за restricted-документов (``sync status``, растяжка)."""

    #: Число restricted-документов проекта.
    documents: int
    #: Строки документов и их тела (секции, комментарии, теги).
    rows: int
    #: uid документов и строк тела, у которых uid есть.
    uids: frozenset[str]


def restricted(session: Session, project_id: int) -> Restricted:
    """Restricted-документы проекта и строки их тела по текущему состоянию."""
    uids, rows, documents = sync_withdraw.restricted_uids(session, project_id)
    return Restricted(documents=documents, rows=rows, uids=uids)


def shippable_tail(session: Session, project_id: int) -> list[Op]:
    """Неотправленные операции проекта, пересмотренные по текущим меткам.

    Пишет в журнал: удаляет операции, которые держит restricted-документ, и
    вычищает придержанные колонки (``sync_withdraw.hold_restricted``).
    Вызывающий — упаковка push'а и экспорт неотправленного — зовет ее в
    транзакции, которая коммитится; провал позже откатывает и ее.
    """
    held = sync_withdraw.hold_restricted(session, project_id)
    if held:
        logger.warning(
            "sync: из хвоста убрано %d операций restricted-документов (Q-015)", len(held)
        )
    return _local.pending_ops(session, project_id)


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def _names(uids: frozenset[str], uid: str, *payloads: Mapping[str, object]) -> bool:
    if uid in uids:
        return True
    return any(s in uids for payload in payloads for s in _strings(dict(payload)))


def _is_withdraw(op: Op) -> bool:
    return (
        op.entity_kind == _DOCUMENT
        and op.kind == _UPSERT
        and reg.is_withdraw(_DOCUMENT, op.fields)
        and set(op.base) <= {reg.WITHDRAW_COLUMN}
    )


def check_ops(
    session: Session, project_id: int, ops: Iterable[Op], *, uids: frozenset[str] | None = None
) -> None:
    """Растяжка changeset'а: ни одна операция не называет restricted-строку.

    ``uids`` — готовый ``restricted(...).uids`` (иначе читается здесь).
    """
    held = restricted(session, project_id).uids if uids is None else uids
    if not held:
        return
    bad = [
        op.entity_kind
        for op in ops
        if not _is_withdraw(op) and _names(held, op.entity_uid, op.fields, op.base)
    ]
    if bad:
        kinds = ", ".join(sorted(set(bad)))
        msg = f"changeset несёт строки restricted-документа: {len(bad)} операций ({kinds})"
        raise RestrictedLeakError(msg)


def check_row(uids: frozenset[str], table: str, uid: str, fields: Mapping[str, object]) -> None:
    """Растяжка строки снапшота: строка не restricted и не ссылается на нее."""
    if uids and _names(uids, uid, fields):
        raise RestrictedLeakError(f"снапшот несёт строку restricted-документа ({table})")
