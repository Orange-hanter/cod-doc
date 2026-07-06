"""PCA-101: TaskDocumentService — task-bound docs with revisions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
    TaskDocumentModel,
)
from cod_doc.services import task_doc_service as tdocs
from cod_doc.services import task_service as tasks
from cod_doc.services.run_context import set_current_run_id
from cod_doc.services.task_doc_service import TaskDocConflictError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_task(session: Session, task_id: str = "TD-001") -> tuple[int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="td", title="TD", root_path="/tmp/td", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="td-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Sec", slug="A-Sec", position=0)
    session.add(sec)
    session.flush()
    task = tasks.create(
        session,
        project_id=proj.row_id,
        plan_id=plan.row_id,
        section_id=sec.row_id,
        task_id=task_id,
        title="t",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human",
    )
    return proj.row_id, task.row_id


def test_get_returns_none_when_doc_missing(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _, task_row_id = _seed_task(session)
        assert tdocs.get(session, task_row_id, "plan") is None


def test_put_creates_new_doc_with_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, task_row_id = _seed_task(session)
        doc = tdocs.put(
            session,
            project_id=proj_id,
            task_row_id=task_row_id,
            key="plan",
            title="Plan",
            body="step 1",
        )
        assert doc.body == "step 1"
        assert doc.current_revision_id is not None

    with transactional(factory) as session:
        revs = list(
            session.execute(
                select(RevisionModel).where(
                    RevisionModel.entity_kind == EntityKind.TASK_DOC.value,
                )
            ).scalars()
        )
        assert len(revs) == 1
        assert revs[0].parent_revision_id is None
        assert revs[0].reason == "create"


def test_put_updates_existing_doc_chains_revisions(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, task_row_id = _seed_task(session)
        d1 = tdocs.put(
            session,
            project_id=proj_id,
            task_row_id=task_row_id,
            key="plan",
            title="P",
            body="v1",
        )
        d2 = tdocs.put(
            session,
            project_id=proj_id,
            task_row_id=task_row_id,
            key="plan",
            title="P",
            body="v2",
        )
        assert d2.body == "v2"
        assert d2.current_revision_id != d1.current_revision_id

    with transactional(factory) as session:
        revs = list(
            session.execute(
                select(RevisionModel)
                .where(RevisionModel.entity_kind == EntityKind.TASK_DOC.value)
                .order_by(RevisionModel.at.asc(), RevisionModel.row_id.asc())
            ).scalars()
        )
        assert len(revs) == 2
        assert revs[1].parent_revision_id == revs[0].revision_id


def test_put_with_stale_base_revision_raises_conflict(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, task_row_id = _seed_task(session)
        tdocs.put(
            session,
            project_id=proj_id,
            task_row_id=task_row_id,
            key="plan",
            title="P",
            body="v1",
        )
        with pytest.raises(TaskDocConflictError):
            tdocs.put(
                session,
                project_id=proj_id,
                task_row_id=task_row_id,
                key="plan",
                title="P",
                body="v2",
                base_revision_id="01J0NONEXISTENT",
            )


def test_put_create_with_base_revision_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Passing base_revision_id when no doc exists is a programming error."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, task_row_id = _seed_task(session)
        with pytest.raises(TaskDocConflictError):
            tdocs.put(
                session,
                project_id=proj_id,
                task_row_id=task_row_id,
                key="plan",
                title="P",
                body="v",
                base_revision_id="01J0WHATEVER",
            )


def test_list_for_task_returns_all_keys(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, task_row_id = _seed_task(session)
        for key in ("plan", "design", "verification"):
            tdocs.put(
                session,
                project_id=proj_id,
                task_row_id=task_row_id,
                key=key,
                title=key.title(),
                body=f"body-{key}",
            )
        docs = tdocs.list_for_task(session, task_row_id)
        assert {d.key for d in docs} == {"plan", "design", "verification"}


def test_revisions_returns_history_oldest_first(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, task_row_id = _seed_task(session)
        tdocs.put(
            session, project_id=proj_id, task_row_id=task_row_id, key="plan", title="P", body="v1"
        )
        tdocs.put(
            session, project_id=proj_id, task_row_id=task_row_id, key="plan", title="P", body="v2"
        )
        tdocs.put(
            session, project_id=proj_id, task_row_id=task_row_id, key="plan", title="P", body="v3"
        )
        history = tdocs.revisions(session, task_row_id, "plan")
        assert len(history) == 3
        assert history[0]["parent_revision_id"] is None
        assert history[1]["parent_revision_id"] == history[0]["revision_id"]
        assert history[2]["parent_revision_id"] == history[1]["revision_id"]


def test_revisions_returns_empty_for_missing_doc(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _, task_row_id = _seed_task(session)
        assert tdocs.revisions(session, task_row_id, "plan") == []


def test_revert_restores_body_to_target_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, task_row_id = _seed_task(session)
        d1 = tdocs.put(
            session, project_id=proj_id, task_row_id=task_row_id, key="plan", title="P", body="v1"
        )
        tdocs.put(
            session, project_id=proj_id, task_row_id=task_row_id, key="plan", title="P", body="v2"
        )
        # Revert back to v1.
        reverted = tdocs.revert(
            session,
            project_id=proj_id,
            task_row_id=task_row_id,
            key="plan",
            revision_id=d1.current_revision_id,
        )
        assert reverted.body == "v1"
        # The revert itself is a new revision.
        history = tdocs.revisions(session, task_row_id, "plan")
        assert len(history) == 3
        assert history[-1]["reason"].startswith("revert to ")


def test_revert_unknown_revision_raises_lookup_error(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, task_row_id = _seed_task(session)
        tdocs.put(
            session, project_id=proj_id, task_row_id=task_row_id, key="plan", title="P", body="v1"
        )
        with pytest.raises(LookupError):
            tdocs.revert(
                session,
                project_id=proj_id,
                task_row_id=task_row_id,
                key="plan",
                revision_id="01J0NOSUCHREV",
            )


def test_revert_missing_doc_raises_lookup_error(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, task_row_id = _seed_task(session)
        with pytest.raises(LookupError):
            tdocs.revert(
                session,
                project_id=proj_id,
                task_row_id=task_row_id,
                key="plan",
                revision_id="01J0X",
            )


def test_put_stamps_run_id_from_contextvar(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Revisions written under a run scope carry the run_id."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, task_row_id = _seed_task(session)
        try:
            set_current_run_id("01J0TASKDOC")
            tdocs.put(
                session,
                project_id=proj_id,
                task_row_id=task_row_id,
                key="plan",
                title="P",
                body="v1",
            )
        finally:
            set_current_run_id(None)

    with transactional(factory) as session:
        rev = session.execute(
            select(RevisionModel).where(RevisionModel.entity_kind == EntityKind.TASK_DOC.value)
        ).scalar_one()
        assert rev.run_id == "01J0TASKDOC"


def test_unique_constraint_per_task_key(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Same (task_id, key) → one row, not two."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, task_row_id = _seed_task(session)
        tdocs.put(
            session, project_id=proj_id, task_row_id=task_row_id, key="plan", title="P", body="v1"
        )
        tdocs.put(
            session, project_id=proj_id, task_row_id=task_row_id, key="plan", title="P", body="v2"
        )
        rows = list(
            session.execute(
                select(TaskDocumentModel).where(TaskDocumentModel.task_id == task_row_id)
            ).scalars()
        )
        assert len(rows) == 1
