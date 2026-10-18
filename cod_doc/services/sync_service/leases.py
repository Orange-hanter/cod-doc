"""Аренда номеров и закрепление провизорных ID (RFC 29 §3.3, §10.1, §10.6; CLO-030).

Пять генераторов человеческих ID (§2: задачи ``ADO-229``, ``ADR-NNN``,
``US-NNN``, ``SCN-NNN``, ``Q-NNN``) считали ``max+1``: две офлайн-реплики
гарантированно выдавали один номер разным сущностям. С настроенным remote
(запись ``remotes`` проекта в ``~/.cod-doc/config.yaml``) номер берётся так:

1. **Аренда блока.** ``acquire`` берёт у remote блок номеров префикса —
   объект ``leases/<PREFIX>`` (``{"next", "block", "holders"}``), обновляемый
   ``cas_json``: на CAS-отказ — перечитать и повторить. Блоки двух реплик не
   пересекаются по построению: каждая сдвигает общий ``next`` своим CAS.
   Локальный остаток — ``sync_lease (next_number, end_number)``. Сеть — только
   в ``acquire`` (push, pull, ``refill``), никогда внутри транзакции записи.
2. **Выдача** (``lease_number``) — в транзакции создания сущности: следующий
   номер остатка условным ``UPDATE`` (параллельный генератор не получит тот
   же), занятый локально номер (явный ID пользователя) пропускается.
3. **Провизорный ID** — остаток пуст: ``PREFIX-~xxxx`` (4 символа base32),
   предупреждение ``ProvisionalIdWarning`` (``SYNC-107``). Он рабочий: на него
   ссылаются, его checkout'ят и пишут в коммит.
4. **Закрепление** (``pin_provisional``) — первый шаг push: провизорные ID
   получают номера из аренды. Строка, строковые ссылки на неё
   (``human_id_refs`` реестра, у ``to_ref`` — с дискриминатором ``to_kind``,
   у ``criterion`` — часть до ``#``) и неупакованный хвост журнала
   переписываются **на месте** под ``suspended``: провизорный ID ни разу не
   покидает реплику, и другим репликам не с чем сталкиваться. Старое имя —
   ``entity_alias (reason = pinned)`` и событие ``sync.alias_created``;
   поиски по ID (``current_id``) резолвят его. Упоминания в тексте (тела,
   описания, ``diff`` ревизий) не переписываются — их резолвит алиас.

Без remote генераторы работают как раньше (``enabled`` → ``False``).

**Двойник арендованного номера** (``keys.resolve_key``, CLO-023 оставил его
``MergeRequiredError``) по построению возникает только у номера, выданного мимо
аренды: явный ID или ``max+1`` без remote. Push fast-forward'ный — уехавшая
строка уезжала, когда реплика уже видела всё на remote; значит, в момент
совпадения своя строка ещё в хвосте. Она уступает номер (``yield_number``:
провизорный ID тем же переписыванием на месте) и закрепляется следующим push.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import warnings
from collections import Counter
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from sqlalchemy import or_, select, update

from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import self_replica
from cod_doc.infra.models import (
    Base,
    EntityAliasModel,
    PlanModel,
    ProjectModel,
    SyncLeaseModel,
    SyncOpModel,
)
from cod_doc.infra.remote.base import LEASES_PREFIX
from cod_doc.services import activity_service
from cod_doc.services.sync_service import _local
from cod_doc.services.sync_service.errors import (
    ObjectMissingError,
    ProvisionalIdWarning,
    PushRejectedError,
    RemoteUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.orm import Mapper, Session, sessionmaker

    from cod_doc.infra.remote.base import JsonValue, RemoteStore

__all__ = [
    "LEASED_COLUMNS",
    "LEASE_BLOCK",
    "LOW_WATER",
    "acquire",
    "claim_block",
    "current_id",
    "enabled",
    "enabled_for",
    "format_number",
    "is_provisional",
    "lease_number",
    "lease_status",
    "peek_number",
    "pin_provisional",
    "provisional_id",
    "refill",
    "refill_quietly",
    "scope_prefix",
    "yield_number",
]

logger = logging.getLogger("cod_doc.services.sync_service.leases")

#: Размер блока аренды (§3.3: «по умолчанию 20»). Блок больше, если за раз
#: нужно закрепить больше провизорных ID.
LEASE_BLOCK: Final = 20
#: Остаток меньше этого — ``refill`` на push/pull берёт новый блок. Блок,
#: примыкающий к остатку, его продлевает; иначе остаток сгорает (дыры в
#: нумерации допустимы, как после удаления).
LOW_WATER: Final = 5
#: Попыток CAS на ``leases/<PREFIX>``, прежде чем считать remote недоступным.
MAX_CAS_ATTEMPTS: Final = 16
#: Ширина номера: ``ADO-007``, ``ADR-012`` (генераторы до CLO-030 — так же).
NUMBER_WIDTH: Final = 3
#: Длина суффикса провизорного ID (§3.3: «4 символа base32»).
SUFFIX_LEN: Final = 4
_B32: Final = "abcdefghijklmnopqrstuvwxyz234567"
_PROVISIONAL_RE: Final = re.compile(rf"^(?P<prefix>[A-Z]{{1,5}})-~[a-z2-7]{{{SUFFIX_LEN}}}$")
_PROVISIONAL_TRIES: Final = 64
#: Раундов «аренда → закрепление» в одном push: между ними генератор мог
#: выдать ещё провизорных ID.
_PIN_ROUNDS: Final = 3
_PINNED: Final = "pinned"
_UID: Final = "uid"
_ROW_ID: Final = "row_id"
_CRITERION: Final = "criterion"

#: Таблица → колонка арендованного номера (``KeyKind.LEASED`` реестра).
LEASED_COLUMNS: Final[Mapping[str, str]] = MappingProxyType(
    {
        spec.table: key.columns[-1]
        for spec in reg.REGISTRY.values()
        for key in spec.natural_keys
        if key.kind is reg.KeyKind.LEASED
    }
)
#: Префиксы, которые берутся впрок всегда (у задач и историй префикс свой).
_FIXED_PREFIXES: Final = ("ADR", "Q", "SCN")
#: ``activity_event.scope_kind`` событий сущности таблицы: ``scope_id`` — её ID.
_EVENT_SCOPES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "task": "task",
        "adr": "adr",
        "user_story": "story",
        "scenario": "scenario",
        "open_question": "question",
    }
)
_TASK_ID_RE: Final = re.compile(r"^([A-Z]{2,5})-\d+$")
_STORY_ID_RE: Final = re.compile(r"^([A-Z]{2,4})-\d+$")

#: Префикс задачи из scope плана (``id_prefix_from_scope``): первые три буквы.
_MIN_PREFIX_LEN: Final = 2
_SCOPE_PREFIX_LEN: Final = 3
_FALLBACK_PREFIX: Final = "TSK"


# --- форма ID ---------------------------------------------------------------------


def scope_prefix(scope: str) -> str:
    """Префикс ID из scope плана: ``adoption-2026-08`` → ``ADO``; меньше двух букв — ``TSK``."""
    letters = [c for c in scope.upper() if c.isascii() and c.isalpha()]
    if len(letters) < _MIN_PREFIX_LEN:
        return _FALLBACK_PREFIX
    return "".join(letters[:_SCOPE_PREFIX_LEN])


def format_number(prefix: str, number: int) -> str:
    return f"{prefix}-{number:0{NUMBER_WIDTH}d}"


def is_provisional(value: object) -> bool:
    return isinstance(value, str) and _PROVISIONAL_RE.fullmatch(value) is not None


def _prefix_of(value: str) -> str:
    return value.split("-", 1)[0]


def provisional_id(prefix: str, seed: str | None = None) -> str:
    """``PREFIX-~xxxx``: суффикс от ``seed`` (детерминированно) или случайный."""
    if seed is None:
        suffix = "".join(secrets.choice(_B32) for _ in range(SUFFIX_LEN))
    else:
        digest = hashlib.sha256(seed.encode()).digest()
        suffix = "".join(_B32[b % len(_B32)] for b in digest[:SUFFIX_LEN])
    return f"{prefix}-~{suffix}"


# --- модели и чтение ------------------------------------------------------------


_MAPPERS: dict[str, Mapper[object]] = {}


def _mapper(table: str) -> Mapper[object]:
    if table not in _MAPPERS:
        for mapper in Base.registry.mappers:
            _MAPPERS.setdefault(mapper.local_table.name, mapper)  # type: ignore[attr-defined]
    return _MAPPERS[table]


def _taken(session: Session, project_id: int, table: str, human_id: str) -> bool:
    tbl = Base.metadata.tables[table]
    column = tbl.c[LEASED_COLUMNS[table]]
    stmt = select(tbl.c.row_id).where(tbl.c.project_id == project_id, column == human_id)
    return session.execute(stmt.limit(1)).first() is not None


def _lease_row(session: Session, project_id: int, prefix: str) -> SyncLeaseModel | None:
    """Строка аренды из БД, а не из identity map: её двигают core-``UPDATE``."""
    return session.scalars(
        select(SyncLeaseModel)
        .where(SyncLeaseModel.project_id == project_id, SyncLeaseModel.prefix == prefix)
        .execution_options(populate_existing=True)
    ).one_or_none()


def _remaining(row: SyncLeaseModel | None) -> int:
    return 0 if row is None else max(0, row.end_number - row.next_number + 1)


def lease_status(session: Session, project_id: int) -> dict[str, tuple[int, int]]:
    """Остатки аренды проекта: ``{префикс: (следующий, последний)}`` (``sync status``)."""
    rows = session.scalars(select(SyncLeaseModel).where(SyncLeaseModel.project_id == project_id))
    return {row.prefix: (row.next_number, row.end_number) for row in rows}


def enabled_for(project: str) -> bool:
    """У проекта настроен хоть один remote (``config.yaml``, §10.12)."""
    from cod_doc.config import Config

    return Config.has_project_remotes(project)


def enabled(session: Session, project_id: int) -> bool:
    """Генераторы проекта берут номера из аренды (remote настроен)."""
    slug = session.execute(
        select(ProjectModel.slug).where(ProjectModel.row_id == project_id)
    ).scalar_one_or_none()
    return slug is not None and enabled_for(slug)


# --- выдача (в транзакции создания, без сети) -----------------------------------


def _consume(session: Session, project_id: int, prefix: str, table: str) -> str | None:
    """Следующий свободный номер остатка; ``None`` — остаток пуст."""
    while True:
        row = _lease_row(session, project_id, prefix)
        if row is None or row.next_number > row.end_number:
            return None
        number = row.next_number
        result = session.execute(
            update(SyncLeaseModel)
            .where(
                SyncLeaseModel.project_id == project_id,
                SyncLeaseModel.prefix == prefix,
                SyncLeaseModel.next_number == number,
            )
            .values(next_number=number + 1)
            .execution_options(synchronize_session=False)
        )
        if not getattr(result, "rowcount", 0):
            continue  # номер забрал параллельный генератор
        candidate = format_number(prefix, number)
        if not _taken(session, project_id, table, candidate):
            return candidate


def _new_provisional(
    session: Session, project_id: int, table: str, prefix: str, seed: str | None = None
) -> str:
    candidate = provisional_id(prefix, seed)
    for _ in range(_PROVISIONAL_TRIES):
        if not _taken(session, project_id, table, candidate):
            return candidate
        candidate = provisional_id(prefix)
    msg = f"{table}: свободного провизорного ID {prefix}-~… не нашлось"
    raise RuntimeError(msg)


def lease_number(session: Session, project_id: int, prefix: str, *, kind: str = "task") -> str:
    """Номер из аренды (``ADO-241``) или провизорный (``ADO-~k3f2``) с ``SYNC-107``.

    ``kind`` — таблица сущности (``task``, ``adr``, ``user_story``, ``scenario``,
    ``open_question``): номер, занятый в ней явным ID, пропускается.
    """
    number = _consume(session, project_id, prefix, kind)
    if number is not None:
        return number
    provisional = _new_provisional(session, project_id, kind, prefix)
    msg = f"аренда номеров {prefix} пуста — выдан провизорный {provisional}; push закрепит номер"
    logger.warning("%s: %s", ProvisionalIdWarning.code, msg)
    warnings.warn(ProvisionalIdWarning(msg), stacklevel=2)
    return provisional


def peek_number(
    session: Session, project_id: int, prefix: str, *, kind: str = "task"
) -> str | None:
    """Какой номер выдаст следующий ``lease_number`` — подсказка, остаток не тратится."""
    row = _lease_row(session, project_id, prefix)
    if row is None:
        return None
    for number in range(row.next_number, row.end_number + 1):
        candidate = format_number(prefix, number)
        if not _taken(session, project_id, kind, candidate):
            return candidate
    return None


# --- алиасы ---------------------------------------------------------------------


def current_id(session: Session, project_id: int, table: str, human_id: str) -> str | None:
    """Нынешний ID сущности, которую звали ``human_id`` до закрепления; ``None`` — алиаса нет.

    Сущность могла после закрепления склеиться с двойником (алиас ``merged``
    по uid) — цепочка проходится до живого uid.
    """
    column = LEASED_COLUMNS.get(table)
    if column is None:
        return None
    row = session.get(EntityAliasModel, (project_id, table, human_id))
    if row is None:
        return None
    uid, seen = row.uid, {human_id}
    while uid not in seen:
        seen.add(uid)
        nxt = session.get(EntityAliasModel, (project_id, table, uid))
        if nxt is None:
            break
        uid = nxt.uid
    tbl = Base.metadata.tables[table]
    found = session.execute(
        select(tbl.c[column]).where(tbl.c.project_id == project_id, tbl.c.uid == uid)
    ).scalar_one_or_none()
    return None if found is None else str(found)


# --- аренда на remote (сеть, вне транзакции записи) ------------------------------


def _floor(session: Session, project_id: int, prefix: str) -> int:
    """Первый номер, не занятый локально ни одной из пяти таблиц и своей арендой."""
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    top = 0
    for table, column in LEASED_COLUMNS.items():
        tbl = Base.metadata.tables[table]
        values = session.execute(
            select(tbl.c[column]).where(
                tbl.c.project_id == project_id, tbl.c[column].like(f"{prefix}-%")
            )
        ).scalars()
        for value in values:
            match = pattern.match(str(value))
            if match is not None:
                top = max(top, int(match.group(1)))
    row = _lease_row(session, project_id, prefix)
    if row is not None:
        top = max(top, row.end_number)
    return top + 1


def _remote_next(key: str, body: Mapping[str, JsonValue]) -> int:
    value = body.get("next", 1)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        msg = f"{key}: поле next {value!r} — не номер"
        raise ObjectMissingError(msg)
    return value


def _store_block(
    session: Session, project_id: int, prefix: str, start: int, end: int
) -> tuple[int, int]:
    """Записать блок в ``sync_lease``; блок, примыкающий к остатку, его продлевает."""
    row = _lease_row(session, project_id, prefix)
    now = datetime.now(UTC)
    if row is None:
        session.add(
            SyncLeaseModel(
                project_id=project_id,
                prefix=prefix,
                next_number=start,
                end_number=end,
                leased_at=now,
            )
        )
        session.flush()
        return start, end
    if _remaining(row) and row.end_number + 1 == start:
        row.end_number = end
    else:
        row.next_number, row.end_number = start, end
    row.leased_at = now
    session.flush()
    return row.next_number, row.end_number


def acquire(
    sf: sessionmaker[Session],
    project: str,
    prefix: str,
    *,
    store: RemoteStore,
    need: int = 1,
) -> tuple[int, int]:
    """Взять у remote блок номеров ``prefix`` (CAS на ``leases/<PREFIX>``) → остаток.

    Первый блок начинается не ниже локального максимума: история до remote
    (``max+1``) номера не теряет. Блок — ``max(LEASE_BLOCK, need)`` номеров.
    """
    with transactional(sf) as session:
        ref = _local.project_ref(session, project)
        replica = self_replica(session, ref.row_id)
        floor = _floor(session, ref.row_id, prefix)
    start, end = claim_block(
        store, ref.uid, prefix, replica=replica, floor=floor, size=max(LEASE_BLOCK, need)
    )
    with transactional(sf) as session:
        return _store_block(session, ref.row_id, prefix, start, end)


def claim_block(
    store: RemoteStore,
    project_uid: str,
    prefix: str,
    *,
    replica: str,
    floor: int = 1,
    size: int = LEASE_BLOCK,
) -> tuple[int, int]:
    """Сдвинуть ``leases/<PREFIX>`` на ``size`` номеров своим CAS → ``(start, end)``.

    Транспортная половина ``acquire``, без БД: только ``get_json``/``cas_json``
    протокола ``RemoteStore``. CAS-отказ — перечитать и повторить; каждый
    успешный CAS двигает общий ``next``, поэтому блоки не пересекаются.
    """
    key = f"{LEASES_PREFIX}{prefix}"
    for _ in range(MAX_CAS_ATTEMPTS):
        got = store.get_json(project_uid, key)
        body, etag = got if got is not None else ({}, None)
        start = max(_remote_next(key, body), floor)
        end = start + size - 1
        raw = body.get("holders")
        holders: dict[str, JsonValue] = dict(raw) if isinstance(raw, dict) else {}
        holders[replica] = [start, end]
        new_body: dict[str, JsonValue] = {"next": end + 1, "block": LEASE_BLOCK, "holders": holders}
        if store.cas_json(project_uid, key, etag, new_body) is not None:
            return start, end
    msg = f"{key}: CAS не прошёл за {MAX_CAS_ATTEMPTS} попыток"
    raise RemoteUnavailableError(msg)


def _prefixes_in_use(session: Session, project_id: int) -> set[str]:
    """Префиксы, номера которых проекту нужны: фиксированные, планов, задач, историй."""
    out = set(_FIXED_PREFIXES)
    out.update(lease_status(session, project_id))
    for explicit, scope in session.execute(
        select(PlanModel.id_prefix, PlanModel.scope).where(PlanModel.project_id == project_id)
    ):
        out.add(explicit or scope_prefix(scope or ""))
    for table, pattern in (("task", _TASK_ID_RE), ("user_story", _STORY_ID_RE)):
        tbl = Base.metadata.tables[table]
        column = tbl.c[LEASED_COLUMNS[table]]
        for value in session.execute(
            select(column).where(tbl.c.project_id == project_id).distinct()
        ).scalars():
            match = pattern.match(str(value))
            if match is not None:
                out.add(match.group(1))
    return out


def refill(
    sf: sessionmaker[Session], project: str, *, store: RemoteStore
) -> dict[str, tuple[int, int]]:
    """Добрать аренду впрок там, где остаток меньше ``LOW_WATER`` (push/pull)."""
    with transactional(sf) as session:
        ref = _local.project_ref(session, project)
        prefixes = sorted(_prefixes_in_use(session, ref.row_id))
        remaining = {p: _remaining(_lease_row(session, ref.row_id, p)) for p in prefixes}
    return {
        prefix: acquire(sf, project, prefix, store=store)
        for prefix in prefixes
        if remaining[prefix] < LOW_WATER
    }


def refill_quietly(sf: sessionmaker[Session], project: str, store: RemoteStore) -> None:
    """``refill`` после push/pull, если у проекта настроен remote.

    Сбой сети здесь не роняет push/pull, которые уже прошли: без аренды
    генераторы выдадут провизорные ID, а следующий push их закрепит.
    """
    if not enabled_for(project):
        return
    try:
        refill(sf, project, store=store)
    except RemoteUnavailableError as exc:
        logger.warning("аренда номеров не добрана: %s", exc)


# --- закрепление ----------------------------------------------------------------


def _provisional_rows(session: Session, project_id: int) -> list[tuple[str, str, str]]:
    """``(таблица, провизорный ID, uid)`` всех провизорных строк проекта."""
    out: list[tuple[str, str, str]] = []
    for table, column in sorted(LEASED_COLUMNS.items()):
        tbl = Base.metadata.tables[table]
        rows = session.execute(
            select(tbl.c[column], tbl.c.uid)
            .where(tbl.c.project_id == project_id, tbl.c[column].like("%-~%"))
            .order_by(tbl.c.row_id)
        )
        out.extend((table, str(v), str(uid)) for v, uid in rows if is_provisional(v))
    return out


def _shipped(session: Session, project_id: int, table: str, uid: str) -> bool:
    found = session.execute(
        select(SyncOpModel.op_id)
        .where(
            SyncOpModel.project_id == project_id,
            SyncOpModel.entity_kind == table,
            SyncOpModel.entity_uid == uid,
            SyncOpModel.changeset.is_not(None),
        )
        .limit(1)
    ).first()
    return found is not None


def _record_alias(
    session: Session, project_id: int, table: str, alias: str, uid: str, new: str, author: str
) -> None:
    if session.get(EntityAliasModel, (project_id, table, alias)) is None:
        session.add(
            EntityAliasModel(
                project_id=project_id, entity_kind=table, alias=alias, uid=uid, reason=_PINNED
            )
        )
        session.flush()
    activity_service.emit_for_write(
        session,
        project_id,
        "sync.alias_created",
        author,
        scope_kind=table,
        scope_id=uid,
        payload={"alias": alias, "uid": uid, "reason": _PINNED, "to": new},
        summary=f"закрепление {table}: {alias} → {new}",
    )


def pin_provisional(
    sf: sessionmaker[Session], project: str, *, store: RemoteStore, author: str
) -> dict[str, str]:
    """Закрепить провизорные ID проекта номерами аренды (первый шаг push).

    → ``{провизорный: постоянный}``. Строка, уже уехавшая под провизорным ID,
    нарушала бы инвариант (провизорный ID реплику не покидает) — ``SYNC-103``.
    """
    pinned: dict[str, str] = {}
    for _ in range(_PIN_ROUNDS):
        with transactional(sf) as session:
            ref = _local.project_ref(session, project)
            rows = _provisional_rows(session, ref.row_id)
            if not rows:
                return pinned
            need = Counter(_prefix_of(human_id) for _, human_id, _ in rows)
            have = {p: _remaining(_lease_row(session, ref.row_id, p)) for p in need}
        for prefix, count in sorted(need.items()):
            if have[prefix] < count:
                acquire(sf, project, prefix, store=store, need=count)
        with transactional(sf) as session:
            for table, old, uid in rows:
                if not _taken(session, ref.row_id, table, old):
                    continue  # строку удалили или закрепили между транзакциями
                if _shipped(session, ref.row_id, table, uid):
                    msg = f"{table} {old}: провизорный ID уже на remote — закрепить на месте нельзя"
                    raise PushRejectedError(msg)
                new = _consume(session, ref.row_id, _prefix_of(old), table)
                if new is None:
                    continue  # остаток съел параллельный генератор — следующий раунд
                rename_in_tail(session, ref.row_id, table, old, new)
                _record_alias(session, ref.row_id, table, old, uid, new, author)
                pinned[old] = new
    with transactional(sf) as session:
        left = _provisional_rows(session, _local.project_ref(session, project).row_id)
    if left:
        msg = f"провизорные ID не закреплены за {_PIN_ROUNDS} раунда: {[h for _, h, _ in left]}"
        raise PushRejectedError(msg)
    return pinned


def yield_number(session: Session, project_id: int, table: str, human_id: str, uid: str) -> str:
    """Своя неуехавшая строка уступает номер двойнику: провизорный ID на месте.

    Зовёт ``keys.resolve_key`` внутри применения changeset'а. Следующий push
    закрепит строку номером из аренды.
    """
    if _shipped(session, project_id, table, uid):
        msg = (
            f"{table} {human_id}: арендованный номер у двух уехавших строк — "
            "история remote нарушает fast-forward push"
        )
        raise ObjectMissingError(msg)
    new = _new_provisional(session, project_id, table, _prefix_of(human_id), seed=uid)
    rename_in_tail(session, project_id, table, human_id, new)
    return new


# --- переписывание на месте ---------------------------------------------------------


def _rewritten(value: object, old: str, new: str, *, criterion: bool) -> str | None:
    if value == old:
        return new
    if criterion and isinstance(value, str) and value.startswith(f"{old}#"):
        return new + value[len(old) :]
    return None


def _ref_targets(table: str) -> list[tuple[str, str, str | None, tuple[str, ...]]]:
    """``(таблица, колонка, дискриминатор, его значения)`` ссылок на ID ``table``."""
    out: list[tuple[str, str, str | None, tuple[str, ...]]] = []
    for spec in reg.REGISTRY.values():
        for column, target in spec.human_id_refs.items():
            if target in reg.REGISTRY:
                if target == table:
                    out.append((spec.table, column, None, ()))
                continue
            kinds = tuple(k for k, t in reg.HUMAN_ID_KINDS.items() if t == table)
            if kinds:
                out.append((spec.table, column, target, kinds))
    return out


def _rewrite_ref_rows(
    session: Session, project_id: int, table: str, old: str, new: str
) -> dict[tuple[str, str], str]:
    """Строковые ссылки на ``old`` в строках проекта; → новые uid рёбер."""
    uid_map: dict[tuple[str, str], str] = {}
    for ref_table, column, disc, kinds in _ref_targets(table):
        tbl = Base.metadata.tables[ref_table]
        col = tbl.c[column]
        stmt = select(tbl)
        if disc is None:
            stmt = stmt.where(col == old)
        else:
            stmt = stmt.where(tbl.c[disc].in_(kinds), or_(col == old, col.like(f"{old}#%")))
        key_columns = reg.spec_for(ref_table).deterministic_uid or ()
        for found in session.execute(stmt).mappings().all():
            row = dict(found)
            if cap.row_project_id(session, ref_table, row) != project_id:
                continue
            value = _rewritten(
                row[column], old, new, criterion=disc is not None and row[disc] == _CRITERION
            )
            if value is None:
                continue
            values: dict[str, object] = {column: value}
            if column in key_columns and _UID in tbl.c:
                new_uid = cap.row_edge_uid(session, ref_table, {**row, column: value})
                if new_uid is not None and new_uid != row[_UID]:
                    values[_UID] = new_uid
                    uid_map[(ref_table, str(row[_UID]))] = new_uid
            session.execute(update(tbl).where(tbl.c.row_id == row[_ROW_ID]).values(**values))
    scope = _EVENT_SCOPES.get(table)
    if scope is not None:
        events = Base.metadata.tables["activity_event"]
        session.execute(
            update(events)
            .where(
                events.c.project_id == project_id,
                events.c.scope_kind == scope,
                events.c.scope_id == old,
            )
            .values(scope_id=new)
        )
    return uid_map


def _dumps(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _rewrite_payload(
    entity_kind: str, payload: dict[str, object], table: str, old: str, new: str
) -> bool:
    changed = False
    if entity_kind == table and payload.get(LEASED_COLUMNS[table]) == old:
        payload[LEASED_COLUMNS[table]] = new
        changed = True
    if entity_kind not in reg.REGISTRY:
        return changed
    for column, target in reg.spec_for(entity_kind).human_id_refs.items():
        if column not in payload:
            continue
        criterion = False
        if target in reg.REGISTRY:
            if target != table:
                continue
        else:
            kind = payload.get(target)
            # Правка без дискриминатора: провизорный ID однозначен и так.
            if kind is not None and reg.HUMAN_ID_KINDS.get(str(kind)) != table:
                continue
            criterion = kind == _CRITERION
        value = _rewritten(payload[column], old, new, criterion=criterion)
        if value is not None:
            payload[column] = value
            changed = True
    return changed


def _rewrite_tail(
    session: Session,
    project_id: int,
    table: str,
    old: str,
    new: str,
    uid_map: Mapping[tuple[str, str], str],
) -> None:
    """Неупакованные операции: ID, ссылки на него и uid рёбер, в чьём ключе он был."""
    tail = session.scalars(
        select(SyncOpModel).where(
            SyncOpModel.project_id == project_id, SyncOpModel.changeset.is_(None)
        )
    )
    for op in tail:
        fields = json.loads(op.fields)
        base = json.loads(op.base)
        changed = _rewrite_payload(op.entity_kind, fields, table, old, new)
        changed = _rewrite_payload(op.entity_kind, base, table, old, new) or changed
        remapped = uid_map.get((op.entity_kind, op.entity_uid))
        if (
            changed
            and remapped is None
            and op.entity_kind in reg.REGISTRY
            and reg.spec_for(op.entity_kind).deterministic_uid is not None
        ):
            source = fields if op.kind == "upsert" else base
            try:
                remapped = reg.edge_uid(op.entity_kind, source)
            except KeyError:  # в правке нет всех концов ребра
                remapped = None
        if remapped is not None and remapped != op.entity_uid:
            op.entity_uid = remapped
        if changed:
            op.fields = _dumps(fields)
            op.base = _dumps(base)
    session.flush()


def rename_in_tail(session: Session, project_id: int, table: str, old: str, new: str) -> None:
    """Переписать ID ``old`` → ``new`` на месте, без операций журнала.

    Строка сущности, строковые ссылки на неё (``human_id_refs``, uid рёбер с
    этим ID в ключе), ``scope_id`` её событий и неупакованный хвост. Годится
    только для ID, ни разу не уезжавшего: уехавшая история так не переписывается.
    """
    mapper = _mapper(table)
    model = mapper.class_
    column = Base.metadata.tables[table].c[LEASED_COLUMNS[table]]
    attr = mapper.get_property_by_column(column).key
    with cap.suspended(session):
        session.flush()
        rows = session.scalars(
            select(model).where(
                model.project_id == project_id,  # type: ignore[attr-defined]
                getattr(model, attr) == old,
            )
        ).all()
        for row in rows:
            setattr(row, attr, new)
        session.flush()
        uid_map = _rewrite_ref_rows(session, project_id, table, old, new)
        _rewrite_tail(session, project_id, table, old, new, uid_map)
        session.expire_all()
