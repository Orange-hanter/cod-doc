"""COD-005 smoke: tag (§3.12) + junction tables + partial link index."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    DocumentModel,
    DocumentTagModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    StoryTagModel,
    TagModel,
    TaskModel,
    TaskTagModel,
    UserStoryModel,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'tags.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _seed_project_with_targets(session) -> dict[str, int]:
    """Seed one project + one document + one task + one story. Return their row_ids."""
    now = datetime.now(timezone.utc)

    proj = ProjectModel(slug="t", title="T", root_path="/tmp/t", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()

    doc = DocumentModel(
        project_id=proj.row_id,
        doc_key="d",
        path="d.md",
        type="guide",
        status="active",
        title="D",
        frontmatter_json={},
        created=now,
        last_updated=now,
    )
    session.add(doc)

    plan = PlanModel(project_id=proj.row_id, scope="t-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="A", slug="A-A", position=0
    )
    session.add(sec)
    session.flush()

    task = TaskModel(
        project_id=proj.row_id,
        task_id="T-001",
        plan_id=plan.row_id,
        section_id=sec.row_id,
        title="T1",
        status="pending",
        type="feature",
        priority="medium",
        created=now,
        last_updated=now,
    )
    session.add(task)

    story = UserStoryModel(
        project_id=proj.row_id,
        story_id="US-001",
        persona="P",
        narrative="N",
        status="draft",
        priority="medium",
        created=now,
        last_updated=now,
    )
    session.add(story)
    session.flush()

    return {
        "project_id": proj.row_id,
        "document_id": doc.row_id,
        "task_id": task.row_id,
        "story_id": story.row_id,
    }


def test_migration_creates_tag_tables(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    with engine_with_schema.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    assert {"tag", "document_tag", "task_tag", "story_tag"} <= tables


def test_link_partial_index_replaces_unresolved(engine_with_schema) -> None:
    """ix_link_broken exists as a partial index; old ix_link_unresolved is gone."""
    with engine_with_schema.connect() as conn:
        rows = list(
            conn.execute(
                text(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='link'"
                )
            )
        )
    by_name = {name: sql for name, sql in rows}
    assert "ix_link_unresolved" not in by_name
    assert "ix_link_broken" in by_name
    assert "WHERE" in (by_name["ix_link_broken"] or "").upper()


def test_tag_unique_per_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Same tag name allowed across projects, banned within one."""
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    factory = make_session_factory(engine_with_schema)
    now = datetime.now(timezone.utc)

    with transactional(factory) as session:
        a = ProjectModel(slug="A", title="A", root_path="/a", config_json={})
        a.created = now
        a.updated = now
        b = ProjectModel(slug="B", title="B", root_path="/b", config_json={})
        b.created = now
        b.updated = now
        session.add_all([a, b])
        session.flush()
        session.add_all(
            [
                TagModel(project_id=a.row_id, name="urgent"),
                TagModel(project_id=b.row_id, name="urgent"),  # ok — different project
            ]
        )
        a_id = a.row_id

    with pytest.raises(IntegrityError):
        with transactional(factory) as session:
            session.add(TagModel(project_id=a_id, name="urgent"))


def test_attach_tags_to_document_task_story(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        ids = _seed_project_with_targets(session)
        urgent = TagModel(project_id=ids["project_id"], name="urgent")
        backend = TagModel(project_id=ids["project_id"], name="backend")
        session.add_all([urgent, backend])
        session.flush()

        session.add_all(
            [
                DocumentTagModel(document_id=ids["document_id"], tag_id=urgent.row_id),
                DocumentTagModel(document_id=ids["document_id"], tag_id=backend.row_id),
                TaskTagModel(task_id=ids["task_id"], tag_id=urgent.row_id),
                StoryTagModel(story_id=ids["story_id"], tag_id=backend.row_id),
            ]
        )

    with engine_with_schema.connect() as conn:
        doc_tags = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT t.name FROM tag t "
                    "JOIN document_tag dt ON dt.tag_id = t.row_id "
                    "WHERE dt.document_id = :d"
                ),
                {"d": ids["document_id"]},
            )
        }
        task_tags = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT t.name FROM tag t "
                    "JOIN task_tag tt ON tt.tag_id = t.row_id "
                    "WHERE tt.task_id = :t"
                ),
                {"t": ids["task_id"]},
            )
        }
        story_tags = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT t.name FROM tag t "
                    "JOIN story_tag st ON st.tag_id = t.row_id "
                    "WHERE st.story_id = :s"
                ),
                {"s": ids["story_id"]},
            )
        }
    assert doc_tags == {"urgent", "backend"}
    assert task_tags == {"urgent"}
    assert story_tags == {"backend"}


def test_junction_pk_prevents_duplicate_attach(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """(document_id, tag_id) is the primary key — same pair twice must fail."""
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        ids = _seed_project_with_targets(session)
        tag = TagModel(project_id=ids["project_id"], name="x")
        session.add(tag)
        session.flush()
        session.add(DocumentTagModel(document_id=ids["document_id"], tag_id=tag.row_id))
        doc_id, tag_id = ids["document_id"], tag.row_id

    with pytest.raises(IntegrityError):
        with transactional(factory) as session:
            session.add(DocumentTagModel(document_id=doc_id, tag_id=tag_id))


def test_cascade_delete_tag_drops_attachments(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        ids = _seed_project_with_targets(session)
        tag = TagModel(project_id=ids["project_id"], name="ephemeral")
        session.add(tag)
        session.flush()
        session.add(DocumentTagModel(document_id=ids["document_id"], tag_id=tag.row_id))
        session.add(TaskTagModel(task_id=ids["task_id"], tag_id=tag.row_id))
        tag_id = tag.row_id

    with transactional(factory) as session:
        session.delete(session.get(TagModel, tag_id))

    with engine_with_schema.connect() as conn:
        dt = conn.execute(
            text("SELECT COUNT(*) FROM document_tag WHERE tag_id = :t"), {"t": tag_id}
        ).scalar_one()
        tt = conn.execute(
            text("SELECT COUNT(*) FROM task_tag WHERE tag_id = :t"), {"t": tag_id}
        ).scalar_one()
    assert (dt, tt) == (0, 0)
