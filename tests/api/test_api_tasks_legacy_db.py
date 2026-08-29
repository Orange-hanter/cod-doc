"""ADO-037: legacy /api/projects/{name}/tasks поверх task_service (DB).

Finding C3 контракт-аудита ADO-034: до фикса эндпоинты ходили в YAML-путь
(`core/project.py`) — на мигрированных проектах RuntimeError, без Revision /
activity / статус-машины. Тесты ниже проверяют DB-side эффекты (строка task,
Revision, activity event) — на main без фикса они падают, потому что в БД
ничего не появляется.

⚠ lifespan: cfg.save() до входа в TestClient (см. conftest.py).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import make_engine, make_session_factory
from cod_doc.infra.models import ProjectModel, RevisionModel, TaskModel

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db_client(tmp_path: Path, migrate_db):  # type: ignore[no-untyped-def]
    repo = tmp_path / "legacy-repo"
    repo.mkdir()
    entry = ProjectEntry(name="legacy-db", path=str(repo))
    state_db = entry.cod_doc_dir / "state.db"
    entry.cod_doc_dir.mkdir(parents=True, exist_ok=True)
    migrate_db(state_db)

    engine = make_engine(f"sqlite:///{state_db}")
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(ProjectModel(slug=entry.name, title="Legacy DB", root_path=str(repo)))
        session.commit()
    engine.dispose()

    cfg = Config(api_key="sk-test-key", model="test/model", base_url="https://x.example")
    cfg.add_project(entry)
    cfg.save()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry, state_db


def _task_rows(state_db: Path) -> list[TaskModel]:
    engine = make_engine(f"sqlite:///{state_db}")
    factory = make_session_factory(engine)
    with factory() as session:
        rows = list(session.execute(select(TaskModel)).scalars())
        session.expunge_all()
    engine.dispose()
    return rows


def _revisions_for_task(state_db: Path, task_row_id: int) -> list[RevisionModel]:
    engine = make_engine(f"sqlite:///{state_db}")
    factory = make_session_factory(engine)
    with factory() as session:
        rows = list(
            session.execute(
                select(RevisionModel).where(
                    RevisionModel.entity_id == task_row_id,
                    RevisionModel.entity_kind == "task",
                )
            ).scalars()
        )
        session.expunge_all()
    engine.dispose()
    return rows


def test_create_task_persists_to_db_with_revision(db_client) -> None:  # type: ignore[no-untyped-def]
    client, entry, state_db = db_client
    r = client.post(
        f"/api/projects/{entry.name}/tasks",
        json={"title": "DB задача", "priority": 2, "description": "через legacy REST"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "DB задача"
    assert body["status"] == "pending"
    assert body["priority"] == 1  # int-контракт сохранён, маппинг lossy: 1–2 → critical → 1

    rows = _task_rows(state_db)
    assert len(rows) == 1
    assert rows[0].task_id == body["id"]
    assert rows[0].priority == "critical"
    assert _revisions_for_task(state_db, rows[0].row_id), "create должен писать Revision"


def test_list_and_patch_via_status_machine(db_client) -> None:  # type: ignore[no-untyped-def]
    client, entry, state_db = db_client
    r = client.post(f"/api/projects/{entry.name}/tasks", json={"title": "На обновление"})
    task_id = r.json()["id"]

    r = client.get(f"/api/projects/{entry.name}/tasks", params={"status": "pending"})
    assert r.status_code == 200
    assert [t["id"] for t in r.json()] == [task_id]

    r = client.patch(
        f"/api/projects/{entry.name}/tasks/{task_id}",
        json={"status": "done", "result": "сделано через REST"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"

    rows = _task_rows(state_db)
    assert rows[0].status == "done"
    assert rows[0].completed_at is not None


def test_patch_rejects_invalid_transition(db_client) -> None:  # type: ignore[no-untyped-def]
    client, entry, _ = db_client
    r = client.post(f"/api/projects/{entry.name}/tasks", json={"title": "Закрытая"})
    task_id = r.json()["id"]
    client.patch(f"/api/projects/{entry.name}/tasks/{task_id}", json={"status": "done"})

    # done → cancelled не входит в ALLOWED_TRANSITIONS (reopen — только
    # todo/in_progress) — статус-машина должна отрезать.
    r = client.patch(f"/api/projects/{entry.name}/tasks/{task_id}", json={"status": "cancelled"})
    assert r.status_code == 409


def test_patch_rejects_unknown_fields(db_client) -> None:  # type: ignore[no-untyped-def]
    client, entry, _ = db_client
    r = client.post(f"/api/projects/{entry.name}/tasks", json={"title": "Поля"})
    task_id = r.json()["id"]

    r = client.patch(f"/api/projects/{entry.name}/tasks/{task_id}", json={"owner": "x"})
    assert r.status_code == 422


def test_patch_unknown_task_404(db_client) -> None:  # type: ignore[no-untyped-def]
    client, entry, _ = db_client
    r = client.patch(f"/api/projects/{entry.name}/tasks/LEG-999", json={"status": "done"})
    assert r.status_code == 404


def test_tasks_409_without_db(tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "yaml-only"
    repo.mkdir()
    entry = ProjectEntry(name="yaml-only", path=str(repo))
    cfg = Config(api_key="sk-test-key", model="test/model", base_url="https://x.example")
    cfg.add_project(entry)
    cfg.save()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.post(f"/api/projects/{entry.name}/tasks", json={"title": "Нет БД"})
        assert r.status_code == 409
        r = client.get(f"/api/projects/{entry.name}/tasks")
        assert r.status_code == 409
