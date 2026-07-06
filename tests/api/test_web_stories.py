"""COD-068 / COD-069: Stories tab + AI generation flow."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
    Plan,
    PlanSection,
    Priority,
    UserStoryStatus,
)
from cod_doc.domain.entities import (
    Project as ProjectEntity,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import story_service

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def stories_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "stories-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()
    # Seed MASTER.md so generate-stories has docs to read.
    entry.master_path.write_text(
        "# Project Master\n\n## Sections\n- A: Auth\n- B: Search\n",
        encoding="utf-8",
    )

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    project_db_id: int | None = None
    plan_id: int | None = None
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()
        project_db_id = proj.row_id

        plan = PlanRepository(session).add(
            Plan(project_id=proj.row_id, scope="cod-doc", principle="test-first")
        )
        plan.created = now
        plan.last_updated = now
        session.flush()
        plan_id = plan.row_id

        PlanSectionRepository(session).add(
            PlanSection(
                plan_id=plan.row_id,
                letter="A",
                title="Core",
                slug="A-Core",
                position=0,
            )
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry, project_db_id, plan_id


# ── Stories list page ──────────────────────────────────────────────────


def test_stories_list_renders_empty(stories_client) -> None:
    client, entry, _pid, _ = stories_client
    r = client.get(f"/p/{entry.name}/stories")
    assert r.status_code == 200
    assert "User stories" in r.text
    assert "Generate from docs" in r.text
    assert "ещё не создан" in r.text.lower() or "stories" in r.text.lower()
    from tests.api.conftest import assert_active_tab

    assert_active_tab(r.text, "demo", "stories")


def test_stories_list_shows_existing(stories_client) -> None:
    client, entry, project_db_id, _ = stories_client

    db_path = entry.cod_doc_dir / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        story_service.create(
            session,
            project_id=project_db_id,
            story_id="US-001",
            persona="developer",
            narrative="I want quick search",
            priority=Priority.HIGH,
            author="human:test",
            status=UserStoryStatus.DRAFT,
            acceptance=["search returns ≤500ms"],
        )
    engine.dispose()

    r = client.get(f"/p/{entry.name}/stories")
    assert r.status_code == 200
    assert "US-001" in r.text
    assert "developer" in r.text
    assert "I want quick search" in r.text


# ── Generate stories from docs ─────────────────────────────────────────


def test_stories_generate_returns_drafts_fragment(stories_client, monkeypatch) -> None:
    client, entry, _pid, _ = stories_client
    from cod_doc.services import ai_generate

    def fake(docs_text, *, cfg, intent=""):
        return [
            ai_generate.StoryDraft(
                persona="developer",
                narrative="I want X so that Y",
                priority="high",
                acceptance=["criterion 1", "criterion 2"],
            ),
            ai_generate.StoryDraft(
                persona="ops",
                narrative="I want Z so that W",
                priority="medium",
            ),
        ], ai_generate.GenerationMeta(model="test/m", input_tokens=10)

    monkeypatch.setattr(ai_generate, "generate_stories", fake)

    r = client.post(
        f"/p/{entry.name}/stories/generate",
        data={"intent": "focus on auth"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    body = r.text
    assert "2 stories proposed" in body
    assert "I want X so that Y" in body
    assert "I want Z so that W" in body
    assert 'name="selected"' in body
    assert "criterion 1" in body


def test_stories_generate_surfaces_error(stories_client, monkeypatch) -> None:
    client, entry, _pid, _ = stories_client
    from cod_doc.services import ai_generate
    from cod_doc.services.ai_text import AIBackendError

    def boom(docs_text, *, cfg, intent=""):
        raise AIBackendError("rate limited")

    monkeypatch.setattr(ai_generate, "generate_stories", boom)
    r = client.post(
        f"/p/{entry.name}/stories/generate",
        data={"intent": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "AI error: rate limited" in r.text


def test_stories_save_one_failure_does_not_kill_others(stories_client, monkeypatch) -> None:
    """COD-071: savepoint isolation — second draft fails, first + third land."""
    client, entry, _pid, _ = stories_client

    from cod_doc.services import story_service

    real_create = story_service.create
    call_count = {"n": 0}

    def flaky(session, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("boom on second draft")
        return real_create(session, **kwargs)

    monkeypatch.setattr(story_service, "create", flaky)

    r = client.post(
        f"/p/{entry.name}/stories/save",
        data={
            "selected": ["0", "1", "2"],
            "persona": ["a", "b", "c"],
            "narrative": ["I want A", "I want B", "I want C"],
            "priority": ["high", "medium", "low"],
            "acceptance": ["", "", ""],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # 1 + 3 saved, 2 dropped — saved counter must reflect only persisted rows.
    assert "saved=2" in r.headers["location"]
    follow = client.get(f"/p/{entry.name}/stories")
    assert "I want A" in follow.text
    assert "I want C" in follow.text
    assert "I want B" not in follow.text


def test_stories_save_persists_selected(stories_client) -> None:
    client, entry, _pid, _ = stories_client
    r = client.post(
        f"/p/{entry.name}/stories/save",
        data={
            "selected": ["0", "2"],
            "persona": ["developer", "ops", "architect"],
            "narrative": ["I want A", "I want B", "I want C"],
            "priority": ["high", "medium", "low"],
            "acceptance": ["a1\na2", "b1", "c1"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "saved=2" in r.headers["location"]

    # Verify they exist with auto-numbered IDs
    follow = client.get(f"/p/{entry.name}/stories")
    assert "US-001" in follow.text
    assert "US-002" in follow.text
    assert "I want A" in follow.text
    assert "I want C" in follow.text
    assert "I want B" not in follow.text


# ── COD-069: story detail + generate tasks ────────────────────────────


def test_story_show_renders_detail(stories_client) -> None:
    client, entry, project_db_id, _ = stories_client

    db_path = entry.cod_doc_dir / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        story_service.create(
            session,
            project_id=project_db_id,
            story_id="US-001",
            persona="developer",
            narrative="I want search",
            priority=Priority.HIGH,
            author="human:test",
            acceptance=["fast"],
        )
    engine.dispose()

    r = client.get(f"/p/{entry.name}/stories/US-001")
    assert r.status_code == 200
    body = r.text
    assert "US-001" in body
    assert "I want search" in body
    assert "Generate tasks via AI" in body
    assert "fast" in body  # acceptance criterion
    assert "0/0" in body  # no linked tasks yet


def test_story_tasks_generate_returns_drafts(stories_client, monkeypatch) -> None:
    client, entry, project_db_id, _ = stories_client

    db_path = entry.cod_doc_dir / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        story_service.create(
            session,
            project_id=project_db_id,
            story_id="US-001",
            persona="developer",
            narrative="I want search",
            priority=Priority.HIGH,
            author="human:test",
        )
    engine.dispose()

    from cod_doc.services import ai_generate

    def fake(persona, narrative, *, cfg, section_layout=None, intent=""):
        return [
            ai_generate.TaskDraft(
                title="Implement search", type="feature", priority="high", section_letter="A"
            ),
            ai_generate.TaskDraft(
                title="Test search", type="test", priority="medium", section_letter="A"
            ),
        ], ai_generate.GenerationMeta(model="test/m")

    monkeypatch.setattr(ai_generate, "generate_tasks_for_story", fake)
    r = client.post(
        f"/p/{entry.name}/stories/US-001/tasks/generate",
        data={"plan_scope": "cod-doc", "intent": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    body = r.text
    assert "2 tasks proposed" in body
    assert "Implement search" in body
    assert "Test search" in body
    # Plan scope is preserved in hidden field
    assert 'value="cod-doc"' in body


def test_story_tasks_save_creates_and_links(stories_client) -> None:
    client, entry, project_db_id, _plan_id = stories_client

    db_path = entry.cod_doc_dir / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        story_service.create(
            session,
            project_id=project_db_id,
            story_id="US-001",
            persona="developer",
            narrative="I want search",
            priority=Priority.HIGH,
            author="human:test",
        )
    engine.dispose()

    r = client.post(
        f"/p/{entry.name}/stories/US-001/tasks/save",
        data={
            "plan_scope": "cod-doc",
            "selected": ["0", "1"],
            "title": ["Implement search", "Test search"],
            "type": ["feature", "test"],
            "priority": ["high", "medium"],
            "section_letter": ["A", "A"],
            "description": ["", ""],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "saved=2" in r.headers["location"]

    # Story page now shows the linked tasks
    follow = client.get(f"/p/{entry.name}/stories/US-001")
    assert "0/2" in follow.text
    assert "Implement search" in follow.text


def test_story_tasks_save_requires_plan_scope(stories_client) -> None:
    client, entry, project_db_id, _ = stories_client

    db_path = entry.cod_doc_dir / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        story_service.create(
            session,
            project_id=project_db_id,
            story_id="US-001",
            persona="developer",
            narrative="I want X",
            priority=Priority.MEDIUM,
            author="human:test",
        )
    engine.dispose()

    r = client.post(
        f"/p/{entry.name}/stories/US-001/tasks/save",
        data={"selected": "0", "title": "x"},
    )
    assert r.status_code == 400


def test_story_show_404_unknown(stories_client) -> None:
    client, entry, *_ = stories_client
    r = client.get(f"/p/{entry.name}/stories/US-999")
    assert r.status_code == 404


# ── COD-060: import from folder ───────────────────────────────────────


def test_import_master_scan_returns_draft(stories_client, monkeypatch) -> None:
    client, entry, _pid, _ = stories_client

    # Plant a file the walker should pick up.
    (entry.cod_doc_dir.parent / "README.md").write_text("# Demo repo", encoding="utf-8")

    from cod_doc.services import ai_generate

    def fake(files, *, cfg, intent=""):
        assert files, "walker should have found README.md"
        return ai_generate.MasterDraft(
            master_md="# Project Master\n\n## A: Auth\n- README.md — entry point",
            coverage_tasks=[
                ai_generate.TaskDraft(
                    title="Document auth flow",
                    type="docs",
                    priority="medium",
                    section_letter="A",
                )
            ],
            files_seen=[p for p, _ in files],
        ), ai_generate.GenerationMeta(model="test/m")

    monkeypatch.setattr(ai_generate, "generate_master_from_folder", fake)

    r = client.post(
        f"/p/{entry.name}/import_master/scan",
        data={"intent": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    body = r.text
    assert "Project Master" in body
    assert "Document auth flow" in body
    assert "Files scanned" in body
    assert "README.md" in body


def test_import_master_save_writes_file_and_creates_tasks(stories_client) -> None:
    client, entry, _pid, _ = stories_client

    new_master = "# New Master\n\n## Sections\n- A: Done"
    r = client.post(
        f"/p/{entry.name}/import_master/save",
        data={
            "master_md": new_master,
            "write_master": "on",
            "plan_scope": "cod-doc",
            "selected": ["0"],
            "title": ["Document auth flow"],
            "type": ["docs"],
            "priority": ["medium"],
            "section_letter": ["A"],
            "description": [""],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "tasks_saved=1" in r.headers["location"]
    # File on disk was overwritten.
    assert entry.master_path.read_text() == new_master


def test_import_master_save_rejects_empty_master(stories_client) -> None:
    client, entry, *_ = stories_client
    r = client.post(
        f"/p/{entry.name}/import_master/save",
        data={"master_md": "  "},
    )
    assert r.status_code == 400
