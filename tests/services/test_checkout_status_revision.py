"""FLW-002 (RFC 33 F2): checkout оставляет ревизию перехода todo → in_progress.

До правки ``checkout_service.checkout`` писал только событие
``task.checked_out``: в журнале ревизий момента начала работы не было ни у
одной задачи (переход ``todo → in_progress`` разрешён только через checkout,
ADO-039). Правило ADO-040 — ревизия и событие одним вызовом.

Эталоны — прямые SELECT по ``revision`` и ``activity_event``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, RevisionModel, TaskModel
from cod_doc.services import checkout_service
from cod_doc.services import task_service as tasks
from tests.services.test_checkout_service import _seed_task

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

AGENT = "agent:dakh/claude-code"


@pytest.fixture
def sf(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


@pytest.fixture
def pid(sf: sessionmaker[Session]) -> int:
    with transactional(sf) as s:
        return _seed_task(s, status="todo")


def _status_revisions(sf: sessionmaker[Session]) -> list[tuple[str, str]]:
    with transactional(sf) as s:
        row_id = s.scalar(select(TaskModel.row_id).where(TaskModel.task_id == "CO-001"))
        diffs = s.scalars(
            select(RevisionModel.diff)
            .where(RevisionModel.entity_kind == "task", RevisionModel.entity_id == row_id)
            .order_by(RevisionModel.at, RevisionModel.row_id)
        ).all()
    # Переход статуса пишут два вида ревизий: op=status (old/new) и закрывающая
    # op=complete (old_status → done).
    out = []
    for raw in diffs:
        diff = json.loads(raw)
        if diff.get("op") == "status":
            out.append((diff["old"], diff["new"]))
        elif diff.get("op") == "complete":
            out.append((diff["old_status"], "done"))
    return out


def _events(sf: sessionmaker[Session], kind: str) -> int:
    with transactional(sf) as s:
        return len(
            s.scalars(
                select(ActivityEventModel.row_id).where(
                    ActivityEventModel.kind == kind, ActivityEventModel.scope_id == "CO-001"
                )
            ).all()
        )


def test_checkout_writes_status_revision_and_event(sf: sessionmaker[Session], pid: int) -> None:
    with transactional(sf) as s:
        checkout_service.checkout(s, "CO-001", project_id=pid, agent=AGENT)
    assert _status_revisions(sf) == [("todo", "in_progress")]
    assert _events(sf, "task.checked_out") == 1


def test_idempotent_recheckout_writes_no_revision(sf: sessionmaker[Session], pid: int) -> None:
    with transactional(sf) as s:
        checkout_service.checkout(s, "CO-001", project_id=pid, agent=AGENT)
    with transactional(sf) as s:
        result = checkout_service.checkout(s, "CO-001", project_id=pid, agent=AGENT)
    assert result.idempotent
    assert _status_revisions(sf) == [("todo", "in_progress")]
    assert _events(sf, "task.checked_out") == 1


def test_checkout_then_complete_one_revision_per_transition(
    sf: sessionmaker[Session], pid: int
) -> None:
    with transactional(sf) as s:
        checkout_service.checkout(s, "CO-001", project_id=pid, agent=AGENT)
    with transactional(sf) as s:
        tasks.complete(s, project_id=pid, task_id="CO-001", author=AGENT, commit_sha="abc1234")
    assert _status_revisions(sf) == [("todo", "in_progress"), ("in_progress", "done")]


def test_complete_from_todo_one_revision_per_transition(
    sf: sessionmaker[Session], pid: int
) -> None:
    """ADO-038: checkout-нога complete() идёт через update_status — дубля нет.

    Две ревизии на два перехода; закрывающая пишет ``old_status`` статуса до
    вызова complete() (``todo``) — это её формат, не предмет FLW-002.
    """
    with transactional(sf) as s:
        tasks.complete(s, project_id=pid, task_id="CO-001", author=AGENT, commit_sha="abc1234")
    revisions = _status_revisions(sf)
    assert len(revisions) == 2
    assert revisions[0] == ("todo", "in_progress")
    assert revisions[1][1] == "done"
