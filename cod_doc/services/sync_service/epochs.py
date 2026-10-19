"""Эпохи на remote и ``export-pending`` (RFC 29 §3.6, Q-012; CLO-032).

Какие миграции открывают эпоху — ``infra/sync_epochs.py`` (застывший реестр и
гейт). Здесь — что из этого следует для обмена:

1. **meta.json** (§10.1) — ``epoch`` и ``schema_min`` remote. Тело пишут чужие
   машины, поэтому оно проверяется (``parse_meta``): ``v`` = 1, ``project_uid``
   = uid проекта, ``epoch``/``schema_min`` — имена ревизий, ``snapshot_every`` —
   целое в пределах. Не по форме — ``SYNC-102``. Незнакомое коду, но
   правильно записанное имя эпохи — эпоха новее своей (её открыл релиз,
   которого у реплики нет): ответ — ``cod-doc update``, а не «объект испорчен».
2. **push** (``check_push``): эпоха реплики старше ``meta.epoch`` —
   ``SYNC-105``: незапушенные операции старой эпохи на новую схему не лягут,
   выход — ``export-pending``. Схема старше ``meta.schema_min`` — ``SYNC-104``.
   Эпоха реплики новее — push проходит и открывает эпоху: снапшот
   ``snapshots/<эпоха>/<голова>`` и CAS ``meta.json`` с новыми ``epoch`` и
   ``schema_min`` (``snapshot.write_if_due``), событие ``sync.epoch_opened``.
3. **pull** (``check_pull``): эпоха реплики старше ``meta.epoch`` — ``SYNC-105``
   до первой записи. Changeset'ы новой эпохи на старой схеме не применяются,
   а операции хвоста старой эпохи уже не уедут: сначала ``export-pending``
   (если хвост есть), потом ``cod-doc update``.
4. **export-pending** (``render_pending``/``export_pending``): неупакованные
   операции проекта — в файл, в форме changeset'а §10.4 (``to_dict``) внутри
   конверта ``{"v", "kind": "pending", "slug", "exported_at", "changeset"}``.
   Операции берутся тем же ``gate.shippable_tail`` и проверяются той же
   растяжкой ``gate.check_ops``, что у push (CLO-033): уезжающие поля, время
   §10.4, restricted не попадает и в файл. Как и упаковка push'а, хвост
   по текущим меткам вычищает из журнала операции ставших restricted
   документов; других правок журнала нет. Обратного импорта нет (RFC: «для ручного переноса»): операции
   старой эпохи по определению не ложатся на новую схему, переносит их
   человек правкой через CLI/MCP. Проект читается колонками, а не строкой
   модели, чтобы экспорт работал и когда код новее схемы БД (``update``
   остановился на ``SYNC-106``); гейт restricted читает документы моделями —
   на такой схеме он сработает, пока миграция эпохи не тронула их таблицы.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from cod_doc.infra import sync_epochs
from cod_doc.infra.db import transactional
from cod_doc.infra.models import ProjectModel, ReplicaModel
from cod_doc.infra.remote.base import META_KEY, check_name
from cod_doc.services.sync_service import _local, gate
from cod_doc.services.sync_service.changeset import (
    Changeset,
    canonical_json,
    format_time,
    to_dict,
)
from cod_doc.services.sync_service.errors import (
    EpochPassedError,
    ObjectMissingError,
    RemoteConfigError,
    SchemaMismatchError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.infra.remote.base import JsonValue, RemoteStore

__all__ = [
    "EXPORT_KIND",
    "META_VERSION",
    "RemoteMeta",
    "check_pull",
    "check_push",
    "export_pending",
    "opens_epoch",
    "parse_meta",
    "pending_phrase",
    "read_meta",
    "render_pending",
]

META_VERSION: Final = 1
EXPORT_KIND: Final = "pending"
EXPORT_VERSION: Final = 1
#: Потолок имени ревизии в meta.json: длиннее имён в цепочке не бывает.
MAX_REVISION_LEN: Final = 128
#: Пределы ``snapshot_every``: ноль и отрицательное ломают каденцию,
#: астрономическое — отключает снапшоты.
MIN_SNAPSHOT_EVERY: Final = 1
MAX_SNAPSHOT_EVERY: Final = 10_000_000
#: Права файла экспорта: в операциях — тексты документов.
_EXPORT_MODE: Final = 0o600
_ACTOR: Final = "export-pending"
#: Окончания «операция» по числу (1 / 2–4 / 5–20).
_FEW_MIN: Final = 2
_FEW_MAX: Final = 4
_TEEN_MIN: Final = 11
_TEEN_MAX: Final = 14
_DECIMAL: Final = 10
_HUNDRED: Final = 100


@dataclass(frozen=True, slots=True)
class RemoteMeta:
    epoch: str
    schema_min: str
    etag: str
    body: dict[str, JsonValue]


def _bad(reason: str) -> ObjectMissingError:
    return ObjectMissingError(f"meta.json: {reason}")


def _revision(value: object, what: str) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= MAX_REVISION_LEN:
        raise _bad(f"{what}: нужна строка 1…{MAX_REVISION_LEN} символов")
    try:
        return check_name(value, what)
    except ValueError as exc:
        raise _bad(str(exc)) from None


def parse_meta(body: dict[str, JsonValue], etag: str, project_uid: str) -> RemoteMeta:
    """Проверить тело ``meta.json`` (недоверенный ввод); не по форме — ``SYNC-102``."""
    version = body.get("v")
    if type(version) is not int or version != META_VERSION:
        raise _bad(f"v: поддерживается {META_VERSION}, получено {version!r}")
    if body.get("project_uid") != project_uid:
        raise _bad(f"project_uid {body.get('project_uid')!r} ≠ {project_uid}")
    every = body.get("snapshot_every")
    if every is not None and (
        type(every) is not int or not MIN_SNAPSHOT_EVERY <= every <= MAX_SNAPSHOT_EVERY
    ):
        raise _bad(f"snapshot_every: целое {MIN_SNAPSHOT_EVERY}…{MAX_SNAPSHOT_EVERY}")
    return RemoteMeta(
        epoch=_revision(body.get("epoch"), "epoch"),
        schema_min=_revision(body.get("schema_min"), "schema_min"),
        etag=etag,
        body=body,
    )


def read_meta(store: RemoteStore, project_uid: str) -> RemoteMeta | None:
    """``meta.json`` remote, проверенный; ``None`` — remote ещё не заведён."""
    raw = store.get_json(project_uid, META_KEY)
    if raw is None:
        return None
    return parse_meta(raw[0], raw[1], project_uid)


def _newer(candidate: str, than: str) -> bool:
    try:
        return sync_epochs.epoch_is_newer(candidate, than)
    except sync_epochs.UnknownRevisionError as exc:
        raise SchemaMismatchError(str(exc)) from exc


def opens_epoch(meta: RemoteMeta | None, local_epoch: str) -> bool:
    """Push реплики эпохи ``local_epoch`` открывает эпоху на remote с ``meta``.

    Без ``meta.json`` эпоху не открывают, а заводят (первый снапшот, CLO-029).
    """
    return meta is not None and _newer(local_epoch, meta.epoch)


def pending_phrase(count: int) -> str:
    """«3 незапушенные операции» — с согласованием по числу."""
    tail = count % _HUNDRED
    last = count % _DECIMAL
    if _TEEN_MIN <= tail <= _TEEN_MAX:
        words = "незапушенных операций"
    elif last == 1:
        words = "незапушенная операция"
    elif _FEW_MIN <= last <= _FEW_MAX:
        words = "незапушенные операции"
    else:
        words = "незапушенных операций"
    return f"{count} {words}"


def check_push(
    meta: RemoteMeta | None, *, project: str, epoch: str, schema_head: str, pending: int
) -> None:
    """Push принимается, пока ``meta.epoch`` не новее эпохи реплики (§3.6 п. 1)."""
    if meta is None:
        return
    if _newer(meta.epoch, epoch):
        msg = (
            f"remote перешёл в эпоху {meta.epoch}, реплика в эпохе {epoch}: "
            f"{pending_phrase(pending)} на новую схему не лягут — "
            f"cod-doc sync export-pending -p {project}, затем cod-doc update"
        )
        raise EpochPassedError(msg)
    if _local.schema_is_newer(meta.schema_min, schema_head):
        msg = f"remote принимает схему не старше {meta.schema_min}, у вас {schema_head} — cod-doc update"
        raise SchemaMismatchError(msg)


def check_pull(meta: RemoteMeta | None, *, project: str, epoch: str, pending: int) -> None:
    """Pull реплики эпохи старше ``meta.epoch`` — ``SYNC-105`` до первой записи."""
    if meta is None or not _newer(meta.epoch, epoch):
        return
    first = (
        f"{pending_phrase(pending)} старой эпохи уже не уедут — "
        f"сначала cod-doc sync export-pending -p {project}, затем "
        if pending
        else ""
    )
    msg = f"remote перешёл в эпоху {meta.epoch}, реплика в эпохе {epoch} — {first}cod-doc update"
    raise EpochPassedError(msg)


# --- export-pending -----------------------------------------------------------------


def _project_row(session: Session, slug: str) -> tuple[int, str]:
    """``row_id`` и ``uid`` колонками: экспорт не читает строку модели целиком."""
    row = session.execute(
        select(ProjectModel.row_id, ProjectModel.uid).where(ProjectModel.slug == slug)
    ).one_or_none()
    if row is None:
        msg = f"проекта {slug!r} нет в БД"
        raise RemoteConfigError(msg)
    row_id, uid = row
    if not uid:
        msg = f"у проекта {slug!r} нет uid — миграция 0049 не накатана"
        raise RemoteConfigError(msg)
    return int(row_id), str(uid)


def _self_replica(session: Session, project_id: int) -> str | None:
    """Своя реплика без заведения новой: экспорт ничего не пишет."""
    uid = session.scalar(
        select(ReplicaModel.uid).where(
            ReplicaModel.project_id == project_id, ReplicaModel.is_self.is_(True)
        )
    )
    return None if uid is None else str(uid)


def render_pending(sf: sessionmaker[Session], project: str) -> tuple[bytes, int]:
    """Неупакованные операции проекта в каноническом JSON и их число.

    Журнал меняется ровно как при упаковке push'а: ``gate.shippable_tail``
    убирает операции документов, ставших restricted (CLO-033).
    """
    with transactional(sf) as session:
        row_id, uid = _project_row(session, project)
        # Тот же хвост и та же растяжка, что у push._pack (CLO-033): restricted
        # не уезжает и через файл. shippable_tail пишет — убирает из журнала
        # операции ставшего restricted документа, как сделал бы push.
        ops = gate.shippable_tail(session, row_id)
        gate.check_ops(session, row_id, ops)
        head = _local.local_head(session, row_id)
        schema = _local.schema_head(session)
        epoch = _local.epoch(session)
        replica = _self_replica(session, row_id)
    changeset: JsonValue = None
    if ops:
        changeset = to_dict(
            Changeset(
                project=uid,
                parents=[head] if head else [],
                replica=replica or "-",
                actor=_ACTOR,
                epoch=epoch,
                schema_head=schema,
                hlc_max=max(op.hlc for op in ops),
                # Дайджест состояния не считается: экспорт — не changeset для
                # remote, а перенос операций руками.
                digest=_local.DIGEST_PENDING,
                ops=ops,
            )
        )
    doc: dict[str, JsonValue] = {
        "v": EXPORT_VERSION,
        "kind": EXPORT_KIND,
        "slug": project,
        "exported_at": format_time(datetime.now(UTC)),
        "ops": len(ops),
        "changeset": changeset,
    }
    return canonical_json(doc) + b"\n", len(ops)


def export_pending(sf: sessionmaker[Session], project: str, *, path: Path) -> int:
    """Записать незапушенные операции в новый файл ``path`` (0600); число операций (§10.6).

    Существующий файл не перезаписывается (``FileExistsError``): экспорт
    делают перед необратимым шагом, и затереть прошлую выгрузку нельзя.
    """
    data, count = render_pending(sf, project)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _EXPORT_MODE)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return count
