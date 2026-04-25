"""COD-003 smoke: user_story / story_acceptance / story_link / module schema."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    ModuleCodeModel,
    ModuleDependencyModel,
    ModuleModel,
    ProjectModel,
    StoryAcceptanceModel,
    StoryLinkModel,
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
    return f"sqlite:///{tmp_path / 'stories.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _add_project(session, slug: str = "p") -> int:
    now = datetime.now(timezone.utc)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def test_migration_creates_all_tables(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    with engine_with_schema.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    assert {
        "user_story",
        "story_acceptance",
        "story_link",
        "module",
        "module_dependency",
        "module_code",
    } <= names


def test_user_story_with_acceptance_and_links(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(timezone.utc)

    with transactional(factory) as session:
        proj_id = _add_project(session, "demo")
        story = UserStoryModel(
            project_id=proj_id,
            story_id="US-014",
            persona="Agency Owner",
            narrative="As an Agency Owner, I want X, so Y",
            status="accepted",
            priority="high",
            created=now,
            last_updated=now,
        )
        session.add(story)
        session.flush()

        session.add_all(
            [
                StoryAcceptanceModel(
                    story_id=story.row_id, position=0, criterion="A1", met=True
                ),
                StoryAcceptanceModel(
                    story_id=story.row_id, position=1, criterion="A2", met=False
                ),
                StoryLinkModel(
                    story_id=story.row_id,
                    to_kind="task",
                    to_ref="AUTH-025",
                    relation="implemented_by",
                ),
                StoryLinkModel(
                    story_id=story.row_id,
                    to_kind="document",
                    to_ref="modules/M1-auth/overview",
                    relation="specified_in",
                ),
            ]
        )

    with transactional(factory) as session:
        story = session.execute(
            select(UserStoryModel).where(UserStoryModel.story_id == "US-014")
        ).scalar_one()
        assert [a.position for a in story.acceptance] == [0, 1]
        assert [a.met for a in story.acceptance] == [True, False]
        assert {(l.to_kind, l.to_ref, l.relation) for l in story.links} == {
            ("task", "AUTH-025", "implemented_by"),
            ("document", "modules/M1-auth/overview", "specified_in"),
        }


def test_user_story_id_is_unique_globally(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """story_id is globally unique per DATA_MODEL §6."""
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    factory = make_session_factory(engine_with_schema)
    now = datetime.now(timezone.utc)

    with transactional(factory) as session:
        a_id = _add_project(session, "a")
        b_id = _add_project(session, "b")
        session.add(
            UserStoryModel(
                project_id=a_id,
                story_id="US-001",
                persona="X",
                narrative="N",
                status="draft",
                priority="medium",
                created=now,
                last_updated=now,
            )
        )

    with pytest.raises(IntegrityError):
        with transactional(factory) as session:
            session.add(
                UserStoryModel(
                    project_id=b_id,
                    story_id="US-001",
                    persona="Y",
                    narrative="N2",
                    status="draft",
                    priority="medium",
                    created=now,
                    last_updated=now,
                )
            )


def test_story_acceptance_cascade_delete(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(timezone.utc)

    with transactional(factory) as session:
        proj_id = _add_project(session, "casc")
        story = UserStoryModel(
            project_id=proj_id,
            story_id="US-CASC",
            persona="P",
            narrative="N",
            status="draft",
            priority="low",
            created=now,
            last_updated=now,
        )
        session.add(story)
        session.flush()
        session.add(
            StoryAcceptanceModel(story_id=story.row_id, position=0, criterion="C")
        )
        session.add(
            StoryLinkModel(
                story_id=story.row_id,
                to_kind="module",
                to_ref="M1-auth",
                relation="owned_by",
            )
        )
        story_pk = story.row_id

    with transactional(factory) as session:
        session.delete(session.get(UserStoryModel, story_pk))

    with engine_with_schema.connect() as conn:
        ac = conn.execute(
            text("SELECT COUNT(*) FROM story_acceptance WHERE story_id = :s"),
            {"s": story_pk},
        ).scalar_one()
        ln = conn.execute(
            text("SELECT COUNT(*) FROM story_link WHERE story_id = :s"),
            {"s": story_pk},
        ).scalar_one()
    assert (ac, ln) == (0, 0)


def test_module_with_dependencies_and_code(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session, "mod")
        m1 = ModuleModel(project_id=proj_id, module_id="M1-auth", name="Auth", status="active")
        m2 = ModuleModel(
            project_id=proj_id, module_id="M2-billing", name="Billing", status="proposed"
        )
        session.add_all([m1, m2])
        session.flush()

        session.add(
            ModuleDependencyModel(
                from_module=m2.row_id, to_module=m1.row_id, reason="billing needs auth"
            )
        )
        session.add_all(
            [
                ModuleCodeModel(module_id=m1.row_id, kind="backend", path="src/auth"),
                ModuleCodeModel(module_id=m1.row_id, kind="tests", path="tests/auth"),
            ]
        )

    with transactional(factory) as session:
        m1 = session.execute(
            select(ModuleModel).where(ModuleModel.module_id == "M1-auth")
        ).scalar_one()
        assert {(c.kind, c.path) for c in m1.code_paths} == {
            ("backend", "src/auth"),
            ("tests", "tests/auth"),
        }
        m2 = session.execute(
            select(ModuleModel).where(ModuleModel.module_id == "M2-billing")
        ).scalar_one()
        assert [(d.from_module, d.to_module) for d in m2.outgoing_deps] == [
            (m2.row_id, m1.row_id)
        ]


def test_module_dependency_unique_edge(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session, "uniq")
        m1 = ModuleModel(project_id=proj_id, module_id="UA", name="A", status="active")
        m2 = ModuleModel(project_id=proj_id, module_id="UB", name="B", status="active")
        session.add_all([m1, m2])
        session.flush()
        session.add(ModuleDependencyModel(from_module=m1.row_id, to_module=m2.row_id))
        a, b = m1.row_id, m2.row_id

    with pytest.raises(IntegrityError):
        with transactional(factory) as session:
            session.add(ModuleDependencyModel(from_module=a, to_module=b))


def test_module_id_unique_globally(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        a = _add_project(session, "ma")
        b = _add_project(session, "mb")
        session.add(ModuleModel(project_id=a, module_id="M1-auth", name="Auth A", status="active"))

    with pytest.raises(IntegrityError):
        with transactional(factory) as session:
            session.add(
                ModuleModel(project_id=b, module_id="M1-auth", name="Auth B", status="active")
            )
