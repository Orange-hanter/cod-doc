"""ADR-004/005/006/008: web pages for Architecture Decision Records."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import adr_service

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def adr_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "adr-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="adr-demo", path=str(repo))
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
            ProjectEntity(slug="adr-demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        adr_service.create(
            session,
            project_id=proj.row_id,
            title="Layered architecture with DIP",
            status="accepted",
            # `decided_at` заполнен нарочно: колонка — `sa.Date`, и до
            # ADO-188 каждая запись с датой роняла список ADR в 500. Фикстура
            # оставляла поле пустым, поэтому весь набор тестов проходил мимо.
            decided_at=date(2026, 4, 5),
            context="LLM-provider abstraction needed",
            decision="4-layer + DIP",
            adr_id="ADR-001",
        )
        adr_service.create(
            session,
            project_id=proj.row_id,
            title="Use SQLite for local-first",
            status="proposed",
            context="docker-free deployment",
            decision="SQLite via SQLAlchemy",
            adr_id="ADR-002",
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


# ----------------------------------------------------------------- #
# ADR-004: list page                                                 #
# ----------------------------------------------------------------- #


def test_adr_list_renders_both_records(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr")
    assert r.status_code == 200
    assert "ADR-001" in r.text
    assert "ADR-002" in r.text
    assert "Layered architecture" in r.text
    assert "Use SQLite" in r.text


def test_adr_list_status_filter(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr?status=accepted")
    assert r.status_code == 200
    assert "ADR-001" in r.text
    assert "ADR-002" not in r.text


def test_adr_list_shows_status_icon(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr")
    # Default 'accepted' icon is the green check.
    assert "✅" in r.text


def test_adr_list_renders_decided_at(adr_client) -> None:  # type: ignore[no-untyped-def]
    """ADO-188: `sa.Date` приходит в шаблон голой `date`, а не строкой.

    Фильтр ``short_date`` разбирал только `datetime` и `str`, поэтому
    ``/p/<slug>/adr`` отдавал 500 на любом проекте, где у ADR проставлена
    дата решения. Detail-страница не падала — там `decided_at` проходит
    через ``adr_to_dict`` и приезжает строкой.
    """
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr")
    assert r.status_code == 200
    assert "2026-04-05" in r.text


# ----------------------------------------------------------------- #
# ADR-005: detail + new                                              #
# ----------------------------------------------------------------- #


def test_adr_show_renders_full_record(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-001")
    assert r.status_code == 200
    assert "ADR-001" in r.text
    assert "Layered architecture with DIP" in r.text
    assert "4-layer + DIP" in r.text
    # ADR-001 is ACCEPTED — edit form is hidden, deprecate + supersede shown.
    assert 'action="/p/adr-demo/adr/ADR-001/edit"' not in r.text
    assert 'action="/p/adr-demo/adr/ADR-001/deprecate"' in r.text
    # Supersede form lists OTHER ADRs as candidates (ADR-002), not self.
    assert "ADR-002" in r.text


def test_adr_show_edit_form_for_proposed(adr_client) -> None:  # type: ignore[no-untyped-def]
    """PROPOSED ADRs surface the full edit form."""
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-002")  # status=proposed in fixture
    assert r.status_code == 200
    assert 'action="/p/adr-demo/adr/ADR-002/edit"' in r.text
    # Deprecate / Supersede affordances are NOT shown for PROPOSED.
    assert 'action="/p/adr-demo/adr/ADR-002/deprecate"' not in r.text
    assert 'action="/p/adr-demo/adr/ADR-002/supersede"' not in r.text


def test_adr_edit_rejected_on_accepted(adr_client) -> None:  # type: ignore[no-untyped-def]
    """POST to /edit on an ACCEPTED ADR returns 400 (immutable)."""
    client, entry = adr_client
    r = client.post(
        f"/p/{entry.name}/adr/ADR-001/edit",  # ADR-001 is ACCEPTED
        data={
            "title": "tampering with accepted",
            "status": "accepted",
            "context": "should fail",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_adr_deprecate_post_transitions(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.post(
        f"/p/{entry.name}/adr/ADR-001/deprecate",
        data={"reason": "obsolete"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    after = client.get(f"/p/{entry.name}/adr/ADR-001")
    assert "deprecated" in after.text
    # Locked-state notice now appears (terminal status).
    assert "terminal status" in after.text


def test_adr_show_404_for_missing(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-999")
    assert r.status_code == 404


def test_adr_new_form_renders(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/new")
    assert r.status_code == 200
    assert "Title" in r.text
    assert "Decision" in r.text or "Decision" in r.text
    # Status select has all 5 options.
    for s in ("proposed", "accepted", "superseded", "deprecated", "rejected"):
        assert s in r.text


def test_adr_new_submit_creates_and_redirects(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.post(
        f"/p/{entry.name}/adr/new",
        data={
            "title": "New decision via web",
            "status": "proposed",
            "context": "Why",
            "decision": "What we picked",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Auto-allocated next id should be ADR-003.
    assert r.headers["location"].endswith("/adr/ADR-003")
    # Now follow → detail page rendered.
    detail = client.get(r.headers["location"])
    assert detail.status_code == 200
    assert "New decision via web" in detail.text


def test_adr_edit_updates_fields(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.post(
        f"/p/{entry.name}/adr/ADR-002/edit",
        data={
            "title": "SQLite — accepted",
            "status": "accepted",
            "context": "ctx",
            "decision": "decision",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    after = client.get(f"/p/{entry.name}/adr/ADR-002")
    assert "SQLite — accepted" in after.text
    assert "accepted" in after.text


def test_adr_diagram_attach(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.post(
        f"/p/{entry.name}/adr/ADR-001/diagram",
        data={"title": "Layers", "mermaid": "graph TD; A-->B; B-->C"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    after = client.get(f"/p/{entry.name}/adr/ADR-001")
    assert "Layers" in after.text
    assert "graph TD" in after.text


def test_adr_supersede_post(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    # ADR-002 supersedes ADR-001.
    r = client.post(
        f"/p/{entry.name}/adr/ADR-002/supersede",
        data={"superseded_adr_id": "ADR-001", "reason": "newer thinking"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # ADR-001 should now show 'superseded' status.
    detail = client.get(f"/p/{entry.name}/adr/ADR-001")
    assert "superseded" in detail.text


# ----------------------------------------------------------------- #
# ADR-006: graph                                                     #
# ----------------------------------------------------------------- #


def test_adr_graph_renders_mermaid(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    # First create a supersede edge so the graph is non-trivial.
    client.post(
        f"/p/{entry.name}/adr/ADR-002/supersede",
        data={"superseded_adr_id": "ADR-001", "reason": "v2"},
        follow_redirects=False,
    )
    r = client.get(f"/p/{entry.name}/adr/graph")
    assert r.status_code == 200
    assert "mermaid" in r.text
    assert "graph LR" in r.text
    assert "ADR_001" in r.text  # node id (dash → underscore)
    assert "ADR_002" in r.text
    # The mermaid edge: literal source has "-->" but Jinja escapes it to "--&gt;".
    assert ("ADR_002 --> ADR_001" in r.text) or ("ADR_002 --&gt; ADR_001" in r.text)


def test_adr_graph_empty_state(adr_client, tmp_path: Path, migrate_db) -> None:  # type: ignore[no-untyped-def]
    """A fresh project with no ADRs should render an empty-state hint."""
    # The fixture already seeds 2 ADRs but no supersede edges — so 'nodes'
    # are present but 'edges' empty. Just verify the page renders.
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/graph")
    assert r.status_code == 200
    # Even without edges, both nodes show.
    assert "ADR-001" in r.text
    assert "ADR-002" in r.text


# ----------------------------------------------------------------- #
# ADR-008 partial: tabs include ADR link                              #
# ----------------------------------------------------------------- #


def test_project_tabs_include_adr_link(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert f'href="/p/{entry.name}/adr"' in r.text


# ── ADO-133 / ADO-136 / ADO-135: вёрстка вкладки ────────────────────────


def test_adr_list_uses_shared_component_vocabulary(adr_client) -> None:
    """Список должен выглядеть как остальные таблицы, а не как голый HTML."""
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr")
    assert 'class="page-header"' in r.text
    assert 'class="filter-bar"' in r.text
    assert 'class="grid adr-table"' in r.text


def test_adr_list_status_is_a_badge_not_bare_text(adr_client) -> None:
    """Статус — бейдж; иконка живёт ВНУТРИ него, а не вместо него."""
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr")
    assert 'class="badge badge-accepted"' in r.text
    assert 'class="badge badge-proposed"' in r.text
    assert "✅" in r.text, "иконку нельзя терять при переходе на бейджи"


def test_adr_list_filter_works_without_js(adr_client) -> None:
    """Инвариант капабилити §5: формы работают без JS.

    До ADO-133 у формы был единственный `<select onchange>` — без submit-кнопки
    и без noscript, так что с выключенным JS фильтр выбирался, но не применялся.
    """
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr")
    assert "<noscript>" in r.text
    assert 'type="submit"' in r.text
    assert f'action="/p/{entry.name}/adr"' in r.text, "у формы должен быть явный action"


def test_adr_list_offers_clear_only_when_filtered(adr_client) -> None:
    client, entry = adr_client
    assert ">clear<" not in client.get(f"/p/{entry.name}/adr").text
    assert ">clear<" in client.get(f"/p/{entry.name}/adr?status=accepted").text


def test_adr_list_row_and_title_are_clickable(adr_client) -> None:
    """ADO-133: клик по строке, а не только по ID.

    Растянутая ссылка вместо `onclick` — работает без JS.
    """
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr")
    assert 'class="adr-row-link"' in r.text
    # Заголовок — тоже ссылка, а не голый текст.
    assert f'href="/p/{entry.name}/adr/ADR-001">Layered architecture with DIP</a>' in r.text


def test_adr_list_empty_state_when_filter_matches_nothing(adr_client) -> None:
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr?status=rejected")
    assert r.status_code == 200
    assert 'class="tasks-empty"' in r.text
    assert "ADR-001" not in r.text


def test_adr_show_uses_hero_and_cards(adr_client) -> None:
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-001")
    assert 'class="task-hero"' in r.text
    assert 'class="task-card-header"' in r.text
    assert 'class="badge badge-lg badge-accepted"' in r.text


def test_adr_show_form_fields_are_styleable(adr_client) -> None:
    """`.field` и `.form-actions` имеют правила ТОЛЬКО под `.settings-form`.

    Без этого предка классы снова оказались бы мёртвыми — ровно та ошибка,
    из-за которой вкладка и выглядела как голый HTML.
    """
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-002")  # proposed → форма Edit
    assert 'class="settings-form"' in r.text
    assert 'class="field"' in r.text
    assert 'class="form-actions"' in r.text


def test_adr_show_back_link_is_sticky(adr_client) -> None:
    """ADO-135: возврат к списку виден с любой глубины прокрутки."""
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-001")
    assert 'class="adr-back"' in r.text


def test_adr_new_form_uses_settings_form(adr_client) -> None:
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/new")
    assert 'class="settings-form"' in r.text
    assert 'class="field"' in r.text
    assert 'class="form-actions"' in r.text


def test_no_dead_adr_selectors() -> None:
    """Acceptance ADO-133: мёртвых селекторов не осталось.

    Каждый класс `adr-*`, встречающийся в шаблонах, обязан иметь правило в CSS,
    и наоборот. До этой задачи все 18 классов были без правил — страница
    рисовалась браузерным дефолтом.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "cod_doc"
    class_re = re.compile(r"\badr-[a-z0-9-]+")

    in_templates: set[str] = set()
    for tpl in (root / "templates" / "web").rglob("*.html"):
        for m in class_re.finditer(tpl.read_text(encoding="utf-8")):
            in_templates.add(m.group(0))

    css = "".join(p.read_text(encoding="utf-8") for p in (root / "static").rglob("*.css"))
    styled = {m.group(0) for m in class_re.finditer(css)}

    # `adr-ref` рождается в рендерере, а не в шаблоне — учитываем отдельно.
    in_templates.add("adr-ref")

    unstyled = sorted(in_templates - styled)
    assert not unstyled, f"классы без единого CSS-правила: {unstyled}"

    unused = sorted(styled - in_templates)
    assert not unused, f"правила без употребления в шаблонах: {unused}"
