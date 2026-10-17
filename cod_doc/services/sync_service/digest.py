"""Дайджест состояния реплики и ``verify`` (RFC 29 §3.7, §10.6; CLO-014).

**Дайджест** — ``sha256`` потока строк, отсортированного по ``(entity_kind,
uid)``; строка — канонический JSON (§10.4) ``[entity_kind, uid, поля]``, по
строке на каждую уезжающую строку проекта, с переводом строки после каждой.
Поля — ровно то, что уехало бы операцией: ``sync_capture.shipped_state``
(колонки ``SHIPPED_COLUMNS`` без придержанных реестром, FK — uid конца), время
— в форме §10.4 через ``_values.to_wire``, как при упаковке push'а. Поэтому в
дайджест не входят:

- ``row_id`` и FK как ``row_id`` — у реплик разные по построению;
- ``local``-колонки: ``position``, хэши рабочего дерева, ``run_id``;
- строки ``row_is_local`` — restricted-документ и всё под ним, нерешённые
  подсказки ссылок;
- ``revision.parent_revision_id`` (``EXCLUDED_COLUMNS``): чужая ревизия
  встаёт поверх локальной головы (Q-010, CLO-024), и порядок ревизий на
  репликах может различаться. Состав ревизий по ``revision_id`` входит —
  строка ``[revision, revision_id, поля]``;
- ``activity_event``: события не уезжают (решение 19 реестра), лента у
  каждой реплики своя.

Дайджест считается **упаковщиком** push'а и пишется в changeset: состояние
реплики в момент упаковки — это и есть состояние после применения changeset'а
(родитель плюс хвост). ``verify`` сравнивает дайджест реплики с дайджестом в
changeset'е головы remote; равенство значит «реплика стоит ровно на этом
changeset'е и её уезжающее состояние совпало с упаковщиком». Неравенство при
той же голове — признак записи мимо журнала или ошибки применения.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from sqlalchemy import func, select

import cod_doc.infra.models.link_suggestions  # noqa: F401 — ленивая модель: в metadata до обхода
from cod_doc.infra import sync_capture as cap
from cod_doc.infra.db import transactional
from cod_doc.infra.models import ChangesetModel, SyncOpModel
from cod_doc.services.sync_service import _local
from cod_doc.services.sync_service._values import to_wire
from cod_doc.services.sync_service.changeset import canonical_json, decode

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.infra.remote.base import RemoteStore

__all__ = ["EXCLUDED_COLUMNS", "VerifyResult", "compute", "verify"]

#: Колонки, которые уезжают, но в дайджест не входят (§3.7): порядок ревизий на
#: репликах может различаться (Q-010) — родителя переписывает применение.
EXCLUDED_COLUMNS: Final[frozenset[tuple[str, str]]] = frozenset(
    {("revision", "parent_revision_id")}
)


@dataclass(frozen=True, slots=True)
class VerifyResult:
    #: Дайджест реплики сейчас.
    local: str
    #: Дайджест в changeset'е головы remote; ``None`` — remote пуст или его
    #: голова упакована до CLO-014 (нулевой дайджест — «не посчитан»).
    remote: str | None
    equal: bool


def _line(table: str, uid: str, fields_json: str) -> bytes:
    fields = {
        column: value
        for column, value in to_wire(table, json.loads(fields_json)).items()
        if (table, column) not in EXCLUDED_COLUMNS
    }
    return canonical_json([table, uid, fields]) + b"\n"


def compute(session: Session, project_id: int) -> str:
    """Дайджест уезжающего состояния проекта (§3.7), hex ``sha256``."""
    h = hashlib.sha256()
    # shipped_state выдаёт строки уже в порядке (entity_kind, uid) и потоком:
    # память — одна строка плюс кэш концов ссылок, а не весь проект.
    for table, uid, fields in cap.shipped_state(session, project_id):
        h.update(_line(table, uid, fields))
    return h.hexdigest()


def _remote_digest(
    sf: sessionmaker[Session], store: RemoteStore, project: _local.ProjectRef, head: str
) -> str | None:
    """Дайджест changeset'а ``head``: из локального индекса, иначе с remote."""
    with transactional(sf, commit=False) as session:
        row = session.get(ChangesetModel, head)
        digest = row.digest if row is not None and row.project_id == project.row_id else None
    if digest is None:
        digest = decode(store.get_object(project.uid, head), head).digest
    return None if digest == _local.DIGEST_PENDING else digest


def pending_count(session: Session, project_id: int) -> int:
    """Число неупакованных операций проекта."""
    stmt = (
        select(func.count())
        .select_from(SyncOpModel)
        .where(SyncOpModel.project_id == project_id, SyncOpModel.changeset.is_(None))
    )
    return int(session.scalar(stmt) or 0)


def verify(
    sf: sessionmaker[Session],
    project: str,
    *,
    remote: str = "origin",
    store: RemoteStore | None = None,
) -> VerifyResult:
    """Сравнить дайджест реплики с дайджестом в changeset'е головы remote (§3.7).

    Только чтение: ``sync_ref`` не сдвигается, БД не пишется. Реплика позади
    remote или с неупакованным хвостом законно даёт ``equal=False``.
    """
    store = store if store is not None else _local.open_store(project, remote)
    with transactional(sf, commit=False) as session:
        ref = _local.project_ref(session, project)
        local = compute(session, ref.row_id)
    remote_ref = store.get_ref(ref.uid, _local.MAIN)
    digest = None if remote_ref is None else _remote_digest(sf, store, ref, remote_ref.sha)
    return VerifyResult(local=local, remote=digest, equal=digest == local)
