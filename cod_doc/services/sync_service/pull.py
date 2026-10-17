"""``fetch`` и ``pull``: чужие changeset'ы поверх своей головы (RFC 29 §3.6; CLO-013).

Сеть — до транзакций: сначала все объекты от ``refs/main`` remote до первого
уже применённого changeset'а скачиваются и проверяются (``changeset.decode``,
SYNC-102), потом каждый применяется в своей транзакции
(``apply.apply_changeset``). Что проверяется до первой записи, чтобы pull
не оставил БД наполовину:

1. **Схема** (SCN-085, Q-012). Changeset с ``schema_head`` новее головы
   миграций этой БД — ``SYNC-104`` «remote на схеме X, у вас Y — cod-doc
   update»; не применяется ни один changeset этого pull, даже более старые.
2. **Расхождение.** Секция B делает только fast-forward: голова реплики —
   предок головы remote, а свой неупакованный хвост (``sync_op`` без
   ``changeset``) не трогает сущностей чужих операций. «Трогает» — та же
   сущность, или удаление с одной стороны того, на что ссылается FK другой.
   Иначе — ``MergeRequiredError`` до первой записи: LWW, 3-way merge, tombstone
   против правки — секция C. Непересекающийся хвост остаётся хвостом и уедет
   следующим push'ем поверх новой головы — merge-changeset не нужен
   (``fast_forward`` всегда ``True``).

Применённый changeset ложится в ``changeset`` с ``applied_at``, голова
реплики сдвигается на него — в той же транзакции. Повторный pull ничего не
скачивает: обход remote останавливается на применённом.

Свой changeset в истории remote (push упал между CAS и фиксацией) узнаётся по
``op_id``: его операции уже лежат в журнале хвостом. Он не применяется, а
только фиксируется — операции получают ``changeset = sha``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from sqlalchemy import select, update

from cod_doc.infra.db import transactional
from cod_doc.infra.models import ChangesetModel, SyncOpModel
from cod_doc.services import activity_service
from cod_doc.services.sync_service import _local, apply
from cod_doc.services.sync_service.changeset import Changeset, decode
from cod_doc.services.sync_service.errors import (
    MergeRequiredError,
    ObjectMissingError,
    SchemaMismatchError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.infra.remote.base import RefValue, RemoteStore

__all__ = ["PullResult", "fetch", "pull"]

_CHUNK: Final = 500
#: Сколько сущностей назвать в сообщении о расхождении.
_SHOWN: Final = 5


@dataclass(frozen=True, slots=True)
class PullResult:
    applied: list[str]
    ops: int
    #: Секция B не строит merge-changeset: всегда fast-forward (§3.6).
    fast_forward: bool
    #: Конфликты и склейки — секция C; до неё пусто.
    conflicts: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    head: str | None = None


@dataclass(frozen=True, slots=True)
class _Fetched:
    project: _local.ProjectRef
    head: str | None
    remote_ref: RefValue | None
    #: Неприменённые changeset'ы в порядке применения (родитель раньше ребёнка).
    changesets: tuple[tuple[str, Changeset], ...]


@dataclass(frozen=True, slots=True)
class _Pending:
    op_id: str
    entity_kind: str
    entity_uid: str
    kind: str
    refs: frozenset[str]


# --- fetch ------------------------------------------------------------------------


def _applied_shas(session: Session, project_id: int) -> set[str]:
    return set(
        session.scalars(
            select(ChangesetModel.sha).where(
                ChangesetModel.project_id == project_id, ChangesetModel.applied_at.is_not(None)
            )
        )
    )


def _topological(found: dict[str, Changeset]) -> list[str]:
    """Порядок применения: каждый changeset после своих (скачанных) родителей."""
    order: list[str] = []
    done: set[str] = set()
    for start in sorted(found):
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            sha, expanded = stack.pop()
            if sha in done:
                continue
            if expanded:
                done.add(sha)
                order.append(sha)
                continue
            stack.append((sha, True))
            stack.extend((p, False) for p in found[sha].parents if p in found and p not in done)
    return order


def _download(
    store: RemoteStore, project: _local.ProjectRef, top: str, applied: set[str]
) -> dict[str, Changeset]:
    found: dict[str, Changeset] = {}
    queue = [top]
    while queue:
        sha = queue.pop()
        if sha in found or sha in applied:
            continue
        cs = decode(store.get_object(project.uid, sha), sha)
        if cs.project != project.uid:
            msg = f"объект {sha}: changeset проекта {cs.project}, а не {project.uid}"
            raise ObjectMissingError(msg)
        found[sha] = cs
        queue.extend(cs.parents)
    return found


def _fetch(sf: sessionmaker[Session], project: str, store: RemoteStore) -> _Fetched:
    with transactional(sf, commit=False) as session:
        ref = _local.project_ref(session, project)
        head = _local.local_head(session, ref.row_id)
        applied = _applied_shas(session, ref.row_id)
    remote_ref = store.get_ref(ref.uid, _local.MAIN)
    if remote_ref is None or remote_ref.sha == head or remote_ref.sha in applied:
        return _Fetched(ref, head, remote_ref, ())
    found = _download(store, ref, remote_ref.sha, applied)
    if head is not None and not any(head in cs.parents for cs in found.values()):
        msg = f"голова реплики {head[:12]} не предок refs/main {remote_ref.sha[:12]}"
        raise MergeRequiredError(msg)
    return _Fetched(ref, head, remote_ref, tuple((s, found[s]) for s in _topological(found)))


def fetch(
    sf: sessionmaker[Session],
    project: str,
    *,
    remote: str = "origin",
    store: RemoteStore | None = None,
) -> list[str]:
    """Скачать и проверить новые changeset'ы, не применяя; вернуть их sha (§10.6).

    Запоминает увиденную голову remote в ``sync_ref``. Тела не хранятся:
    объекты неизменяемы, ``pull`` скачает их снова.
    """
    store = store if store is not None else _local.open_store(project, remote)
    fetched = _fetch(sf, project, store)
    with transactional(sf) as session:
        _local.set_remote_ref(session, fetched.project.row_id, remote, fetched.remote_ref)
    return [sha for sha, _ in fetched.changesets]


# --- проверки до записи -----------------------------------------------------------


def _check_schema(sf: sessionmaker[Session], fetched: _Fetched) -> None:
    with transactional(sf, commit=False) as session:
        local = _local.schema_head(session)
    for sha, cs in fetched.changesets:
        if _local.schema_is_newer(cs.schema_head, local):
            msg = (
                f"remote на схеме {cs.schema_head}, у вас {local} — cod-doc update "
                f"(changeset {sha[:12]})"
            )
            raise SchemaMismatchError(msg)


def _pending(session: Session, project_id: int) -> list[_Pending]:
    return [
        _Pending(op.op_id, op.entity_kind, op.entity_uid, op.kind, frozenset(apply.fk_uids(op)))
        for op in _local.pending_ops(session, project_id)
    ]


def _own(changesets: Sequence[tuple[str, Changeset]], pending: Sequence[_Pending]) -> set[str]:
    """Свои changeset'ы: все их операции лежат в журнале хвостом."""
    ids = {p.op_id for p in pending}
    return {sha for sha, cs in changesets if cs.ops and all(op.op_id in ids for op in cs.ops)}


def _check_divergence(
    changesets: Sequence[tuple[str, Changeset]], own: set[str], pending: Sequence[_Pending]
) -> None:
    own_ids = {op.op_id for sha, cs in changesets if sha in own for op in cs.ops}
    tail = [p for p in pending if p.op_id not in own_ids]
    if not tail:
        return
    entities = {(p.entity_kind, p.entity_uid) for p in tail}
    deleted = {p.entity_uid for p in tail if p.kind == "delete"}
    referenced = set().union(*(p.refs for p in tail))
    hits: list[str] = []
    for sha, cs in changesets:
        if sha in own:
            continue
        hits.extend(
            f"{op.entity_kind} {op.entity_uid}"
            for op in cs.ops
            if (op.entity_kind, op.entity_uid) in entities
            or (op.kind == "delete" and op.entity_uid in referenced)
            or (op.kind == "upsert" and apply.fk_uids(op) & deleted)
        )
    if hits:
        shown = ", ".join(sorted(set(hits))[:_SHOWN])
        msg = f"неупакованные локальные операции и чужие changeset'ы трогают одно: {shown}"
        raise MergeRequiredError(msg)


# --- применение -------------------------------------------------------------------


def _settle_own(session: Session, project: _local.ProjectRef, sha: str, cs: Changeset) -> None:
    ids = [op.op_id for op in cs.ops]
    for i in range(0, len(ids), _CHUNK):
        session.execute(
            update(SyncOpModel)
            .where(SyncOpModel.op_id.in_(ids[i : i + _CHUNK]), SyncOpModel.changeset.is_(None))
            .values(changeset=sha)
        )
    _local.record_changeset(session, project, sha, cs)


def pull(
    sf: sessionmaker[Session],
    project: str,
    *,
    remote: str = "origin",
    author: str,
    dry_run: bool = False,
    store: RemoteStore | None = None,
) -> PullResult:
    """Fetch + fast-forward: применить чужие changeset'ы поверх своей головы (§10.6)."""
    store = store if store is not None else _local.open_store(project, remote)
    fetched = _fetch(sf, project, store)
    ref = fetched.project
    if not fetched.changesets:
        if not dry_run:
            with transactional(sf) as session:
                _local.set_remote_ref(session, ref.row_id, remote, fetched.remote_ref)
        return PullResult(applied=[], ops=0, fast_forward=True, head=fetched.head)

    _check_schema(sf, fetched)
    with transactional(sf, commit=False) as session:
        pending = _pending(session, ref.row_id)
    own = _own(fetched.changesets, pending)
    _check_divergence(fetched.changesets, own, pending)

    foreign = [(sha, cs) for sha, cs in fetched.changesets if sha not in own]
    ops = sum(len(cs.ops) for _, cs in foreign)
    if dry_run:
        return PullResult(
            applied=[sha for sha, _ in foreign], ops=ops, fast_forward=True, head=fetched.head
        )

    applied: list[str] = []
    head = fetched.head
    for sha, cs in fetched.changesets:
        with transactional(sf) as session:
            if sha in own:
                _settle_own(session, ref, sha, cs)
            else:
                apply.apply_changeset(session, ref, sha, cs)
                applied.append(sha)
            _local.set_local_head(session, ref.row_id, sha)
        head = sha

    with transactional(sf) as session:
        _local.set_remote_ref(session, ref.row_id, remote, fetched.remote_ref)
        if applied:
            activity_service.emit_for_write(
                session,
                ref.row_id,
                "sync.pulled",
                author,
                scope_kind="project",
                scope_id=ref.slug,
                payload={
                    "remote": remote,
                    "applied": applied,
                    "ops": ops,
                    "conflicts": [],
                    "fast_forward": True,
                },
                summary=f"pull {remote}: {len(applied)} changeset'ов, {ops} операций",
            )
    return PullResult(applied=applied, ops=ops, fast_forward=True, head=head)
