"""``fetch`` и ``pull``: чужие changeset'ы поверх своей головы (RFC 29 §3.6; CLO-013).

Сеть — до транзакций: сначала все объекты от ``refs/main`` remote до первого
уже применённого changeset'а скачиваются и проверяются (``changeset.decode``,
SYNC-102), потом каждый применяется в своей транзакции
(``apply.apply_changeset``). Что проверяется до первой записи, чтобы pull
не оставил БД наполовину:

1. **Схема** (SCN-085, Q-012). Changeset с ``schema_head`` новее головы
   миграций этой БД — ``SYNC-104`` «remote на схеме X, у вас Y — cod-doc
   update»; не применяется ни один changeset этого pull, даже более старые.
2. **Штампы** (``apply.check_clock``): реплика операций = реплика changeset'а,
   не своя реплика, не дальше ``hlc.MAX_DRIFT_MS`` впереди стенных часов —
   иначе SYNC-102.
3. **Расхождение.** Секция B делает только fast-forward: голова реплики —
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
совпадению каждой операции с журналом целиком и по своей реплике (``_own``).
Он не применяется, а только фиксируется — операции получают ``changeset =
sha``; схема и штампы сверяются и у него.

Конкурентные pull одной реплики: голова сдвигается CAS'ом первой записью
транзакции changeset'а (``_apply_one``). Второй pull той же цепочки видит
сдвинутую голову и уже применённый sha и идёт дальше, не применяя его снова.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from sqlalchemy import select, update

from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import self_replica
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
#: Потолок неприменённых changeset'ов за один pull: обход идёт по ``parents``
#: недоверенных объектов, и без потолка подделка заставила бы качать без конца.
#: Больше — работа clone со снапшота (CLO-029).
MAX_FETCH_CHANGESETS: Final = 10_000
#: Потолок родителей одного changeset'а: у merge-changeset'а их два (§3.6).
MAX_PARENTS: Final = 16


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
    """Порядок применения: каждый changeset после своих (скачанных) родителей.

    Адрес — sha содержимого, поэтому настоящий цикл невозможен; подделанный
    объект, который его всё же изображает, — SYNC-102.
    """
    order: list[str] = []
    done: set[str] = set()
    for start in sorted(found):
        visiting: set[str] = set()
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            sha, expanded = stack.pop()
            if sha in done:
                continue
            if expanded:
                visiting.discard(sha)
                done.add(sha)
                order.append(sha)
                continue
            if sha in visiting:
                msg = f"объект {sha}: цикл в parents"
                raise ObjectMissingError(msg)
            visiting.add(sha)
            stack.append((sha, True))
            for parent in found[sha].parents:
                if parent in visiting:
                    msg = f"объект {sha}: цикл в parents через {parent}"
                    raise ObjectMissingError(msg)
                if parent in found and parent not in done:
                    stack.append((parent, False))
    return order


def _download(
    store: RemoteStore, project: _local.ProjectRef, top: str, applied: set[str]
) -> dict[str, Changeset]:
    """Объекты от ``top`` до применённых; ограничены число и ветвление (недоверенный ввод)."""
    found: dict[str, Changeset] = {}
    queue = [top]
    while queue:
        sha = queue.pop()
        if sha in found or sha in applied:
            continue
        if len(found) >= MAX_FETCH_CHANGESETS:
            msg = f"от refs/main больше {MAX_FETCH_CHANGESETS} неприменённых changeset'ов"
            raise ObjectMissingError(msg)
        cs = decode(store.get_object(project.uid, sha), sha)
        if cs.project != project.uid:
            msg = f"объект {sha}: changeset проекта {cs.project}, а не {project.uid}"
            raise ObjectMissingError(msg)
        if len(cs.parents) > MAX_PARENTS:
            msg = f"объект {sha}: родителей {len(cs.parents)} > {MAX_PARENTS}"
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
    if remote_ref is None or remote_ref.sha == head:
        return _Fetched(ref, head, remote_ref, ())
    if remote_ref.sha in applied:
        # Remote позади реплики — тянуть нечего, но только если его голова
        # — наш предок; иначе история разошлась, и молчать нельзя.
        with transactional(sf, commit=False) as session:
            behind = _local.is_ancestor(session, remote_ref.sha, head)
        if behind:
            return _Fetched(ref, head, remote_ref, ())
        msg = f"refs/main {remote_ref.sha[:12]} не предок головы реплики"
        raise MergeRequiredError(msg)
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


def _own(session: Session, changesets: Sequence[tuple[str, Changeset]], me: str) -> set[str]:
    """Свои changeset'ы: реплика — эта, и каждая операция совпала с журналом целиком.

    Совпадение — вся операция (``op_id``, сущность, вид, поля и base в форме
    §10.4, ``hlc``, автор), а не только ``op_id``: ``op_id`` уезжает в открытом
    виде, и по нему одному remote провёл бы свои поля как «наши». ``op_id``
    из журнала с другим содержимым, частичное совпадение или «свой» changeset
    от чужой реплики — подделка, SYNC-102.
    """
    own: set[str] = set()
    for sha, cs in changesets:
        journal = _local.journal_ops(session, [op.op_id for op in cs.ops])
        # Операции, записанные применением этого же sha (параллельный pull),
        # — уже история, а не хвост: такой changeset чужой, не подделка.
        local = {i: op for i, (op, mark) in journal.items() if mark != sha}
        if not local:
            continue
        exact = [op for op in cs.ops if local.get(op.op_id) == op]
        if len(exact) != len(local) or len(exact) != len(cs.ops) or cs.replica != me:
            msg = (
                f"объект {sha}: подделка — op_id из журнала реплики с другим содержимым, "
                "неполное совпадение или чужая реплика"
            )
            raise ObjectMissingError(msg)
        own.add(sha)
    return own


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


_APPLIED: Final = "applied"
_ALREADY: Final = "already"
_MOVED: Final = "moved"


def _apply_one(
    sf: sessionmaker[Session],
    project: _local.ProjectRef,
    sha: str,
    cs: Changeset,
    *,
    expected: str | None,
    own: bool,
) -> tuple[str, str | None]:
    """Один changeset в своей транзакции, голова — CAS с ``expected`` на ``sha``.

    CAS — первая запись транзакции: параллельный pull той же цепочки ждёт
    замка записи и видит уже сдвинутую голову. Исход: ``applied``;
    ``already`` — этот sha применил кто-то другой (идём дальше от его
    головы); ``moved`` — голову сдвинуло что-то иное, pull останавливается.
    """
    with transactional(sf) as session:
        if _local.cas_local_head(session, project.row_id, expected, sha):
            if _local.is_applied(session, sha):
                # Голова уже на нём или дальше (параллельный pull успел всё):
                # CAS совпал случайно, применять второй раз нельзя.
                session.rollback()
                return _ALREADY, _local.local_head(session, project.row_id)
            if own:
                _settle_own(session, project, sha, cs)
            else:
                apply.apply_changeset(session, project, sha, cs)
            return _APPLIED, sha
        current = _local.local_head(session, project.row_id)
        if _local.is_applied(session, sha):
            return _ALREADY, current
        return _MOVED, current


def _check_all(
    sf: sessionmaker[Session], fetched: _Fetched
) -> tuple[set[str], list[tuple[str, Changeset]]]:
    """Всё, что проверяется до первой записи: схема, «свои», расхождение, штампы."""
    ref = fetched.project
    _check_schema(sf, fetched)
    with transactional(sf) as session:
        me = self_replica(session, ref.row_id)
        own = _own(session, fetched.changesets, me)
        pending = _pending(session, ref.row_id)
    _check_divergence(fetched.changesets, own, pending)
    for sha, cs in fetched.changesets:  # свои тоже: штампы и реплика сверяются
        apply.check_clock(cs, self_replica_uid=me, own=sha in own)
    return own, [(sha, cs) for sha, cs in fetched.changesets if sha not in own]


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

    own, foreign = _check_all(sf, fetched)
    if dry_run:
        # Что сделал бы pull: те же changeset'ы и та голова, на которой он встал бы.
        return PullResult(
            applied=[sha for sha, _ in foreign],
            ops=sum(len(cs.ops) for _, cs in foreign),
            fast_forward=True,
            head=fetched.changesets[-1][0],
        )

    _local.ensure_local_head(sf, ref.row_id)
    applied: list[str] = []
    ops = 0
    head = fetched.head
    for sha, cs in fetched.changesets:
        outcome, current = _apply_one(sf, ref, sha, cs, expected=head, own=sha in own)
        head = current
        if outcome == _MOVED:
            break
        if outcome == _APPLIED and sha not in own:
            applied.append(sha)
            ops += len(cs.ops)

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
