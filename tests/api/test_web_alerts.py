"""WEB-022: alert/error model.

Verifies:
- WebError raised from a handler under HTMX → returns alert fragment with
  hx-swap-oob and HX-Reswap: none header.
- WebError raised from a handler in a regular form post → 303 redirect to
  Referer + flash_message/flash_severity cookies.
- Cookie-flash is rendered on the next full page load via base.html.
- Inline alert (RevisionConflict) is appended to the task row response as
  a separate hx-swap-oob block.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cod_doc.api import deps
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
    Plan,
    PlanSection,
    Priority,
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

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_upgrade(db_url: str) -> None:
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=True,
        env={"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url},
        capture_output=True,
    )


@pytest.fixture
def alerts_client(tmp_path: Path):
    repo = tmp_path / "alerts-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    _alembic_upgrade(f"sqlite:///{db_path}")

    entry = ProjectEntry(name="demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)
    deps.set_config(cfg)

    Project(entry).init()

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        plan = PlanRepository(session).add(
            Plan(project_id=proj.row_id, scope="alerts", principle="test-first")
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

        tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Task one",
            type=TaskType.TEST,
            priority=Priority.HIGH,
            author="human:dakh",
            id_prefix="ALR",
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


# ── HTMX path: alert OOB fragment + HX-Reswap header ─────────────────────


def test_validation_error_returns_alert_oob_for_htmx(alerts_client) -> None:
    """Posting an invalid status via HTMX returns an alert fragment with hx-swap-oob."""
    client, entry = alerts_client
    r = client.post(
        f"/p/{entry.name}/tasks/ALR-001/status",
        data={"status": "garbage-not-real"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 400
    assert r.headers.get("HX-Reswap") == "none"
    assert 'class="alert alert-warning"' in r.text
    assert 'hx-swap-oob="afterbegin:#alerts"' in r.text
    assert "garbage-not-real" in r.text


def test_not_found_error_returns_alert_oob_for_htmx(alerts_client) -> None:
    """Unknown task via HTMX → 404 + alert-error fragment."""
    client, entry = alerts_client
    r = client.post(
        f"/p/{entry.name}/tasks/UNKNOWN-999/status",
        data={"status": "done"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404
    assert r.headers.get("HX-Reswap") == "none"
    assert 'class="alert alert-error"' in r.text
    assert "UNKNOWN-999" in r.text


# ── Form-post path: 303 redirect + flash cookies ─────────────────────────


def test_validation_error_redirects_with_cookie_flash(alerts_client) -> None:
    """Posting invalid status without HTMX → 303 to Referer with flash cookies."""
    client, entry = alerts_client
    r = client.post(
        f"/p/{entry.name}/tasks/ALR-001/status",
        data={"status": "garbage"},
        headers={"Referer": f"http://testserver/p/{entry.name}/tasks"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"http://testserver/p/{entry.name}/tasks"
    cookies = r.headers.get_list("set-cookie")
    cookie_blob = " ".join(cookies)
    assert "flash_message=" in cookie_blob
    assert "flash_severity=warning" in cookie_blob
    # message is percent-encoded for cookie transport (latin-1 only); the
    # ASCII portion "garbage" survives verbatim in the URL-encoded form.
    assert "garbage" in cookie_blob


def test_not_found_error_redirects_with_cookie_flash(alerts_client) -> None:
    client, entry = alerts_client
    r = client.post(
        f"/p/{entry.name}/tasks/UNKNOWN-999/status",
        data={"status": "done"},
        headers={"Referer": f"http://testserver/p/{entry.name}/tasks"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    cookie_blob = " ".join(r.headers.get_list("set-cookie"))
    assert "flash_severity=error" in cookie_blob


# ── Cookie-flash surfaced on next page load ──────────────────────────────


def test_flash_cookie_renders_on_next_full_page_load(alerts_client) -> None:
    """After the redirect, the flashed alert appears in #alerts on the next page."""
    client, entry = alerts_client
    # Trigger a non-HTMX validation error → cookies set.
    client.post(
        f"/p/{entry.name}/tasks/ALR-001/status",
        data={"status": "garbage"},
        headers={"Referer": f"http://testserver/p/{entry.name}/tasks"},
        follow_redirects=False,
    )
    # Subsequent full-page GET surfaces the alert (cookies persist on TestClient).
    r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200
    assert 'class="alert alert-warning"' in r.text
    assert "garbage" in r.text


# ── Inline-alert path (graceful, conflict during update_status) ──────────


def test_conflict_appends_oob_alert_to_row_response(alerts_client, monkeypatch) -> None:
    """RevisionConflict during update_status is non-fatal; row swap + OOB alert."""
    from cod_doc.api.web import fragments
    from cod_doc.services.revision_service import RevisionConflictError

    client, entry = alerts_client

    # Force a RevisionConflictError from the service layer.
    def boom(*args, **kwargs):
        raise RevisionConflictError("expected vs current head mismatch")

    monkeypatch.setattr(fragments.tasks, "update_status", boom)

    r = client.post(
        f"/p/{entry.name}/tasks/ALR-001/status",
        data={"status": "in-progress"},
        headers={"HX-Request": "true"},
    )
    # 200, because the row IS rendered (with old status); alert OOB is appended.
    assert r.status_code == 200
    # The row is still there
    assert 'id="task-ALR-001"' in r.text
    # The alert-warning OOB block appears as a sibling
    assert 'class="alert alert-warning"' in r.text
    assert 'hx-swap-oob="afterbegin:#alerts"' in r.text
    assert "conflict:" in r.text


# ── Backward-compat: success path returns just the row (no alert) ────────


def test_success_returns_row_only_no_alert(alerts_client) -> None:
    client, entry = alerts_client
    r = client.post(
        f"/p/{entry.name}/tasks/ALR-001/status",
        data={"status": "in-progress"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert 'id="task-ALR-001"' in r.text
    # Old "row-error" span removed; no alert on success
    assert "row-error" not in r.text
    assert "alert-warning" not in r.text
    assert "alert-error" not in r.text
