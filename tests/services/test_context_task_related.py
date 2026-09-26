"""AFT-010 (RFC 27 F11): context_get(task) — связанные документы и порядок соседей."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cod_doc.domain.entities import DocumentStatus, DocumentType, Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    AffectedFileModel,
    ModuleCodeModel,
    ModuleModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskDocumentModel,
    TaskModel,
)
from cod_doc.services import adr_service, context_service, doc_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/repo/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _seed_plan(session: Session, project_id: int) -> tuple[int, int]:
    now = datetime.now(UTC)
    plan = PlanModel(project_id=project_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return plan.row_id, sec.row_id


def _seed_doc(session: Session, project_id: int, doc_key: str, path: str) -> int:
    doc = doc_service.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        title=f"Doc {doc_key}",
        author="human:test",
        owner="human:test",
        path=path,
        reindex=False,
    )
    return doc.row_id  # type: ignore[return-value]


def _create_task(
    session: Session,
    project_id: int,
    plan_id: int,
    sec_id: int,
    task_id: str,
    priority: Priority = Priority.MEDIUM,
    affected_files: list[str] | None = None,
) -> int:
    """Задача напрямую моделью: ID вида ``T-001`` не проходит валидатор task_service."""
    task = TaskModel(
        project_id=project_id,
        task_id=task_id,
        plan_id=plan_id,
        section_id=sec_id,
        title=f"Implement {task_id}",
        status="todo",
        type=TaskType.FEATURE.value,
        priority=priority.value,
    )
    session.add(task)
    session.flush()
    for path in affected_files or []:
        session.add(AffectedFileModel(task_id=task.row_id, path=path))
    session.flush()
    return task.row_id


def _seed_corpus(session: Session) -> int:
    """Проект с документами, модулем M1-auth, задачей T-001, task_doc и ADR-007."""
    proj_id = _seed_project(session)
    plan_id, sec_id = _seed_plan(session, proj_id)
    _seed_doc(session, proj_id, "modules/auth/overview", "docs/modules/auth/overview.md")
    _seed_doc(session, proj_id, "api/a", "docs/api/a.md")
    _seed_doc(session, proj_id, "api/b", "docs/api/b.md")
    spec_id = _seed_doc(session, proj_id, "modules/auth/spec", "docs/modules/auth/spec.md")
    _seed_doc(session, proj_id, "unrelated", "docs/other/x.md")
    _seed_doc(session, proj_id, "apix", "docs/apix/y.md")

    module = ModuleModel(
        project_id=proj_id, module_id="M1-auth", name="Auth", status="active", spec_doc_id=spec_id
    )
    session.add(module)
    session.flush()
    session.add(ModuleCodeModel(module_id=module.row_id, kind="source", path="cod_doc/auth"))

    task_row_id = _create_task(
        session,
        proj_id,
        plan_id,
        sec_id,
        "T-001",
        affected_files=[
            "cod_doc/auth/login.py",
            "docs/api/",
            "docs/modules/auth/overview.md",
        ],
    )
    session.add(TaskDocumentModel(task_id=task_row_id, key="design", title="Design", body=""))

    adr_service.create(session, project_id=proj_id, title="Auth decision", adr_id="ADR-007")
    adr_service.link_task(
        session, project_id=proj_id, adr_id="ADR-007", task_id="T-001", relation="implements"
    )
    session.flush()
    return proj_id


def _documents(result: dict[str, Any]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = result["related"]["documents"]
    return docs


def test_affects_file_module_and_dir_documents(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_corpus(session)
        result = context_service.context_get(session, proj_id, "task", "T-001", depth="L1")

        docs = _documents(result)
        pairs = {(d["doc_key"], d["why"]) for d in docs if d["kind"] == "document"}
        assert pairs == {
            ("modules/auth/overview", "affects_file:docs/modules/auth/overview.md"),
            ("api/a", "affects_dir:docs/api"),
            ("api/b", "affects_dir:docs/api"),
            ("modules/auth/spec", "module_spec:M1-auth"),
        }
        keys = {d.get("doc_key") for d in docs}
        assert "unrelated" not in keys
        assert "apix" not in keys
        for d in docs:
            assert d["why"]


def test_task_doc_and_adr_both_visible(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_corpus(session)
        docs = _documents(context_service.context_get(session, proj_id, "task", "T-001"))

        assert {"kind": "task_doc", "key": "design", "title": "Design", "why": "task_doc"} in docs
        adrs = [d for d in docs if d["kind"] == "adr"]
        assert [(a["adr_id"], a["why"]) for a in adrs] == [("ADR-007", "adr:implements")]


def test_documents_deduplicated_first_why_wins(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_corpus(session)
        plan_id, sec_id = (
            session.query(PlanModel.row_id, PlanSectionModel.row_id)
            .join(PlanSectionModel, PlanSectionModel.plan_id == PlanModel.row_id)
            .one()
        )
        _create_task(
            session,
            proj_id,
            plan_id,
            sec_id,
            "T-002",
            affected_files=["docs/modules/auth/overview.md", "docs/modules/auth"],
        )
        docs = _documents(context_service.context_get(session, proj_id, "task", "T-002"))

        overview = [d for d in docs if d.get("doc_key") == "modules/auth/overview"]
        assert overview == [
            {
                "kind": "document",
                "doc_key": "modules/auth/overview",
                "path": "docs/modules/auth/overview.md",
                "title": "Doc modules/auth/overview",
                "why": "affects_file:docs/modules/auth/overview.md",
            }
        ]


def test_siblings_ordered_by_priority_then_task_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        plan_id, sec_id = _seed_plan(session, proj_id)
        _create_task(session, proj_id, plan_id, sec_id, "S-000")
        for tid, prio in (
            ("S-004", Priority.LOW),
            ("S-002", Priority.CRITICAL),
            ("S-003", Priority.MEDIUM),
            ("S-001", Priority.CRITICAL),
        ):
            _create_task(session, proj_id, plan_id, sec_id, tid, priority=prio)

        first = context_service.context_get(session, proj_id, "task", "S-000")
        second = context_service.context_get(session, proj_id, "task", "S-000")

        assert [t["task_id"] for t in first["related"]["tasks"]] == [
            "S-001",
            "S-002",
            "S-003",
            "S-004",
        ]
        assert [t["task_id"] for t in second["related"]["tasks"]] == [
            "S-001",
            "S-002",
            "S-003",
            "S-004",
        ]


def test_l0_task_has_no_related_documents(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_corpus(session)
        result = context_service.context_get(session, proj_id, "task", "T-001", depth="L0")

        assert "documents" not in result["related"]


def test_related_documents_respect_budget(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_corpus(session)
        full = context_service.context_get(session, proj_id, "task", "T-001")
        small = context_service.context_get(session, proj_id, "task", "T-001", token_budget=60)

        assert len(full["related"]["documents"]) == 6
        assert small["meta"]["tokens_used"] <= 60
        assert len(small["related"]["documents"]) == 2
        assert small["meta"]["truncated"] is True
