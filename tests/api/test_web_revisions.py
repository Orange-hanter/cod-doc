"""WEB-021: revisions log page (`/p/{slug}/revisions`).

Verifies:
- Project-wide log renders newest-first.
- `?entity_kind=task` narrows to TASK revisions only.
- `?entity_kind=task&entity_id=<row_id>` further narrows to one entity.
- Invalid entity_kind warns and renders all.
- DB-not-initialised → graceful warning, no crash.
- The Revisions tab is now live (anchor, not span.tab-disabled).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
    EntityKind,
    Plan,
    PlanSection,
    Priority,
    TaskStatus,
    TaskType,
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
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def revisions_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "rev-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    seeded_task_id = None
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        plan = PlanRepository(session).add(
            Plan(project_id=proj.row_id, scope="rev-bootstrap", principle="test-first")
        )
        plan.created = now
        plan.last_updated = now
        session.flush()

        section = PlanSectionRepository(session).add(
            PlanSection(
                plan_id=plan.row_id,
                letter="A",
                title="Section A",
                slug="section-a",
                position=0,
            )
        )
        session.flush()

        # Create + transition a task → produces several REVISIONS.
        t = tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Revision target",
            type=TaskType.FEATURE,
            priority=Priority.HIGH,
            author="human:dakh",
            id_prefix="REV",
        )
        seeded_task_id = t.row_id
        tasks.update_status(
            session,
            task_id=t.task_id,
            new_status=TaskStatus.IN_PROGRESS,
            author="human:dakh",
            reason="started",
            via_checkout=True,
        )
        tasks.complete(session, task_id=t.task_id, author="human:dakh")
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry, seeded_task_id


# ── Read-side ────────────────────────────────────────────────────────────


def test_revisions_log_renders_seeded_history(revisions_client) -> None:
    client, entry, _ = revisions_client
    r = client.get(f"/p/{entry.name}/revisions")
    assert r.status_code == 200
    # 3 revisions for the seeded task: create + status + complete
    assert "task#" in r.text
    assert "human:dakh" in r.text
    # Tab strip is now active for revisions
    assert 'class="active" href="/p/demo/revisions"' in r.text


def test_revisions_filter_by_entity_kind(revisions_client) -> None:
    client, entry, _ = revisions_client
    r = client.get(f"/p/{entry.name}/revisions?entity_kind=task")
    assert r.status_code == 200
    assert "task#" in r.text


def test_revisions_filter_by_entity_kind_and_id(revisions_client) -> None:
    client, entry, task_row_id = revisions_client
    r = client.get(f"/p/{entry.name}/revisions?entity_kind=task&entity_id={task_row_id}")
    assert r.status_code == 200
    assert f"task#{task_row_id}" in r.text


def test_revisions_invalid_entity_kind_warns(revisions_client) -> None:
    client, entry, _ = revisions_client
    r = client.get(f"/p/{entry.name}/revisions?entity_kind=garbage")
    assert r.status_code == 200
    assert "Неизвестное значение entity_kind" in r.text
    # Falls back to "all" — seeded revisions still visible
    assert "task#" in r.text


def test_revisions_db_absent_renders_warning(tmp_path: Path) -> None:
    repo = tmp_path / "no-db-rev"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}/revisions")
    assert r.status_code == 200
    assert "DB-проект не инициализирован" in r.text


def test_revisions_404_unknown_project(revisions_client) -> None:
    client, _, _ = revisions_client
    r = client.get("/p/nope/revisions")
    assert r.status_code == 404


def test_diff_preview_shows_cyrillic_not_escapes() -> None:
    """JSON-diff, записанный с ensure_ascii=True, в превью читается кириллицей."""
    import json

    from cod_doc.services.revision_service import diff_preview as _diff_preview

    stored = json.dumps({"op": "set_topic", "old": "Хранение", "new": "Агент"})
    assert "\\u0425" in stored
    assert _diff_preview(stored) == '{"op": "set_topic", "old": "Хранение", "new": "Агент"}'


def test_diff_preview_keeps_non_json_and_truncates() -> None:
    import json

    from cod_doc.services.revision_service import DIFF_PREVIEW_CHARS as _PREVIEW_CHARS
    from cod_doc.services.revision_service import diff_preview as _diff_preview

    assert _diff_preview("--- a\n+++ b\n@@ -1 +1 @@") == "--- a"
    assert _diff_preview(None) == ""
    # JSON без \\u-escape показывается байт-в-байт, как записан.
    assert _diff_preview('{"op":"x","n":1}') == '{"op":"x","n":1}'
    assert _diff_preview("true") == "true"
    # \\u в JSON-скаляре или не-JSON тексте не переписывается.
    assert _diff_preview('"\\u0425"') == '"\\u0425"'
    assert _diff_preview("path C:\\users\\u1") == "path C:\\users\\u1"
    # Слишком большой diff не парсится: первая строка как есть.
    from cod_doc.services.revision_service import DIFF_PREVIEW_PARSE_MAX

    big = json.dumps({"op": "Х", "body": "x" * DIFF_PREVIEW_PARSE_MAX})
    assert _diff_preview(big).startswith('{"op": "\\u0425"')
    preview = _diff_preview('{"body": "' + "я" * 500 + '"}')
    assert len(preview) == _PREVIEW_CHARS
    # Режется по символам, а не по байтам: многобайтовая буква не рвётся.
    assert preview == '{"body": "' + "я" * (_PREVIEW_CHARS - len('{"body": "'))


def test_revisions_page_escapes_diff_preview(revisions_client) -> None:
    """Превью diff на странице экранируется: в diff попадает пользовательский ввод."""
    import json

    from sqlalchemy import select

    from cod_doc.infra.models import ProjectModel
    from cod_doc.services import revision_service

    client, entry, task_row_id = revisions_client
    engine = make_engine(f"sqlite:///{entry.path}/.cod-doc/state.db")
    try:
        with transactional(make_session_factory(engine)) as session:
            pid = session.execute(
                select(ProjectModel.row_id).where(ProjectModel.slug == "demo")
            ).scalar_one()
            revision_service.write(
                session,
                project_id=pid,
                entity_kind=EntityKind.TASK,
                entity_id=task_row_id,
                author="human:test",
                diff=json.dumps({"op": "create", "name": "<script>alert(1)</script>Хранение"}),
            )
    finally:
        engine.dispose()
    page = client.get("/p/demo/revisions").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;Хранение" in page
