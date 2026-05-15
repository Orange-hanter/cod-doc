"""COD-052: doc_service.update_status / accept + plan_service.freeze_projection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    Plan,
    PlanSection,
    Priority,
    Sensitivity,
    TaskType,
)
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import doc_service, plan_service, revision_service, task_service

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def _seed(session: Session, slug: str, root) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug=slug, title=slug, root_path=str(root), config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    plan = PlanRepository(session).add(
        Plan(project_id=proj.row_id, scope=f"{slug}-plan", principle="test-first")
    )
    plan.created = now
    plan.last_updated = now
    session.flush()
    PlanSectionRepository(session).add(
        PlanSection(
            plan_id=plan.row_id,
            letter="A",
            title="Core",
            slug="A-Core",
            position=0,
        )
    )
    return proj.row_id, plan.row_id


def _make_doc(session: Session, project_id: int, doc_key: str, status: DocumentStatus):
    return doc_service.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=status,
        title=f"Doc {doc_key}",
        author="human:test",
        owner="human:test",
        sensitivity=Sensitivity.INTERNAL,
        preamble="body",
    )


# ── update_status / accept ─────────────────────────────────────────────


def test_update_status_writes_revision(tmp_path: Path, engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, _ = _seed(session, "p", tmp_path)
        doc = _make_doc(session, project_id, "draft-doc", DocumentStatus.DRAFT)
        before = revision_service.list_for_entity(
            session, EntityKind.DOCUMENT, doc.row_id
        )
        doc_service.update_status(
            session,
            document_id=doc.row_id,
            new_status=DocumentStatus.REVIEW,
            author="human:test",
        )
        after = revision_service.list_for_entity(
            session, EntityKind.DOCUMENT, doc.row_id
        )
        assert len(after) == len(before) + 1
        assert "status" in after[-1].diff and "review" in after[-1].diff


def test_update_status_no_op_when_unchanged(tmp_path: Path, engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, _ = _seed(session, "p", tmp_path)
        doc = _make_doc(session, project_id, "active-doc", DocumentStatus.ACTIVE)
        baseline = len(
            revision_service.list_for_entity(session, EntityKind.DOCUMENT, doc.row_id)
        )
        doc_service.update_status(
            session,
            document_id=doc.row_id,
            new_status=DocumentStatus.ACTIVE,
            author="human:test",
        )
        after = len(
            revision_service.list_for_entity(session, EntityKind.DOCUMENT, doc.row_id)
        )
    assert after == baseline


def test_accept_promotes_draft_to_active(tmp_path: Path, engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, _ = _seed(session, "p", tmp_path)
        doc = _make_doc(session, project_id, "to-accept", DocumentStatus.DRAFT)
        promoted = doc_service.accept(
            session, document_id=doc.row_id, author="human:test"
        )
    assert promoted.status == DocumentStatus.ACTIVE


# ── freeze_projection ──────────────────────────────────────────────────


def test_freeze_projection_creates_execution_log_doc(
    tmp_path: Path, engine_with_schema  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, plan_id = _seed(session, "freeze-p", tmp_path)
        # Seed one task so the projection isn't entirely empty.
        sections = PlanSectionRepository(session).list_for_plan(plan_id)
        section = sections[0]
        task_service.create(
            session,
            project_id=project_id,
            plan_id=plan_id,
            section_id=section.row_id,
            title="Implement: thing",
            type=TaskType.FEATURE,
            priority=Priority.HIGH,
            author="human:test",
            id_prefix="FRZ",
        )
        frozen = plan_service.freeze_projection(
            session, plan_id, author="human:test"
        )
    assert frozen.row_id is not None
    assert frozen.type == DocumentType.EXECUTION_LOG
    assert frozen.status == DocumentStatus.ACTIVE
    assert frozen.doc_key.startswith("frozen/freeze-p-plan/")
    assert "Frozen projection" in (frozen.preamble or "")
    assert "Progress overview" in (frozen.preamble or "")
    assert "graph TD" in (frozen.preamble or "")  # mermaid block included


def test_freeze_projection_creates_distinct_snapshots_per_call(
    tmp_path: Path, engine_with_schema  # type: ignore[no-untyped-def]
) -> None:
    """Two freezes back-to-back must produce distinct doc_keys (millisecond
    resolution prevents UniqueConstraint collisions even within the same second)."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, plan_id = _seed(session, "p2", tmp_path)
        first = plan_service.freeze_projection(
            session, plan_id, author="human:test"
        )
    assert first.row_id is not None
    # NB: no sleep — relies on ms-precision in the timestamp.
    with transactional(factory) as session:
        second = plan_service.freeze_projection(
            session, plan_id, author="human:test"
        )
    assert second.row_id != first.row_id
    assert second.doc_key != first.doc_key


def test_freeze_projection_unknown_plan_raises(
    tmp_path: Path, engine_with_schema  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(plan_service.PlanNotFoundError):
        plan_service.freeze_projection(session, 9999, author="human:test")
