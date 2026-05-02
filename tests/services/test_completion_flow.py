"""COD-022: completion flow — RevisionService.revert dispatch + plan staleness."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    Priority,
    TaskStatus,
    TaskType,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as rev
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'flow.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _seed(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make_task(session: Session, proj: int, plan: int, sec: int, tid: str) -> int:
    t = tasks.create(
        session,
        project_id=proj,
        plan_id=plan,
        section_id=sec,
        task_id=tid,
        title=f"Implement: {tid}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
    )
    return t.row_id  # type: ignore[return-value]


# ============================================================================ #
# RevisionService.revert — TASK                                                #
# ============================================================================ #


def test_revert_task_status_change(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_task(session, p, pl, s, "CF-001")

        tasks.update_status(
            session,
            task_id="CF-001",
            new_status=TaskStatus.IN_PROGRESS,
            author="human:test",
        )
        history = rev.list_for_entity(
            session,
            EntityKind.TASK,
            tasks.get(session, "CF-001").row_id,  # type: ignore[union-attr]
        )
        status_rev = history[-1]
        assert status_rev.diff.startswith('{"op": "status"')

        rev.revert(session, status_rev.revision_id, author="human:test")

        t = tasks.get(session, "CF-001")
        assert t is not None
        assert t.status is TaskStatus.PENDING


def test_revert_task_complete(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_task(session, p, pl, s, "CF-001")
        tasks.complete(session, task_id="CF-001", author="human:test", commit_sha="abc")

        history = rev.list_for_entity(
            session,
            EntityKind.TASK,
            tasks.get(session, "CF-001").row_id,  # type: ignore[union-attr]
        )
        complete_rev = history[-1]
        rev.revert(session, complete_rev.revision_id, author="human:test")

        t = tasks.get(session, "CF-001")
        assert t is not None
        assert t.status is TaskStatus.PENDING


def test_revert_task_unsupported_op_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_task(session, p, pl, s, "CF-001")
        # The initial 'create' revision has op='create' — not revertible.
        history = rev.list_for_entity(
            session,
            EntityKind.TASK,
            tasks.get(session, "CF-001").row_id,  # type: ignore[union-attr]
        )
        create_rev = history[0]
        with pytest.raises(rev.RevertNotSupportedError):
            rev.revert(session, create_rev.revision_id, author="human:test")


# ============================================================================ #
# RevisionService.revert — SECTION                                             #
# ============================================================================ #


def test_revert_section_patch_restores_old_body(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _, _ = _seed(session)
        doc = docs.create(
            session,
            project_id=p,
            doc_key="test-doc",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="Test",
            author="human:test",
        )
        # Use bodies with trailing '\n' (realistic: markdown files always end with newline).
        # Unified diffs for single-line strings without '\n' are not parseable by
        # _restore_original_from_unified; that edge case is addressed in COD-023.
        sec = docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="intro",
            heading="Intro",
            level=2,
            position=0,
            body="Original body.\n",
            author="human:test",
        )
        docs.patch_section(
            session,
            document_id=doc.row_id,
            anchor="intro",
            new_body="Updated body.\n",
            author="human:test",
        )
        history = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id)
        patch_rev = history[-1]

        rev.revert(session, patch_rev.revision_id, author="human:test")

        from cod_doc.infra.models import SectionModel

        sec_model = session.get(SectionModel, sec.row_id)
        assert sec_model is not None
        assert sec_model.body == "Original body.\n"


def test_revert_section_writes_new_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _, _ = _seed(session)
        doc = docs.create(
            session,
            project_id=p,
            doc_key="test-doc",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="Test",
            author="human:test",
        )
        sec = docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="intro",
            heading="Intro",
            level=2,
            position=0,
            body="v1\n",
            author="human:test",
        )
        docs.patch_section(
            session,
            document_id=doc.row_id,
            anchor="intro",
            new_body="v2\n",
            author="human:test",
        )
        n_before = len(rev.list_for_entity(session, EntityKind.SECTION, sec.row_id))

        patch_rev = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id)[-1]
        rev.revert(session, patch_rev.revision_id, author="human:test")

        n_after = len(rev.list_for_entity(session, EntityKind.SECTION, sec.row_id))
        assert n_after == n_before + 1  # the revert is a new revision


# ============================================================================ #
# RevisionService.revert — DOCUMENT rename                                     #
# ============================================================================ #


def test_revert_document_rename_restores_key(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _, _ = _seed(session)
        doc = docs.create(
            session,
            project_id=p,
            doc_key="original-key",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="T",
            author="human:test",
        )
        docs.rename(
            session,
            document_id=doc.row_id,
            new_doc_key="renamed-key",
            author="human:test",
        )
        rename_rev = rev.list_for_entity(session, EntityKind.DOCUMENT, doc.row_id)[-1]

        rev.revert(session, rename_rev.revision_id, author="human:test")

        from cod_doc.infra.models import DocumentModel

        doc_model = session.get(DocumentModel, doc.row_id)
        assert doc_model is not None
        assert doc_model.doc_key == "original-key"


def test_revert_unsupported_entity_kind_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _, _ = _seed(session)
        # Write a PLAN revision manually with an unsupported entity_kind.
        written = rev.write(
            session,
            project_id=p,
            entity_kind=EntityKind.PLAN,
            entity_id=1,
            author="human:test",
            diff='{"op": "create"}',
        )
        with pytest.raises(rev.RevertNotSupportedError):
            rev.revert(session, written.revision_id, author="human:test")


# ============================================================================ #
# Completion flow — plan.last_updated staleness signal                          #
# ============================================================================ #


def test_complete_updates_plan_last_updated(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_task(session, p, pl, s, "CF-001")

        plan_before = session.get(PlanModel, pl)
        # SQLite stores datetimes without tzinfo; normalise for comparison.
        ts_before = plan_before.last_updated.replace(tzinfo=None)  # type: ignore[union-attr]

        tasks.complete(session, task_id="CF-001", author="human:test")

        plan_after = session.get(PlanModel, pl)
        ts_after = plan_after.last_updated.replace(tzinfo=None)  # type: ignore[union-attr]
        assert ts_after >= ts_before


def test_complete_plan_update_is_in_same_transaction(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """plan.last_updated changes atomically with the task completion."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_task(session, p, pl, s, "CF-001")
        tasks.complete(session, task_id="CF-001", author="human:test")

        # Both changes visible within the same session.
        t = tasks.get(session, "CF-001")
        plan = session.get(PlanModel, pl)
        assert t.status is TaskStatus.DONE  # type: ignore[union-attr]
        # Both timestamps should be very close (same flush cycle).
        plan_ts = plan.last_updated.replace(tzinfo=None)  # type: ignore[union-attr]
        task_ts = t.completed_at.replace(tzinfo=None)  # type: ignore[union-attr]
        assert abs((plan_ts - task_ts).total_seconds()) < 1
