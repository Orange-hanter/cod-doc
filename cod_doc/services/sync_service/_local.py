"""Состояние реплики относительно remote: голова, ref, схема, транспорт (RFC 29 §10.1).

Общее для ``push`` и ``pull`` (CLO-012/013). Здесь только чтение и запись
local-таблиц синхронизации (``sync_ref``, ``changeset``) и резолв транспорта;
сеть сюда не ходит.

**Две головы.** ``sync_ref(remote, "main")`` — что реплика последний раз видела
на remote (как ``origin/main`` у git, с etag для CAS). Локальная голова —
changeset, на котором стоит сама реплика, — хранится там же под псевдо-remote
``@local``: имя remote проходит ``[A-Za-z0-9_][A-Za-z0-9._-]*`` (``infra/remote/
base.py``), поэтому с настоящим не совпадёт. Вычислять голову из таблицы
``changeset`` (применённый, не родитель другого применённого) можно, но это
обход всей истории на каждый push; голова — одна строка.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from cod_doc.infra.db import transactional
from cod_doc.infra.models import ChangesetModel, ProjectModel, SyncOpModel, SyncRefModel
from cod_doc.infra.sync_capture import SyncCaptureError
from cod_doc.services.sync_service._values import to_wire
from cod_doc.services.sync_service.changeset import Changeset, Op
from cod_doc.services.sync_service.errors import RemoteConfigError, SchemaMismatchError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.infra.remote.base import RefValue, RemoteStore

#: Ref, который двигает push (§3.5: ``refs/main``).
MAIN: Final = "main"
#: Псевдо-remote локальной головы (см. докстринг модуля).
LOCAL_REMOTE: Final = "@local"
#: Эпоха — голова миграций, последней менявшая authored-колонки (§3.6). Эпохами
#: и ``meta.json`` управляет CLO-032; до него эпоха одна.
EPOCH: Final = "0050_sync_rank"
#: ``digest`` обязателен в формате §10.4, а считает его CLO-014. До него
#: changeset несёт нулевой дайджест — «не посчитан»; pull его не сверяет.
DIGEST_PENDING: Final = "0" * 64
#: Порог ``IN (...)``: SQLite старше 3.32 держит 999 параметров.
_CHUNK: Final = 500
#: Потолок обхода локальной истории (предок ли голова remote).
MAX_HISTORY: Final = 100_000


@dataclass(frozen=True, slots=True)
class ProjectRef:
    row_id: int
    uid: str
    slug: str


def project_ref(session: Session, slug: str) -> ProjectRef:
    """Проект по слагу; без uid (строка мимо хука) синхронизировать нечего."""
    row = session.scalars(select(ProjectModel).where(ProjectModel.slug == slug)).one_or_none()
    if row is None:
        msg = f"проекта {slug!r} нет в БД"
        raise RemoteConfigError(msg)
    if not row.uid:
        msg = f"у проекта {slug!r} нет uid — миграция 0049 не накатана"
        raise RemoteConfigError(msg)
    return ProjectRef(row_id=row.row_id, uid=row.uid, slug=slug)


def _ref_row(session: Session, project_id: int, remote: str, name: str) -> SyncRefModel | None:
    return session.get(SyncRefModel, (project_id, remote, name))


def local_head(session: Session, project_id: int) -> str | None:
    row = _ref_row(session, project_id, LOCAL_REMOTE, MAIN)
    return row.sha if row is not None else None


def set_local_head(session: Session, project_id: int, sha: str) -> None:
    _upsert_ref(session, project_id, LOCAL_REMOTE, sha, None)


def set_remote_ref(session: Session, project_id: int, remote: str, ref: RefValue | None) -> None:
    """Запомнить увиденную голову remote вместе с etag для следующего CAS."""
    _upsert_ref(
        session,
        project_id,
        remote,
        None if ref is None else ref.sha,
        None if ref is None else ref.etag,
    )


def _upsert_ref(
    session: Session, project_id: int, remote: str, sha: str | None, etag: str | None
) -> None:
    row = _ref_row(session, project_id, remote, MAIN)
    now = datetime.now(UTC)
    if row is None:
        session.add(
            SyncRefModel(
                project_id=project_id, remote=remote, name=MAIN, sha=sha, etag=etag, fetched_at=now
            )
        )
    else:
        row.sha, row.etag, row.fetched_at = sha, etag, now
    session.flush()


def schema_head(session: Session) -> str:
    """Голова миграций этой БД (не кода): её changeset и пишет, и сверяет."""
    head = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if not head:
        msg = "в БД нет alembic_version — схема не накатана"
        raise RemoteConfigError(msg)
    return str(head)


@functools.cache
def _revision_order() -> dict[str, int]:
    """Ревизии поставляемых миграций → порядковый номер от корня.

    Каталог миграций берётся через ``as_file``: у ``files()`` из wheel или zip
    ``str()`` — не путь на ФС, и ``ScriptDirectory`` молча увидел бы пустую
    цепочку. Пустая цепочка — отказ, а не «всё новее».
    """
    from importlib.resources import as_file, files

    from alembic.script import ScriptDirectory

    with as_file(files("cod_doc.infra.migrations")) as path:
        chain = [rev.revision for rev in ScriptDirectory(str(path)).walk_revisions()]
    if not chain:
        msg = "цепочка миграций не прочитана — порядок схем неизвестен"
        raise SchemaMismatchError(msg)
    return {rev: i for i, rev in enumerate(reversed(chain))}


def schema_is_newer(remote_head: str, local: str) -> bool:
    """``remote_head`` новее ``local``: незнакомая коду ревизия — заведомо новее.

    Голова самой БД, которой код не знает, — отказ (fail closed): сравнить не
    с чем, а «всё новее» молча отвергло бы и законные changeset'ы.
    """
    order = _revision_order()
    if local not in order:
        msg = f"схема БД {local} незнакома коду — порядок с remote не определить"
        raise SchemaMismatchError(msg)
    if remote_head not in order:
        return True
    return order[remote_head] > order[local]


def open_store(project: str, remote: str) -> RemoteStore:
    """Транспорт remote проекта из ``~/.cod-doc/config.yaml`` (§10.12).

    ``ProjectEntry.remotes`` — ``{name: {url, kind}}``; запись заводит
    ``cod-doc remote add`` (CLO-015). Нет записи — ``SYNC-110``.
    """
    from cod_doc.config import Config
    from cod_doc.infra.remote.base import open_remote

    entry = Config.load().get_project(project)
    remotes = (entry.model_extra or {}).get("remotes") if entry is not None else None
    spec = remotes.get(remote) if isinstance(remotes, dict) else None
    url = spec.get("url") if isinstance(spec, dict) else None
    if not isinstance(url, str) or not url:
        msg = f"remote {remote!r} у проекта {project!r} не настроен"
        raise RemoteConfigError(msg)
    return open_remote(url)


def _payload(row: SyncOpModel, column: str) -> dict[str, object]:
    raw = json.loads(getattr(row, column))
    if not isinstance(raw, dict):
        msg = f"sync_op {row.op_id}.{column}: не JSON-объект ({type(raw).__name__})"
        raise SyncCaptureError(msg)
    return raw


def op_of(row: SyncOpModel) -> Op:
    """Операция журнала в форме changeset'а: время по §10.4."""
    return Op(
        op_id=row.op_id,
        hlc=row.hlc,
        entity_kind=row.entity_kind,
        entity_uid=row.entity_uid,
        kind=row.kind,
        fields=to_wire(row.entity_kind, _payload(row, "fields")),
        base=to_wire(row.entity_kind, _payload(row, "base")),
        author=row.author,
    )


def pending_ops(session: Session, project_id: int) -> list[Op]:
    """Неупакованные операции проекта по ``(hlc, op_id)``, время — в форме §10.4."""
    rows = session.scalars(
        select(SyncOpModel)
        .where(SyncOpModel.project_id == project_id, SyncOpModel.changeset.is_(None))
        .order_by(SyncOpModel.hlc, SyncOpModel.op_id)
    )
    return [op_of(row) for row in rows]


def journal_ops(session: Session, op_ids: list[str]) -> dict[str, tuple[Op, str | None]]:
    """Операции журнала по ``op_id`` (любого проекта и статуса) → (операция, changeset)."""
    out: dict[str, tuple[Op, str | None]] = {}
    for i in range(0, len(op_ids), _CHUNK):
        rows = session.scalars(
            select(SyncOpModel).where(SyncOpModel.op_id.in_(op_ids[i : i + _CHUNK]))
        )
        out.update({row.op_id: (op_of(row), row.changeset) for row in rows})
    return out


def ensure_local_head(session_factory: sessionmaker[Session], project_id: int) -> None:
    """Строка головы (``sha`` пуст) — чтобы её сдвигал CAS; гонку вставки гасит PK."""
    try:
        with transactional(session_factory) as session:
            if _ref_row(session, project_id, LOCAL_REMOTE, MAIN) is None:
                _upsert_ref(session, project_id, LOCAL_REMOTE, None, None)
    except IntegrityError:
        pass  # вставил параллельный pull — строка есть


def cas_local_head(session: Session, project_id: int, expected: str | None, new: str) -> bool:
    """Сдвинуть голову с ``expected`` на ``new`` одним UPDATE; ``False`` — голова ушла.

    UPDATE — первая запись транзакции: на SQLite он берёт замок записи, и всё
    дальнейшее в транзакции видит состояние после параллельного pull.
    """
    cur = SyncRefModel.sha
    match = cur.is_(None) if expected is None else cur == expected
    result = session.execute(
        update(SyncRefModel)
        .where(
            SyncRefModel.project_id == project_id,
            SyncRefModel.remote == LOCAL_REMOTE,
            SyncRefModel.name == MAIN,
            match,
        )
        .values(sha=new, fetched_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    return bool(getattr(result, "rowcount", 0))


def is_applied(session: Session, sha: str) -> bool:
    row = session.get(ChangesetModel, sha)
    return row is not None and row.applied_at is not None


def is_ancestor(session: Session, sha: str, head: str | None) -> bool:
    """``sha`` — ``head`` или его предок по локальному индексу ``changeset``."""
    seen: set[str] = set()
    queue = [head] if head else []
    while queue and len(seen) <= MAX_HISTORY:
        cur = queue.pop()
        if cur == sha:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        row = session.get(ChangesetModel, cur)
        if row is not None:
            parents = json.loads(row.parents)
            queue.extend(p for p in parents if isinstance(p, str))
    return False


def record_changeset(session: Session, project: ProjectRef, sha: str, cs: Changeset) -> None:
    """Changeset известен и применён (``changeset.applied_at``)."""
    now = datetime.now(UTC)
    row = session.get(ChangesetModel, sha)
    if row is None:
        session.add(
            ChangesetModel(
                sha=sha,
                project_id=project.row_id,
                parents=json.dumps(list(cs.parents), ensure_ascii=False),
                replica_uid=cs.replica,
                actor=cs.actor,
                epoch=cs.epoch,
                hlc_max=cs.hlc_max,
                digest=cs.digest,
                applied_at=now,
            )
        )
    else:
        row.applied_at = now
    session.flush()
