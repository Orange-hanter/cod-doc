"""TSC-015: web pages for test scenarios (authoring half of RFC 24 §9)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.domain.entities import (
    ScenarioKind,
    ScenarioLinkKind,
    ScenarioRelation,
    ScenarioStatus,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import scenario_service

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def scn_client(tmp_path: Path, migrate_db):  # type: ignore[no-untyped-def]
    repo = tmp_path / "scn-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="scn-demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)
    Project(entry).init()

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="scn-demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        first = scenario_service.create(
            session,
            project_id=proj.row_id,
            title="Plan progress recomputes after a task completes",
            kind=ScenarioKind.HAPPY_PATH,
            doc_key="docs/system/capabilities/plan-management",
            preconditions="A plan with one open task exists.",
            expected="plan_progress reports one task done.",
            steps=["Complete the task", "Read the plan progress"],
            author="human:test",
        )
        scenario_service.link(
            session,
            project_id=proj.row_id,
            scenario_id=first.scenario_id,
            to_kind=ScenarioLinkKind.CRITERION,
            to_ref="US-013#2",
            relation=ScenarioRelation.VERIFIES,
            author="human:test",
        )
        scenario_service.create(
            session,
            project_id=proj.row_id,
            title="Checkout from todo without via_checkout is refused",
            kind=ScenarioKind.ERROR_PATH,
            doc_key="docs/system/capabilities/task-creation",
            preconditions="A task sits in todo.",
            expected="update_status raises.",
            steps=["Call update_status directly"],
            author="human:test",
        )
        retired = scenario_service.create(
            session,
            project_id=proj.row_id,
            title="Obsolete behaviour",
            kind=ScenarioKind.INVARIANT,
            doc_key="docs/system/capabilities/plan-management",
            preconditions="Nothing.",
            expected="Nothing.",
            steps=["Do nothing"],
            author="human:test",
        )
        scenario_service.retire(
            session,
            project_id=proj.row_id,
            scenario_id=retired.scenario_id,
            author="human:test",
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


# ----------------------------------------------------------------- #
# list                                                               #
# ----------------------------------------------------------------- #


def test_list_groups_by_capability(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    r = client.get(f"/p/{entry.name}/scenarios")
    assert r.status_code == 200
    assert "plan-management" in r.text
    assert "task-creation" in r.text
    assert "SCN-001" in r.text
    assert "SCN-002" in r.text


def test_list_hides_retired_by_default(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    r = client.get(f"/p/{entry.name}/scenarios")
    assert "Obsolete behaviour" not in r.text

    r = client.get(f"/p/{entry.name}/scenarios?include_retired=true")
    assert "Obsolete behaviour" in r.text


def test_list_kind_filter(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    r = client.get(f"/p/{entry.name}/scenarios?kind=error_path")
    assert r.status_code == 200
    assert "SCN-002" in r.text
    assert "SCN-001" not in r.text


def test_list_reports_missing_kinds(scn_client) -> None:  # type: ignore[no-untyped-def]
    """task-creation has an error_path but no happy_path — the gap is shown."""
    client, entry = scn_client
    r = client.get(f"/p/{entry.name}/scenarios")
    assert "Не описано" in r.text
    assert "happy_path" in r.text


def test_list_says_coverage_lives_elsewhere(scn_client) -> None:  # type: ignore[no-untyped-def]
    """The intention/evidence split must be visible, not implied."""
    client, entry = scn_client
    r = client.get(f"/p/{entry.name}/scenarios")
    assert "доказан ли сценарий тестом" in r.text
    for verdict in ("covered", "unverifiable"):
        assert verdict not in r.text


def test_list_empty_project_explains_how_to_start(tmp_path: Path, migrate_db) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "empty-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    migrate_db(repo / ".cod-doc" / "state.db")
    entry = ProjectEntry(name="empty-demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)
    Project(entry).init()

    engine = make_engine(f"sqlite:///{repo / '.cod-doc' / 'state.db'}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="empty-demo", title="D", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}/scenarios")
    assert r.status_code == 200
    assert "cod-doc scenario new" in r.text


# ----------------------------------------------------------------- #
# detail                                                             #
# ----------------------------------------------------------------- #


def test_show_renders_steps_in_order(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    r = client.get(f"/p/{entry.name}/scenarios/SCN-001")
    assert r.status_code == 200
    body = r.text
    assert "Complete the task" in body
    assert "Read the plan progress" in body
    assert body.index("Complete the task") < body.index("Read the plan progress")


def test_show_renders_anchor_and_links(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    r = client.get(f"/p/{entry.name}/scenarios/SCN-001")
    assert "docs/system/capabilities/plan-management" in r.text
    assert "US-013#2" in r.text
    assert "verifies" in r.text


def test_show_points_at_the_generated_projection(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    r = client.get(f"/p/{entry.name}/scenarios/SCN-001")
    assert "docs/system/scenarios/plan-management" in r.text
    assert "править файл руками нельзя" in r.text


def test_show_unknown_scenario_is_404(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    r = client.get(f"/p/{entry.name}/scenarios/SCN-404")
    assert r.status_code == 404


def test_retired_scenario_is_still_reachable_by_id(scn_client) -> None:  # type: ignore[no-untyped-def]
    """Retired scenarios keep their id and stay addressable."""
    client, entry = scn_client
    r = client.get(f"/p/{entry.name}/scenarios/SCN-003")
    assert r.status_code == 200
    assert ScenarioStatus.RETIRED.value in r.text
