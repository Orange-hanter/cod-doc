"""ADO-230: ``adr_service.backlinks`` — кто ссылается на ADR."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import DocumentStatus, DocumentType, Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.services import adr_service
from cod_doc.services import doc_service as docs
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="bl", title="P", root_path="/tmp/bl", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    for n in (11, 12, 120):
        adr_service.create(session, project_id=proj.row_id, title=f"A{n}", adr_id=f"ADR-{n:03d}")
    return proj.row_id


def _task(session: Session, pid: int, task_id: str, description: str) -> None:
    now = datetime.now(UTC)
    plan = PlanModel(project_id=pid, scope=f"{task_id}-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    ps = PlanSectionModel(plan_id=plan.row_id, letter="A", title="X", slug=task_id, position=0)
    session.add(ps)
    session.flush()
    tasks.create(
        session,
        project_id=pid,
        plan_id=plan.row_id,
        section_id=ps.row_id,
        title=f"task {task_id}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
        task_id=task_id,
        description=description,
    )


def test_backlinks_collects_docs_tasks_and_adrs(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        doc = docs.create(
            session,
            project_id=pid,
            doc_key="arch",
            type=DocumentType.GUIDE,
            status=DocumentStatus.ACTIVE,
            title="Architecture",
            author="human:test",
            owner="human:test",
        )
        assert doc.row_id is not None
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="provenance",
            heading="Провенанс",
            level=2,
            position=0,
            body="Решено в ADR-012.",
            author="human:test",
        )
        _task(session, pid, "TST-001", "реализует ADR-012")
        _task(session, pid, "TST-002", "про ADR-0120, не про тот")
        adr_service.update(session, project_id=pid, adr_id="ADR-011", context="см. ADR-012")
        adr_service.update(
            session, project_id=pid, adr_id="ADR-120", context="ADR-0120 сам по себе"
        )

        refs = adr_service.backlinks(session, pid, "ADR-012")

    assert [(d["doc_key"], d["anchor"]) for d in refs["docs"]] == [("arch", "provenance")]
    assert [t["task_id"] for t in refs["tasks"]] == ["TST-001"], "ADR-0120 — не ADR-012"
    assert [a["adr_id"] for a in refs["adrs"]] == ["ADR-011"]


def test_backlinks_excludes_self_and_is_opt_in(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.update(session, project_id=pid, adr_id="ADR-012", context="этот ADR-012")
        row = adr_service.get(session, pid, "ADR-012")
        assert row is not None
        plain = adr_service.adr_to_dict(session, row)
        full = adr_service.adr_to_dict(session, row, include_backlinks=True)

    assert "referenced_by" not in plain
    assert full["referenced_by"] == {"docs": [], "tasks": [], "adrs": []}
