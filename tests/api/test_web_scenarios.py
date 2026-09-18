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

    r = client.get(f"/p/{entry.name}/scenarios?status=all")
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


# --------------------------------------------------------------------------- #
# ADO-133/136: общий словарь вёрстки                                           #
# --------------------------------------------------------------------------- #


def test_list_uses_shared_component_vocabulary(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios").text
    for cls in ('class="page-header"', 'class="filter-bar"', 'class="table-scroll"'):
        assert cls in body, cls
    assert 'class="grid scn-table"' in body


def test_list_status_is_a_badge_not_bare_text(scn_client) -> None:  # type: ignore[no-untyped-def]
    """Иконку нельзя терять при переходе на бейджи."""
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios").text
    assert "badge badge-scn-draft" in body
    assert "✏️" in body


def test_list_filter_works_without_js(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios").text
    assert f'action="/p/{entry.name}/scenarios"' in body
    assert "<noscript>" in body
    assert 'type="submit"' in body


def test_list_offers_clear_only_when_filtered(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    assert ">clear<" not in client.get(f"/p/{entry.name}/scenarios").text
    assert ">clear<" in client.get(f"/p/{entry.name}/scenarios?kind=error_path").text


def test_list_row_and_title_are_clickable(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios").text
    assert 'class="scn-row-link"' in body


def test_list_empty_state_when_filter_matches_nothing(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios?kind=integration").text
    assert 'class="tasks-empty"' in body
    assert "Показать все" in body


def test_show_uses_hero_and_cards(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios/SCN-001").text
    assert 'class="task-hero"' in body
    assert 'class="task-card-header"' in body
    assert "badge badge-lg badge-scn-draft" in body


def test_show_back_link_is_sticky(scn_client) -> None:  # type: ignore[no-untyped-def]
    from pathlib import Path

    client, entry = scn_client
    assert 'class="scn-back"' in client.get(f"/p/{entry.name}/scenarios/SCN-001").text

    css = (Path(__file__).resolve().parents[2] / "cod_doc/static/css/_components.css").read_text(
        encoding="utf-8"
    )
    block = css.split(".adr-back, .scn-back {", 1)[1].split("}", 1)[0]
    assert "position: sticky" in block


def test_show_anchor_links_to_section(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios/SCN-001").text
    assert f"/p/{entry.name}/docs/docs/system/capabilities/plan-management" in body


def test_show_links_to_revisions(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios/SCN-001").text
    assert "entity_kind=scenario" in body


def test_no_dead_scenario_selectors() -> None:
    """Приёмка ADO-133, перенесённая на сценарии: мёртвых селекторов нет."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "cod_doc"
    class_re = re.compile(r"\bscn-[a-z0-9-]+")

    in_templates: set[str] = set()
    for tpl in (root / "templates" / "web").rglob("*.html"):
        for m in class_re.finditer(tpl.read_text(encoding="utf-8")):
            in_templates.add(m.group(0))

    css = "".join(p.read_text(encoding="utf-8") for p in (root / "static").rglob("*.css"))
    styled = {m.group(0) for m in class_re.finditer(css)}

    # Классы статусов собираются интерполяцией `badge-scn-{{ status }}`, поэтому
    # литерала в шаблоне нет. Выводим их из перечисления, а не списком, — тогда
    # новый статус не протухнет молча. Тот же приём, что `adr-ref` в ADR-тесте.
    in_templates.update(f"scn-{s.value}" for s in ScenarioStatus)

    assert not sorted(in_templates - styled), f"без правил: {sorted(in_templates - styled)}"
    assert not sorted(styled - in_templates), f"без употребления: {sorted(styled - in_templates)}"


def test_old_unstyled_class_names_are_gone() -> None:
    """Пиннует конкретный регресс: одиннадцать классов без правил."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "cod_doc/templates/web/project"
    body = (root / "scenarios_list.html").read_text(encoding="utf-8")
    body += (root / "scenario_show.html").read_text(encoding="utf-8")

    for dead in (
        "scenarios-page",
        "scenarios-header",
        "scenarios-filter",
        "scenario-group",
        "scenario-group-links",
        "scenario-page",
        "scenario-header",
        "scenario-body",
        "scenario-steps",
        "scenario-links",
        "scenario-footer",
    ):
        assert dead not in body, f"класс без правила вернулся: {dead}"


# --------------------------------------------------------------------------- #
# §5/§7: инварианты                                                            #
# --------------------------------------------------------------------------- #


def test_list_renders_without_db(tmp_path: Path) -> None:
    """§7: список открывает БД мягко и объясняет, что её нет."""
    repo = tmp_path / "nodb-demo"
    repo.mkdir()
    entry = ProjectEntry(name="nodb-demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}/scenarios")
    assert r.status_code == 200
    assert 'class="warn"' in r.text


def test_list_survives_a_group_with_only_retired_scenarios(scn_client) -> None:  # type: ignore[no-untyped-def]
    """Группа, где всё снято, не исчезает: блоки строятся из покрытия."""
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios").text
    # SCN-003 снят, но его группа plan-management жива за счёт SCN-001.
    assert "plan-management" in body
    body_all = client.get(f"/p/{entry.name}/scenarios?status=retired").text
    assert "Obsolete behaviour" in body_all


def test_kpi_matches_service(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios").text
    assert "Подтверждено" in body
    assert "Сценариев" in body
    assert "Возможностей" in body


def test_group_shows_kind_chips(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios").text
    assert "happy_path 1" in body


def test_drift_card_is_rendered(scn_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = scn_client
    body = client.get(f"/p/{entry.name}/scenarios").text
    assert "Проекция" in body
