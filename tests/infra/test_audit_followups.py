"""Tests for Section A audit follow-ups (migration 0006).

Covers HI-1 (document_body view), HI-2 (JSON server_default), ME-4 (CHECK
no-self-loop), LO-6 (FK SET NULL), LO-7 (story_acceptance position uniqueness).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    DocumentModel,
    ModuleDependencyModel,
    ModuleModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    SectionModel,
    StoryAcceptanceModel,
    TaskModel,
    UserStoryModel,
)
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path


def _run_alembic_upgrade(db_url: str) -> None:
    run_alembic("upgrade", "head", db_url=db_url)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'audit.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _add_project(session, slug: str = "p") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


# --------------------------- HI-1: document_body view ------------------------


def test_document_body_assembles_preamble_and_sections(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(UTC)

    with transactional(factory) as session:
        proj_id = _add_project(session, "body")
        doc = DocumentModel(
            project_id=proj_id,
            doc_key="d",
            path="d.md",
            type="guide",
            status="active",
            title="D",
            preamble="Intro line.",
            frontmatter_json={},
            created=now,
            last_updated=now,
        )
        session.add(doc)
        session.flush()
        # Insert out of order to verify view honours `position`.
        session.add_all(
            [
                SectionModel(
                    document_id=doc.row_id,
                    anchor="b",
                    heading="Beta",
                    level=3,
                    position=1,
                    body="Beta body.",
                    content_hash="hb",
                ),
                SectionModel(
                    document_id=doc.row_id,
                    anchor="a",
                    heading="Alpha",
                    level=2,
                    position=0,
                    body="Alpha body.",
                    content_hash="ha",
                ),
            ]
        )
        doc_id = doc.row_id

    # ADO-010 (F7): preamble and the first heading are separated by a blank
    # line. Before the fix this asserted the glued form — `Intro line.## Alpha`
    # — which is what corrupted every exported document.
    expected = "Intro line.\n\n## Alpha\n\nAlpha body.\n\n### Beta\n\nBeta body."

    with engine_with_schema.connect() as conn:
        body = conn.execute(
            text("SELECT body FROM document_body WHERE document_id = :d"),
            {"d": doc_id},
        ).scalar_one()

    assert body == expected


def test_document_body_for_doc_without_sections(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """LEFT JOIN must yield preamble with empty section block, not NULL."""
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(UTC)

    with transactional(factory) as session:
        proj_id = _add_project(session, "empty")
        doc = DocumentModel(
            project_id=proj_id,
            doc_key="e",
            path="e.md",
            type="guide",
            status="draft",
            title="E",
            preamble="Just preamble.",
            frontmatter_json={},
            created=now,
            last_updated=now,
        )
        session.add(doc)
        session.flush()
        doc_id = doc.row_id

    with engine_with_schema.connect() as conn:
        body = conn.execute(
            text("SELECT body FROM document_body WHERE document_id = :d"),
            {"d": doc_id},
        ).scalar_one()

    assert body == "Just preamble."


# --------------------------- HI-2: JSON server_default -----------------------


def test_raw_insert_without_json_columns_uses_server_default(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Raw SQL INSERT skipping config_json must rely on server_default '{}', not fail."""
    now = datetime.now(UTC).isoformat()

    with engine_with_schema.begin() as conn:
        # Don't pass config_json — server_default must kick in.
        conn.execute(
            text(
                "INSERT INTO project (slug, title, root_path, created, updated) "
                "VALUES (:s, :t, :r, :c, :u)"
            ),
            {"s": "raw", "t": "Raw", "r": "/tmp/raw", "c": now, "u": now},
        )
        row = conn.execute(text("SELECT config_json FROM project WHERE slug = 'raw'")).scalar_one()
    assert row == "{}"


# --------------------------- ME-4: CHECK no-self-loop ------------------------


def _seed_two_tasks(session) -> tuple[int, int]:
    now = datetime.now(UTC)
    proj_id = _add_project(session, "self")
    plan = PlanModel(project_id=proj_id, scope="self-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A-A", position=0)
    session.add(sec)
    session.flush()
    a = TaskModel(
        project_id=proj_id,
        task_id="T-A",
        plan_id=plan.row_id,
        section_id=sec.row_id,
        title="A",
        status="pending",
        type="feature",
        priority="medium",
        created=now,
        last_updated=now,
    )
    b = TaskModel(
        project_id=proj_id,
        task_id="T-B",
        plan_id=plan.row_id,
        section_id=sec.row_id,
        title="B",
        status="pending",
        type="feature",
        priority="medium",
        created=now,
        last_updated=now,
    )
    session.add_all([a, b])
    session.flush()
    return a.row_id, b.row_id


def test_dependency_self_loop_rejected(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError

    factory = make_session_factory(engine_with_schema)

    with pytest.raises(IntegrityError), transactional(factory) as session:
        a, _ = _seed_two_tasks(session)
        session.add(DependencyModel(from_task_id=a, to_task_id=a, kind="blocks"))


def test_module_self_loop_rejected(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError

    factory = make_session_factory(engine_with_schema)

    with pytest.raises(IntegrityError), transactional(factory) as session:
        proj_id = _add_project(session, "msl")
        m = ModuleModel(project_id=proj_id, module_id="MSL", name="MSL", status="active")
        session.add(m)
        session.flush()
        session.add(ModuleDependencyModel(from_module=m.row_id, to_module=m.row_id))


# --------------------------- LO-6: FK SET NULL ------------------------------


def test_module_spec_doc_id_set_null_on_doc_delete(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Deleting a document must NULL out module.spec_doc_id (not cascade-delete the module)."""
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(UTC)

    with transactional(factory) as session:
        proj_id = _add_project(session, "fknull")
        doc = DocumentModel(
            project_id=proj_id,
            doc_key="spec",
            path="spec.md",
            type="module-spec",
            status="active",
            title="Spec",
            frontmatter_json={},
            created=now,
            last_updated=now,
        )
        session.add(doc)
        session.flush()
        m = ModuleModel(
            project_id=proj_id,
            module_id="MFK",
            name="MFK",
            status="active",
            spec_doc_id=doc.row_id,
        )
        session.add(m)
        session.flush()
        m_id, doc_id = m.row_id, doc.row_id

    with transactional(factory) as session:
        session.delete(session.get(DocumentModel, doc_id))

    with transactional(factory) as session:
        m = session.get(ModuleModel, m_id)
        assert m is not None  # module survives
        assert m.spec_doc_id is None  # FK was nulled


def test_plan_parent_doc_id_set_null_on_doc_delete(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(UTC)

    with transactional(factory) as session:
        proj_id = _add_project(session, "pfk")
        doc = DocumentModel(
            project_id=proj_id,
            doc_key="parent",
            path="parent.md",
            type="execution-plan",
            status="active",
            title="Parent",
            frontmatter_json={},
            created=now,
            last_updated=now,
        )
        session.add(doc)
        session.flush()
        plan = PlanModel(
            project_id=proj_id,
            scope="pfk-plan",
            parent_doc_id=doc.row_id,
            created=now,
            last_updated=now,
        )
        session.add(plan)
        session.flush()
        plan_id, doc_id = plan.row_id, doc.row_id

    with transactional(factory) as session:
        session.delete(session.get(DocumentModel, doc_id))

    with transactional(factory) as session:
        p = session.get(PlanModel, plan_id)
        assert p is not None
        assert p.parent_doc_id is None


# --------------------------- LO-7: story_acceptance UNIQUE -------------------


def test_story_acceptance_position_unique_within_story(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError

    factory = make_session_factory(engine_with_schema)
    now = datetime.now(UTC)

    with transactional(factory) as session:
        proj_id = _add_project(session, "sapos")
        story = UserStoryModel(
            project_id=proj_id,
            story_id="US-POS",
            persona="P",
            narrative="N",
            status="draft",
            priority="low",
            created=now,
            last_updated=now,
        )
        session.add(story)
        session.flush()
        session.add(StoryAcceptanceModel(story_id=story.row_id, position=0, criterion="A"))
        s_id = story.row_id

    with pytest.raises(IntegrityError), transactional(factory) as session:
        session.add(StoryAcceptanceModel(story_id=s_id, position=0, criterion="A2"))
