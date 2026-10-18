"""Аренда замка checkout через remote (RFC 29 §3.6 «Checkout», Q-011, §10.6; CLO-031).

Без remote замок checkout — колонки ``task.checked_out_*`` одной БД (ADO-039),
и две реплики могли взять одну задачу, узнав об этом только на pull
(``double_checkout``, CLO-020). С настроенным remote (запись ``remotes``
проекта в ``~/.cod-doc/config.yaml``) замок сначала берётся на remote:

1. **Аренда.** ``leases/checkout/<task_uid>`` — JSON
   ``{"task_id", "agent", "replica", "acquired_at", "ttl_minutes"}``, который
   пишет ``cas_json(expected_etag=None)`` — «ключа нет». Тот же CAS, что у
   ``leases/<PREFIX>`` (CLO-030), поэтому на ``FileRemote`` и ``S3Remote``
   одинаково; снятие — ``delete_json`` по etag (на S3 — условное надгробие).
   Держатель — пара ``(agent, replica)``: тот же агент с другой машины — уже
   другой держатель, как и в ``status_merge``.
2. **Порядок.** ``checkout_service.checkout`` проверяет локальный замок и
   статус, берёт аренду (сеть — до первой записи в БД), потом пишет. Ошибка
   записи или отказ коммита — откат сессии, и аренда, взятая этим вызовом,
   снимается хуком ``after_soft_rollback`` (best-effort: исходная ошибка не
   подменяется). Занято — ``SYNC-108`` (``CheckoutLeaseHeldError``), сеть
   недоступна — ``SYNC-109`` (``CheckoutLeaseUnavailableError``).
3. **Снятие** — после коммита (хук ``after_commit``): ``release``,
   ``complete`` и ``release_stale`` снимают аренду своей реплики; чужую —
   только ``release_stale`` и ``release --force``, и только истёкшую по её
   собственному ``ttl_minutes``. Сеть не идёт внутри транзакции записи; не
   снятая из-за сети аренда истечёт по TTL.
4. **Офлайн** (``offline=True``, CLI ``--offline``) — провизорный замок: в
   БД пишется как обычно, а в ``sync_ref`` встаёт отметка
   ``("@checkout", <task_uid>)`` с ``fetched_at = checked_out_at``. Ближайший
   push/pull (``reconcile_quietly``) берёт по ней аренду: свободна — отметка
   снимается; занята другим — остаётся, а двойной захват ловит слияние
   (``status_merge`` → ``double_checkout``). Отметка, чей ``fetched_at`` не
   совпал с ``checked_out_at`` задачи, протухла: замок сменился (pull,
   разрешение конфликта, release) — она снимается без сети.

Псевдо-remote ``@checkout`` — тот же приём, что ``@local`` в ``_local``: имя
настоящего remote проходит ``[A-Za-z0-9_][A-Za-z0-9._-]*`` и с ним не совпадёт.

Разрешение ``double_checkout`` (``conflicts.resolve_conflict``) оставляет
одного держателя и после коммита переписывает аренду на него
(``schedule_take_over``) — безусловной заменой через CAS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import delete, event, select

from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import self_replica
from cod_doc.infra.models import ProjectModel, SyncRefModel, TaskModel
from cod_doc.infra.remote.base import LEASES_PREFIX
from cod_doc.services import activity_service
from cod_doc.services.sync_service import _local
from cod_doc.services.sync_service.errors import (
    CheckoutLeaseHeldError,
    CheckoutLeaseUnavailableError,
    ObjectMissingError,
    RemoteUnavailableError,
    SyncError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

    from cod_doc.infra.remote.base import JsonValue, RemoteStore

__all__ = [
    "CHECKOUT_PREFIX",
    "DEFAULT_TTL_MINUTES",
    "LEASED",
    "MARKER_REMOTE",
    "OFFLINE",
    "Holder",
    "claim",
    "drop",
    "lease_key",
    "mark_offline",
    "read",
    "reconcile",
    "reconcile_quietly",
    "remote_for",
    "schedule_release",
    "schedule_take_over",
    "take_over",
    "unmark",
]

logger = logging.getLogger("cod_doc.services.sync_service.checkout_lease")

CHECKOUT_PREFIX: Final = f"{LEASES_PREFIX}checkout/"
#: Псевдо-remote отметок провизорного замка в ``sync_ref`` (см. докстринг).
MARKER_REMOTE: Final = "@checkout"
#: Исход аренды у ``CheckoutResult.lease``.
OFFLINE: Final = "offline"
LEASED: Final = "leased"
#: TTL аренды — тот же, что умолчание ``checkout_service.release_stale``.
DEFAULT_TTL_MINUTES: Final = 30
#: Попыток CAS, прежде чем считать remote недоступным.
MAX_CAS_ATTEMPTS: Final = 8
_DEFAULT_REMOTE: Final = "origin"
_PENDING_KEY: Final = "cod_doc.checkout_lease.pending"
_HOOKED_KEY: Final = "cod_doc.checkout_lease.hooked"
#: Сравнение моментов — до миллисекунд: форма времени на проводе (§10.4).
_MS: Final = 1000

#: Транспорт remote; тесты подменяют его, чтобы изобразить сбой сети.
open_store: Callable[[str, str], RemoteStore] = _local.open_store


# --- значение аренды ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Holder:
    """Держатель аренды: агент на реплике, с какого момента и на сколько."""

    task_id: str
    agent: str
    replica: str
    acquired_at: datetime
    ttl_minutes: int = DEFAULT_TTL_MINUTES

    def body(self) -> dict[str, JsonValue]:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "replica": self.replica,
            "acquired_at": _utc(self.acquired_at).isoformat(),
            "ttl_minutes": self.ttl_minutes,
        }

    @classmethod
    def parse(cls, key: str, body: Mapping[str, JsonValue]) -> Holder:
        task_id, agent, replica = body.get("task_id"), body.get("agent"), body.get("replica")
        acquired, ttl = body.get("acquired_at"), body.get("ttl_minutes", DEFAULT_TTL_MINUTES)
        if not (
            isinstance(task_id, str)
            and isinstance(agent, str)
            and isinstance(replica, str)
            and isinstance(acquired, str)
            and isinstance(ttl, int)
            and not isinstance(ttl, bool)
        ):
            msg = f"{key}: аренда checkout не по форме — {dict(body)!r}"
            raise ObjectMissingError(msg)
        try:
            at = _utc(datetime.fromisoformat(acquired))
        except ValueError:
            msg = f"{key}: acquired_at {acquired!r} — не момент ISO-8601"
            raise ObjectMissingError(msg) from None
        return cls(task_id=task_id, agent=agent, replica=replica, acquired_at=at, ttl_minutes=ttl)

    def same(self, other: Holder) -> bool:
        return (self.agent, self.replica) == (other.agent, other.replica)

    def expired(self, now: datetime) -> bool:
        return _utc(self.acquired_at) + timedelta(minutes=self.ttl_minutes) < _utc(now)

    def held_error(self) -> CheckoutLeaseHeldError:
        return CheckoutLeaseHeldError(
            task_id=self.task_id,
            locked_by=self.agent,
            replica=self.replica,
            acquired_at=self.acquired_at,
        )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _same_instant(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return False
    return int(_utc(a).timestamp() * _MS) == int(_utc(b).timestamp() * _MS)


def lease_key(task_uid: str) -> str:
    return f"{CHECKOUT_PREFIX}{task_uid}"


# --- транспорт (без БД) ---------------------------------------------------------------


def read(store: RemoteStore, project_uid: str, task_uid: str) -> tuple[Holder, str] | None:
    """Нынешняя аренда задачи и её etag; ``None`` — свободна."""
    key = lease_key(task_uid)
    got = store.get_json(project_uid, key)
    if got is None:
        return None
    body, etag = got
    return Holder.parse(key, body), etag


def claim(
    store: RemoteStore, project_uid: str, task_uid: str, holder: Holder
) -> tuple[Holder, str, bool]:
    """Взять аренду CAS'ом → ``(держатель, etag, взята_этим_вызовом)``.

    Аренда того же держателя (агент и реплика) — идемпотентно, ``False``.
    Чужая — ``SYNC-108`` сразу, даже истёкшая: её снимает ``release_stale``,
    а не следующий желающий (работа над задачей длиннее TTL — норма).
    """
    key = lease_key(task_uid)
    for _ in range(MAX_CAS_ATTEMPTS):
        current = read(store, project_uid, task_uid)
        if current is not None:
            existing, etag = current
            if existing.same(holder):
                return existing, etag, False
            raise existing.held_error()
        etag_new = store.cas_json(project_uid, key, None, holder.body())
        if etag_new is not None:
            return holder, etag_new, True
    msg = f"{key}: CAS не прошёл за {MAX_CAS_ATTEMPTS} попыток"
    raise RemoteUnavailableError(msg)


def take_over(store: RemoteStore, project_uid: str, task_uid: str, holder: Holder) -> str:
    """Записать аренду ``holder`` поверх любой (разрешение ``double_checkout``) → etag."""
    key = lease_key(task_uid)
    for _ in range(MAX_CAS_ATTEMPTS):
        current = store.get_json(project_uid, key)
        etag = None if current is None else current[1]
        etag_new = store.cas_json(project_uid, key, etag, holder.body())
        if etag_new is not None:
            return etag_new
    msg = f"{key}: CAS не прошёл за {MAX_CAS_ATTEMPTS} попыток"
    raise RemoteUnavailableError(msg)


def drop(
    store: RemoteStore,
    project_uid: str,
    task_uid: str,
    *,
    replica: str,
    agent: str | None = None,
    etag: str | None = None,
    expired_ok: bool = False,
    now: datetime | None = None,
) -> bool:
    """Снять аренду, если она своя; ``True`` — аренды больше нет.

    Своя — реплика ``replica`` и (если задан) агент ``agent``; ``etag`` сужает
    до той самой версии, что взял вызов. ``expired_ok`` снимает и чужую,
    истёкшую по её ``ttl_minutes`` (``release_stale``, ``release --force``).
    """
    key = lease_key(task_uid)
    for _ in range(MAX_CAS_ATTEMPTS):
        current = read(store, project_uid, task_uid)
        if current is None:
            return True
        holder, current_etag = current
        mine = holder.replica == replica and agent in (None, holder.agent)
        if etag is not None and current_etag != etag:
            mine = False
        if not mine and not (expired_ok and holder.expired(now or datetime.now(UTC))):
            return False
        if store.delete_json(project_uid, key, current_etag):
            return True
    msg = f"{key}: удаление по etag не прошло за {MAX_CAS_ATTEMPTS} попыток"
    raise RemoteUnavailableError(msg)


# --- remote проекта -------------------------------------------------------------------


def remote_for(session: Session, project_id: int) -> tuple[str, str, str] | None:
    """``(slug, имя remote, uid проекта)``; ``None`` — remote не настроен (ADO-039).

    Remote — ``origin``, а без него — первый по имени: у checkout нет опции
    ``--remote``, как у push/pull.
    """
    from cod_doc.config import Config
    from cod_doc.services.sync_service import remotes

    row = session.execute(
        select(ProjectModel.slug, ProjectModel.uid).where(ProjectModel.row_id == project_id)
    ).one_or_none()
    if row is None or not row.uid or not Config.has_project_remotes(row.slug):
        return None
    names = [spec.name for spec in remotes.list_for(row.slug)]
    if not names:
        return None
    name = _DEFAULT_REMOTE if _DEFAULT_REMOTE in names else names[0]
    return row.slug, name, str(row.uid)


def _store(slug: str, remote: str) -> RemoteStore:
    return open_store(slug, remote)


def unavailable(exc: RemoteUnavailableError) -> CheckoutLeaseUnavailableError:
    msg = f"аренда checkout не взята — remote недоступен ({exc}); без сети: --offline"
    return CheckoutLeaseUnavailableError(msg)


def claim_for_checkout(
    slug: str, remote: str, project_uid: str, task_uid: str, holder: Holder
) -> tuple[Holder, str, bool]:
    """``claim`` с переводом сбоя сети в ``SYNC-109``; ``SYNC-108`` — как есть."""
    try:
        return claim(_store(slug, remote), project_uid, task_uid, holder)
    except CheckoutLeaseHeldError:
        raise
    except RemoteUnavailableError as exc:
        raise unavailable(exc) from exc


# --- хуки сессии: сеть после коммита или отката ----------------------------------------


@dataclass(frozen=True, slots=True)
class _Pending:
    task_uid: str
    on_commit: Callable[[], None] | None
    on_rollback: Callable[[], None] | None


def _pending(session: Session) -> list[_Pending]:
    pending: list[_Pending] = session.info.setdefault(_PENDING_KEY, [])
    return pending


def _run(actions: list[Callable[[], None]]) -> None:
    for action in actions:
        try:
            action()
        except Exception as exc:
            logger.warning("аренда checkout: действие после транзакции не выполнено: %s", exc)


def _after_commit(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, [])
    _run([p.on_commit for p in pending if p.on_commit is not None])


def _after_soft_rollback(session: Session, previous: SessionTransaction) -> None:
    if previous.parent is not None:
        return  # откат savepoint'а — внешняя транзакция ещё может закоммититься
    pending = session.info.pop(_PENDING_KEY, [])
    _run([p.on_rollback for p in pending if p.on_rollback is not None])


def _hook(session: Session) -> None:
    if session.info.get(_HOOKED_KEY):
        return
    event.listen(session, "after_commit", _after_commit)
    event.listen(session, "after_soft_rollback", _after_soft_rollback)
    session.info[_HOOKED_KEY] = True


def defer(
    session: Session,
    task_uid: str,
    *,
    on_commit: Callable[[], None] | None = None,
    on_rollback: Callable[[], None] | None = None,
) -> None:
    """Сетевое действие после исхода транзакции; одно действие-коммит на задачу."""
    _hook(session)
    pending = _pending(session)
    if on_commit is not None and any(
        p.task_uid == task_uid and p.on_commit is not None for p in pending
    ):
        on_commit = None
    if on_commit is None and on_rollback is None:
        return
    pending.append(_Pending(task_uid, on_commit, on_rollback))


def schedule_release(
    session: Session,
    project_id: int,
    task_uid: str,
    *,
    agent: str | None,
    expired_ok: bool = False,
) -> str | None:
    """После коммита снять аренду своей реплики (``agent=None`` — любого агента).

    Возвращает имя remote; ``None`` — remote не настроен, планировать нечего.
    Отметка провизорного замка снимается сразу, в этой транзакции.
    """
    target = remote_for(session, project_id)
    if target is None:
        return None
    unmark(session, project_id, task_uid)
    slug, remote, project_uid = target
    replica = self_replica(session, project_id)

    def release() -> None:
        drop(
            _store(slug, remote),
            project_uid,
            task_uid,
            replica=replica,
            agent=agent,
            expired_ok=expired_ok,
        )

    defer(session, task_uid, on_commit=release)
    return remote


def schedule_take_over(session: Session, project_id: int, task_uid: str, holder: Holder) -> bool:
    """После коммита переписать аренду на ``holder`` (разрешение ``double_checkout``)."""
    target = remote_for(session, project_id)
    if target is None:
        return False
    slug, remote, project_uid = target

    def rewrite() -> None:
        take_over(_store(slug, remote), project_uid, task_uid, holder)

    defer(session, task_uid, on_commit=rewrite)
    return True


def schedule_rollback_drop(
    session: Session,
    slug: str,
    remote: str,
    project_uid: str,
    task_uid: str,
    holder: Holder,
    etag: str,
) -> None:
    """Аренда, взятая этим вызовом, снимается, если транзакция записи откатится."""

    def undo() -> None:
        drop(
            _store(slug, remote),
            project_uid,
            task_uid,
            replica=holder.replica,
            agent=holder.agent,
            etag=etag,
        )

    defer(session, task_uid, on_rollback=undo)


# --- отметки провизорного замка -------------------------------------------------------


def mark_offline(
    session: Session, project_id: int, task_uid: str, checked_out_at: datetime
) -> None:
    """Провизорный замок: ближайший push/pull возьмёт по нему аренду."""
    stmt = _local._insert_ref(session).values(
        project_id=project_id,
        remote=MARKER_REMOTE,
        name=task_uid,
        sha=OFFLINE,
        etag=None,
        fetched_at=checked_out_at,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=list(_local._REF_KEY),
            set_={"sha": OFFLINE, "fetched_at": checked_out_at},
        )
    )


def unmark(session: Session, project_id: int, task_uid: str) -> None:
    session.execute(
        delete(SyncRefModel).where(
            SyncRefModel.project_id == project_id,
            SyncRefModel.remote == MARKER_REMOTE,
            SyncRefModel.name == task_uid,
        )
    )


def is_marked(session: Session, project_id: int, task_uid: str) -> bool:
    return (
        session.execute(
            select(SyncRefModel.name).where(
                SyncRefModel.project_id == project_id,
                SyncRefModel.remote == MARKER_REMOTE,
                SyncRefModel.name == task_uid,
            )
        ).first()
        is not None
    )


# --- сверка провизорных замков на push/pull --------------------------------------------


@dataclass(frozen=True, slots=True)
class _Marked:
    task_uid: str
    task_id: str | None
    agent: str | None
    checked_out_at: datetime | None
    marked_at: datetime | None


def _marked(session: Session, project_id: int) -> list[_Marked]:
    rows = session.execute(
        select(
            SyncRefModel.name,
            SyncRefModel.fetched_at,
            TaskModel.task_id,
            TaskModel.checked_out_by,
            TaskModel.checked_out_at,
        )
        .outerjoin(
            TaskModel,
            (TaskModel.uid == SyncRefModel.name) & (TaskModel.project_id == project_id),
        )
        .where(SyncRefModel.project_id == project_id, SyncRefModel.remote == MARKER_REMOTE)
        .order_by(SyncRefModel.name)
    )
    return [
        _Marked(
            task_uid=row.name,
            task_id=row.task_id,
            agent=row.checked_out_by,
            checked_out_at=row.checked_out_at,
            marked_at=row.fetched_at,
        )
        for row in rows
    ]


def reconcile(
    sf: sessionmaker[Session], project: str, *, store: RemoteStore, remote: str
) -> dict[str, str]:
    """Провизорные замки → аренда (§3.6): ``{task_id: "leased" | "held" | "stale"}``.

    ``leased`` — аренда взята, отметка снята; ``held`` — аренду держит другой,
    отметка остаётся (двойной захват откроет слияние); ``stale`` — замок
    сменился после офлайн-checkout, отметка снята без сети.
    """
    with transactional(sf) as session:
        ref = _local.project_ref(session, project)
        replica = self_replica(session, ref.row_id)
        marked = _marked(session, ref.row_id)
    out: dict[str, str] = {}
    leased: list[_Marked] = []
    stale: list[_Marked] = []
    for item in marked:
        if (
            item.agent is None
            or item.task_id is None
            or not _same_instant(item.checked_out_at, item.marked_at)
        ):
            stale.append(item)
            out[item.task_id or item.task_uid] = "stale"
            continue
        assert item.checked_out_at is not None
        holder = Holder(
            task_id=item.task_id,
            agent=item.agent,
            replica=replica,
            acquired_at=item.checked_out_at,
        )
        try:
            claim(store, ref.uid, item.task_uid, holder)
        except CheckoutLeaseHeldError as exc:
            logger.warning("провизорный замок %s не закреплён: %s", item.task_id, exc)
            out[item.task_id] = "held"
            continue
        leased.append(item)
        out[item.task_id] = "leased"
    if not (leased or stale):
        return out
    with transactional(sf) as session:
        for item in (*leased, *stale):
            unmark(session, ref.row_id, item.task_uid)
        for item in leased:
            assert item.agent is not None and item.task_id is not None
            activity_service.emit_for_write(
                session,
                ref.row_id,
                "task.checkout_leased",
                item.agent,
                scope_kind="task",
                scope_id=item.task_id,
                payload={"remote": remote, "offline": True},
                summary=f"провизорный замок {item.task_id} закреплён арендой на {remote}",
            )
    return out


def reconcile_quietly(
    sf: sessionmaker[Session], project: str, store: RemoteStore, remote: str
) -> None:
    """``reconcile`` после push/pull; сбой сети push/pull не отменяет."""
    try:
        reconcile(sf, project, store=store, remote=remote)
    except SyncError as exc:
        logger.warning("провизорные замки не сверены с remote: %s", exc)
