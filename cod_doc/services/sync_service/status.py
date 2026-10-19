"""``status`` и ``log``: где реплика относительно remote (RFC 29 §3.9, §10.6; CLO-015).

``status`` с настроенным remote делает ``fetch`` (сеть вне транзакции, как у
pull): ``behind`` — число скачанных и ещё не применённых changeset'ов, и
``sync_ref`` сдвигается на увиденную голову, как после ``cod-doc fetch``.
Без remote — только локальное состояние, ``remote=None``.

Поля, у которых в секции B нет источника, отдаются нейтральными значениями и
заполнятся своими задачами: ``epoch_remote`` — ``None`` (``meta.json``,
CLO-032). ``leases`` — остатки аренды номеров ``{префикс: (следующий,
последний)}`` (CLO-030). ``open_conflicts`` — открытые
строки ``sync_conflict`` (CLO-017).

``log`` — changeset'ы локального индекса по первому родителю от головы
реплики: автор, реплика, число операций. Сеть не нужна: применённый или
запушенный changeset лежит в ``changeset``, его операции — в ``sync_op``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from sqlalchemy import func, select

from cod_doc.infra.db import transactional
from cod_doc.infra.models import ChangesetModel, SyncOpModel, SyncRefModel
from cod_doc.services.sync_service import _local, conflicts, digest, gate, leases, remotes
from cod_doc.services.sync_service import pull as pull_mod

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.infra.remote.base import RemoteStore

__all__ = ["LogEntry", "SyncStatus", "log", "status"]

#: ``sync log -n`` по умолчанию (§10.8).
DEFAULT_LOG_LIMIT: Final = 20


@dataclass(frozen=True, slots=True)
class SyncStatus:
    #: Имя remote; ``None`` — у проекта такого remote нет.
    remote: str | None
    head_local: str | None
    head_remote: str | None
    #: Changeset'ы реплики, которых нет в истории remote (в секции B — 0:
    #: push фиксирует changeset только после CAS).
    ahead: int
    #: Changeset'ы remote, ещё не применённые здесь (по ``fetch``).
    behind: int
    pending_ops: int
    open_conflicts: int
    epoch_local: str
    epoch_remote: str | None
    #: Документы, которые не уезжают (restricted, §3.2).
    restricted_local: int
    #: Строки, которые эти документы держат на реплике: сами документы и их
    #: тело (секции, комментарии, теги; CLO-033).
    restricted_rows: int = 0
    leases: dict[str, tuple[int, int]] = field(default_factory=dict)
    drift_hint: str | None = None
    #: ``status(verify=True)`` — сверка дайджеста (§3.7, ``sync verify``).
    verify: digest.VerifyResult | None = None
    #: URL для ``cod-doc clone`` на другой машине: URL remote + ``/<project_uid>``
    #: (CLO-029); ``None`` — remote не настроен в реестре.
    clone_url: str | None = None


@dataclass(frozen=True, slots=True)
class LogEntry:
    sha: str
    parents: list[str]
    actor: str
    replica: str
    ops: int
    hlc_max: str
    applied_at: datetime | None


def _first_parent_chain(
    session: Session, head: str | None, stop: str | None, limit: int
) -> list[ChangesetModel]:
    """Changeset'ы от ``head`` по первому родителю до ``stop`` (не включая)."""
    out: list[ChangesetModel] = []
    cur = head
    while cur is not None and cur != stop and len(out) < limit:
        row = session.get(ChangesetModel, cur)
        if row is None:
            break
        out.append(row)
        parents = json.loads(row.parents)
        cur = parents[0] if parents and isinstance(parents[0], str) else None
    return out


def _ahead(session: Session, head: str | None, remote_head: str | None) -> int:
    if head is None or head == remote_head:
        return 0
    if remote_head is not None and not _local.is_ancestor(session, remote_head, head):
        # Remote не предок головы: история разошлась, «впереди» не посчитать
        # по первому родителю — считаем всю локальную цепочку.
        remote_head = None
    return len(_first_parent_chain(session, head, remote_head, _local.MAX_HISTORY))


def status(
    sf: sessionmaker[Session],
    project: str,
    *,
    remote: str = "origin",
    verify: bool = False,
    store: RemoteStore | None = None,
) -> SyncStatus:
    """Снимок синхронизации проекта (§3.9 ``sync status``)."""
    spec = remotes.get(project, remote)
    configured = store is not None or spec is not None
    behind = 0
    if configured:
        store = store if store is not None else _local.open_store(project, remote)
        behind = len(pull_mod.fetch(sf, project, remote=remote, store=store))
    with transactional(sf, commit=False) as session:
        ref = _local.project_ref(session, project)
        head = _local.local_head(session, ref.row_id)
        seen = session.get(SyncRefModel, (ref.row_id, remote, _local.MAIN))
        head_remote = seen.sha if configured and seen is not None else None
        pending = digest.pending_count(session, ref.row_id)
        ahead = _ahead(session, head, head_remote) if configured else 0
        held = gate.restricted(session, ref.row_id)
        open_conflicts = conflicts.count_open(session, ref.row_id)
        lease_left = leases.lease_status(session, ref.row_id)
    checked = (
        digest.verify(sf, project, remote=remote, store=store)
        if verify and configured and store is not None
        else None
    )
    hint = (
        f"после pull проекции markdown отстанут от БД — cod-doc doc drift -p {project}"
        if behind
        else None
    )
    return SyncStatus(
        remote=remote if configured else None,
        head_local=head,
        head_remote=head_remote,
        ahead=ahead,
        behind=behind,
        pending_ops=pending,
        open_conflicts=open_conflicts,
        epoch_local=_local.EPOCH,
        epoch_remote=None,
        restricted_local=held.documents,
        restricted_rows=held.rows,
        leases=lease_left,
        drift_hint=hint,
        verify=checked,
        clone_url=None if spec is None else f"{spec.url.rstrip('/')}/{ref.uid}",
    )


def log(
    sf: sessionmaker[Session], project: str, *, limit: int = DEFAULT_LOG_LIMIT
) -> list[LogEntry]:
    """Последние ``limit`` changeset'ов истории реплики, от головы (§10.8 ``sync log``)."""
    with transactional(sf, commit=False) as session:
        ref = _local.project_ref(session, project)
        head = _local.local_head(session, ref.row_id)
        chain = _first_parent_chain(session, head, None, max(limit, 0))
        rows = session.execute(
            select(SyncOpModel.changeset, func.count())
            .where(SyncOpModel.project_id == ref.row_id, SyncOpModel.changeset.is_not(None))
            .group_by(SyncOpModel.changeset)
        )
        counts: dict[str | None, int] = {sha: n for sha, n in rows}
        return [
            LogEntry(
                sha=row.sha,
                parents=[p for p in json.loads(row.parents) if isinstance(p, str)],
                actor=row.actor,
                replica=row.replica_uid,
                ops=int(counts.get(row.sha, 0)),
                hlc_max=row.hlc_max,
                applied_at=row.applied_at,
            )
            for row in chain
        ]
