"""``push``: упаковка хвоста журнала и fast-forward ``refs/main`` (RFC 29 §3.6; CLO-012).

Три шага, сеть — только между транзакциями (§10.6, функции принимают фабрику
сессий):

1. **Упаковка** (транзакция): неупакованные ``sync_op`` проекта по
   ``(hlc, op_id)`` → один changeset, ``parents = [локальная голова]``. Время
   в полях приводится к форме §10.4 по типу колонки (``_values.to_wire``);
   ``digest`` — дайджест состояния реплики в той же транзакции (CLO-014).
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

**Аренда номеров** (CLO-030). Перед каждой упаковкой провизорные ID
закрепляются номерами аренды (``leases.pin_provisional``): провизорный ID
реплику не покидает. Перед каждой, а не один раз: pull внутри повтора может
отдать номер своей неуехавшей строки двойнику (``leases.yield_number``). После
push с настроенным remote аренда добирается впрок (``leases.refill``); сбой
добора push не отменяет — он уже на remote.

**Снапшот** (CLO-029). После фиксации push пишет снапшот головы, если на
remote его ещё нет или с последнего набралось ``snapshot.SNAPSHOT_EVERY``
операций журнала (``snapshot.write_quietly``); сбой снапшота push не отменяет.

**Эпоха** (CLO-032). Перед обменом ``meta.json`` сверяется с эпохой и схемой
changeset'а (``epochs.check_push``): эпоха remote новее — ``SYNC-105``, схема
старше ``schema_min`` — ``SYNC-104``. Эпоха реплики новее — push проходит,
а снапшот после него пишется вне каденции и поднимает ``meta.epoch`` и
``schema_min`` (``snapshot.write_if_due``); событие ``sync.epoch_opened``.
Не записался снапшот эпохи — эпоха не открыта, и следующий push попробует
снова: условие (эпоха реплики новее ``meta.epoch``) никуда не делось.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from sqlalchemy import update

from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import self_replica
from cod_doc.infra.models import SyncOpModel
from cod_doc.services import activity_service
from cod_doc.services.sync_service import _local, checkout_lease, digest, epochs, leases
from cod_doc.services.sync_service.changeset import Changeset, encode, sha
from cod_doc.services.sync_service.errors import MergeRequiredError, PushRejectedError

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
    #: Закреплённые провизорные ID: ``{провизорный: постоянный}`` (CLO-030).
    pinned: dict[str, str] = field(default_factory=dict)
    #: Снапшот, записанный этим push'ем (sha головы), или ``None`` (CLO-029).
    snapshot: str | None = None
    #: Эпоха, которую открыл этот push (снапшот эпохи и ``meta.json``), CLO-032.
    epoch_opened: str | None = None


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
            epoch=_local.epoch(session),
            schema_head=_local.schema_head(session),
            hlc_max=max(op.hlc for op in ops),
            # Состояние реплики сейчас = родитель + хвост = состояние после
            # применения этого changeset'а (§3.7, CLO-014).
            digest=digest.compute(session, ref.row_id),
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


def _check_epoch(store: RemoteStore, packed: _Packed) -> None:
    """``meta.json`` remote против эпохи и схемы changeset'а (§3.6, CLO-032).

    Читается на каждой попытке: между повторами эпоху мог открыть другой push.
    """
    cs = packed.changeset
    epochs.check_push(
        epochs.read_meta(store, packed.project.uid),
        project=packed.project.slug,
        epoch=cs.epoch,
        schema_head=cs.schema_head,
        pending=len(packed.op_ids),
    )


def _emit_epoch_opened(
    sf: sessionmaker[Session], project: _local.ProjectRef, epoch: str, snap: str, author: str
) -> None:
    with transactional(sf) as session:
        activity_service.emit_for_write(
            session,
            project.row_id,
            "sync.epoch_opened",
            author,
            scope_kind="project",
            scope_id=project.slug,
            payload={"epoch": epoch, "snapshot": snap},
            summary=f"эпоха {epoch} открыта на remote, снапшот {snap[:12]}",
        )


def _exchange(store: RemoteStore, packed: _Packed) -> RefValue | None:
    """Загрузить объект и сдвинуть ref; ``None`` — remote не на нашей голове.

    Объект, который ``has_objects`` уже видит под этим sha, не перезаливается
    и не сверяется здесь: адрес — sha256 содержимого, а каждый читатель
    проверяет sha при ``decode`` (SYNC-102). Подложенный под наш адрес чужой
    объект поэтому не применится ни на одной реплике — он сломает fetch, но не
    подменит данные.
    """
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
    # pull зовёт только повтор; ленивый импорт держит push независимым от
    # применения чужих операций (pull → apply → link/search-сервисы).
    from cod_doc.services.sync_service import pull as pull_mod
    from cod_doc.services.sync_service import snapshot

    store = store if store is not None else _local.open_store(project, remote)
    retries = 0
    pinned: dict[str, str] = {}
    while True:
        pinned.update(leases.pin_provisional(sf, project, store=store, author=author))
        _, head, packed = _pack(sf, project, author)
        if packed is None:
            leases.refill_quietly(sf, project, store)
            checkout_lease.reconcile_quietly(sf, project, store, remote)  # CLO-031
            return PushResult(changesets=[], ops=0, head=head, retries=retries, pinned=pinned)
        _check_epoch(store, packed)
        new_ref = _exchange(store, packed)
        if new_ref is not None:
            _commit(sf, packed, new_ref, remote, author)
            written = snapshot.write_quietly(
                sf, project, store, head=packed.sha, digest_hex=packed.changeset.digest
            )
            snap = None if written is None else written.sha
            opened = None if written is None else written.epoch_opened
            if written is not None and opened is not None:
                _emit_epoch_opened(sf, packed.project, opened, written.sha, author)
            leases.refill_quietly(sf, project, store)
            checkout_lease.reconcile_quietly(sf, project, store, remote)  # CLO-031
            return PushResult(
                changesets=[packed.sha],
                ops=len(packed.op_ids),
                head=packed.sha,
                retries=retries,
                pinned=pinned,
                snapshot=snap,
                epoch_opened=opened,
            )
        if retries >= max_retries:
            suffix = f"; повторов: {max_retries}" if max_retries else ""
            raise PushRejectedError(f"{_BEHIND}{suffix}")
        retries += 1
        try:
            pulled = pull_mod.pull(sf, project, remote=remote, author=author, store=store)
        except MergeRequiredError as exc:
            msg = f"{_BEHIND}, но pull упёрся в несведённый случай: {exc}"
            raise PushRejectedError(msg) from exc
        if pulled.head == head:
            # Pull не сдвинул голову — следующая упаковка даст тот же отказ CAS.
            msg = f"{_BEHIND}; pull не сдвинул голову реплики, повтор бесполезен"
            raise PushRejectedError(msg)
