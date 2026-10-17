"""``push``: упаковка хвоста журнала и fast-forward ``refs/main`` (RFC 29 §3.6; CLO-012).

Три шага, сеть — только между транзакциями (§10.6, функции принимают фабрику
сессий):

1. **Упаковка** (транзакция): неупакованные ``sync_op`` проекта по
   ``(hlc, op_id)`` → один changeset, ``parents = [локальная голова]``. Время
   в полях приводится к форме §10.4 по типу колонки (``_values.to_wire``).
   Журнал не меняется: если дальше что-то сорвётся, хвост остаётся хвостом.
2. **Обмен** (без транзакции): ``get_ref`` → ``has_objects`` /
   ``put_object`` → ``cas_ref(expected = голова, которую реплика видела)``.
   Remote не на локальной голове — CAS заведомо не пройдёт, и объект не
   грузится.
3. **Фиксация** (транзакция, только после CAS): операции получают
   ``changeset = sha``, changeset — в локальный индекс, голова и
   ``sync_ref`` сдвигаются, событие ``sync.pushed`` (§10.11).

Отказ CAS значит «remote ушёл вперёд»: ``pull`` и упаковка заново — хвост
встаёт поверх новой головы, merge-changeset не нужен (хвост ещё не changeset).
Повторов — ``max_retries`` (по умолчанию 3, §3.6), дальше ``SYNC-103``;
``max_retries=0`` — отказ сразу (SCN-083, ``--no-retry``). ``pull``, которому
нужно слияние (свой хвост и чужие операции трогают одно и то же), — тоже
``SYNC-103``: без секции C push здесь не пройдёт.

Сбой между CAS и фиксацией оставляет remote на changeset'е, операции которого
локально ещё хвост. Следующий ``pull`` узнаёт свои ``op_id`` и только
фиксирует их (``pull._own``), не применяя второй раз.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from sqlalchemy import update

from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import self_replica
from cod_doc.infra.models import SyncOpModel
from cod_doc.services import activity_service
from cod_doc.services.sync_service import _local
from cod_doc.services.sync_service.changeset import Changeset, encode, sha
from cod_doc.services.sync_service.errors import PushRejectedError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.infra.remote.base import RefValue, RemoteStore

__all__ = ["DEFAULT_MAX_RETRIES", "PushResult", "push"]

DEFAULT_MAX_RETRIES: Final = 3
_CHUNK: Final = 500
_BEHIND: Final = "remote ушёл вперёд — сделайте pull"


@dataclass(frozen=True, slots=True)
class PushResult:
    changesets: list[str]
    ops: int
    head: str | None
    retries: int
    #: Закреплённые провизорные ID (CLO-030); до аренды номеров пусто.
    pinned: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Packed:
    project: _local.ProjectRef
    parent: str | None
    sha: str
    data: bytes
    changeset: Changeset
    op_ids: tuple[str, ...]


def _pack(
    sf: sessionmaker[Session], project: str, author: str
) -> tuple[_local.ProjectRef, str | None, _Packed | None]:
    with transactional(sf) as session:
        ref = _local.project_ref(session, project)
        head = _local.local_head(session, ref.row_id)
        ops = _local.pending_ops(session, ref.row_id)
        if not ops:
            return ref, head, None
        cs = Changeset(
            project=ref.uid,
            parents=[head] if head else [],
            replica=self_replica(session, ref.row_id),
            actor=author,
            epoch=_local.EPOCH,
            schema_head=_local.schema_head(session),
            hlc_max=max(op.hlc for op in ops),
            digest=_local.DIGEST_PENDING,
            ops=ops,
        )
    packed = _Packed(
        project=ref,
        parent=head,
        sha=sha(cs),
        data=encode(cs),
        changeset=cs,
        op_ids=tuple(op.op_id for op in cs.ops),
    )
    return ref, head, packed


def _exchange(store: RemoteStore, packed: _Packed) -> RefValue | None:
    """Загрузить объект и сдвинуть ref; ``None`` — remote не на нашей голове."""
    uid = packed.project.uid
    current = store.get_ref(uid, _local.MAIN)
    if (None if current is None else current.sha) != packed.parent:
        return None
    if packed.sha not in store.has_objects(uid, [packed.sha]):
        store.put_object(uid, packed.sha, packed.data)
    return store.cas_ref(uid, _local.MAIN, current, packed.sha)


def _commit(
    sf: sessionmaker[Session], packed: _Packed, new_ref: RefValue, remote: str, author: str
) -> None:
    ref = packed.project
    with transactional(sf) as session:
        ids = list(packed.op_ids)
        for i in range(0, len(ids), _CHUNK):
            session.execute(
                update(SyncOpModel)
                .where(SyncOpModel.op_id.in_(ids[i : i + _CHUNK]), SyncOpModel.changeset.is_(None))
                .values(changeset=packed.sha)
            )
        _local.record_changeset(session, ref, packed.sha, packed.changeset)
        _local.set_local_head(session, ref.row_id, packed.sha)
        _local.set_remote_ref(session, ref.row_id, remote, new_ref)
        activity_service.emit_for_write(
            session,
            ref.row_id,
            "sync.pushed",
            author,
            scope_kind="project",
            scope_id=ref.slug,
            payload={
                "remote": remote,
                "changesets": [packed.sha],
                "ops": len(ids),
                "head": packed.sha,
            },
            summary=f"push {remote}: {len(ids)} операций, голова {packed.sha[:12]}",
        )


def push(
    sf: sessionmaker[Session],
    project: str,
    *,
    remote: str = "origin",
    author: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    store: RemoteStore | None = None,
) -> PushResult:
    """Упаковать хвост журнала и fast-forward'нуть ``refs/main`` (§10.6).

    ``store`` — транспорт вместо ``remotes`` из конфига проекта (тесты, clone).
    """
    store = store if store is not None else _local.open_store(project, remote)
    retries = 0
    while True:
        _, head, packed = _pack(sf, project, author)
        if packed is None:
            return PushResult(changesets=[], ops=0, head=head, retries=retries)
        new_ref = _exchange(store, packed)
        if new_ref is not None:
            _commit(sf, packed, new_ref, remote, author)
            return PushResult(
                changesets=[packed.sha],
                ops=len(packed.op_ids),
                head=packed.sha,
                retries=retries,
            )
        if retries >= max_retries:
            suffix = f"; повторов: {max_retries}" if max_retries else ""
            raise PushRejectedError(f"{_BEHIND}{suffix}")
        # Повтор идёт через pull (CLO-013): до него догонять remote нечем.
        raise PushRejectedError(f"{_BEHIND}; pull ещё не реализован")
