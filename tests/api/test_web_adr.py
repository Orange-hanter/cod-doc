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


def test_adr_list_shows_status_glyph(adr_client) -> None:  # type: ignore[no-untyped-def]
    """ARG-004: статус — знак слева; у действующего пусто, у черновика «!»."""
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr").text
    assert '<td class="adr-col-glyph adr-glyph-proposed" title="proposed">' in r
    assert '<td class="adr-col-glyph " title="accepted">' in r
    # Статус словом — для скринридера.
    assert '<span class="adr-sr">accepted</span>' in r


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
    assert "ADR_002 --&gt;|replaces| ADR_001" in r.text
    # Узел окрашен по статусу через classDef, а не эмодзи в подписи.
    assert "]:::superseded" in r.text
    assert "classDef superseded" in r.text


def test_adr_graph_draws_only_chained_nodes(adr_client) -> None:  # type: ignore[no-untyped-def]
    """Одиночные ADR в mermaid не идут — иначе стрелку не найти среди
    несвязанных прямоугольников; они перечислены блоком «Standalone»."""
    client, entry = adr_client
    create = client.post(
        f"/p/{entry.name}/adr/new",
        data={"title": "Unrelated", "status": "proposed"},
        follow_redirects=False,
    )
    assert create.status_code == 303
    client.post(
        f"/p/{entry.name}/adr/ADR-002/supersede",
        data={"superseded_adr_id": "ADR-001"},
        follow_redirects=False,
    )
    r = client.get(f"/p/{entry.name}/adr/graph")
    mermaid = r.text.split('<div class="mermaid adr-graph-canvas">', 1)[1].split("</div>", 1)[0]
    assert "ADR_001" in mermaid
    assert "ADR_003" not in mermaid
    assert "Standalone" in r.text
    assert f'href="/p/{entry.name}/adr/ADR-003"' in r.text
    # Схема — под списками, а не над ними.
    assert r.text.index("Standalone") < r.text.index('<div class="mermaid adr-graph-canvas">')


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
    assert 'class="filter-bar adr-filter adr-summary"' in r.text
    assert 'class="grid adr-table"' in r.text


def test_adr_list_drops_status_badges(adr_client) -> None:
    """ARG-004: бейдж статуса в строке заменён знаком — строка действующего молчит."""
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr").text
    assert 'class="badge badge-accepted"' not in r
    assert 'class="badge badge-proposed"' not in r


def test_adr_list_filter_works_without_js(adr_client) -> None:
    """Инвариант капабилити §5: формы работают без JS.

    ARG-004: фильтры — ссылки сводки (``?view=``), JS им не нужен.
    """
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr")
    assert "onchange" not in r.text
    assert f'href="/p/{entry.name}/adr?view=pending"' in r.text


def test_adr_list_summary_counts(adr_client) -> None:
    """Сводка: всего, действуют, ждут решения; пустые фильтры не рисуются."""
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr").text
    assert "2 decisions</a>" in r
    assert "<span>1 in force</span>" in r
    assert "1 awaiting decision</a>" in r
    assert "?view=replaced" not in r
    assert "?view=gaps" not in r


def test_adr_list_marks_active_filter(adr_client) -> None:
    client, entry = adr_client
    unfiltered = client.get(f"/p/{entry.name}/adr").text
    assert f'href="/p/{entry.name}/adr" aria-current="page"' in unfiltered
    filtered = client.get(f"/p/{entry.name}/adr?view=pending").text
    assert f'href="/p/{entry.name}/adr?view=pending" aria-current="page"' in filtered
    assert f'href="/p/{entry.name}/adr" aria-current="page"' not in filtered
    assert "ADR-002" in filtered
    assert "ADR-001" not in filtered


def test_adr_supersede_relation_visible_on_list_and_detail(adr_client) -> None:
    """Связь «кем заменён» видна там, где читают, а не только на графе.

    ARG-004: заменённое строкой по умолчанию не показывается — на него ведёт
    пометка «replaces» у преемника; само оно — в фильтре «replaced».
    """
    client, entry = adr_client
    client.post(
        f"/p/{entry.name}/adr/ADR-002/supersede",
        data={"superseded_adr_id": "ADR-001", "reason": "v2"},
        follow_redirects=False,
    )
    listing = client.get(f"/p/{entry.name}/adr").text
    assert 'data-adr="ADR-001"' not in listing
    assert "· replaces" in listing
    assert 'data-target="ADR-001"' in listing
    replaced = client.get(f"/p/{entry.name}/adr?view=replaced").text
    assert 'class="adr-row-closed"' in replaced
    assert "· replaced by" in replaced
    old = client.get(f"/p/{entry.name}/adr/ADR-001").text
    assert "No longer in force." in old
    assert f'href="/p/{entry.name}/adr/ADR-002"' in old
    new = client.get(f"/p/{entry.name}/adr/ADR-002").text
    assert "Replaces ADR-001" in new
    assert "No longer in force." not in new


def test_adr_show_task_link_to_missing_task_is_kept(adr_client) -> None:
    """Ссылка из ADR на задачу, которой нет в БД, не роняет страницу и не
    пропадает: сама ссылка — факт, а название подставить неоткуда."""
    client, entry = adr_client
    engine = make_engine(f"sqlite:///{entry.path}/.cod-doc/state.db")
    with transactional(make_session_factory(engine)) as session:
        proj = ProjectRepository(session).get_by_slug("adr-demo")
        assert proj is not None
        assert proj.row_id is not None
        adr_service.link_task(session, project_id=proj.row_id, adr_id="ADR-001", task_id="ADO-999")
    engine.dispose()

    r = client.get(f"/p/{entry.name}/adr/ADR-001")
    assert r.status_code == 200
    assert f'href="/p/{entry.name}/tasks/ADO-999"' in r.text
    assert "task not found" in r.text


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
    # ARG-004: классы знаков и колонки «когда» выбирает роут списка.
    pages = root / "api" / "web" / "pages" / "adr.py"
    in_templates |= {m.group(0) for m in class_re.finditer(pages.read_text(encoding="utf-8"))}

    unstyled = sorted(in_templates - styled)
    assert not unstyled, f"классы без единого CSS-правила: {unstyled}"

    unused = sorted(styled - in_templates)
    assert not unused, f"правила без употребления в шаблонах: {unused}"


# ── ADO-229: суть решения в начале карточки ─────────────────────────────


def _set_decision(entry: ProjectEntry, adr_id: str, decision: str) -> None:
    engine = make_engine(f"sqlite:///{entry.path}/.cod-doc/state.db")
    with transactional(make_session_factory(engine)) as session:
        proj = ProjectRepository(session).get_by_slug("adr-demo")
        assert proj is not None
        assert proj.row_id is not None
        adr_service.update(
            session,
            project_id=proj.row_id,
            adr_id=adr_id,
            title="Use SQLite for local-first",
            status="proposed",
            decision=decision,
        )
    engine.dispose()


def test_adr_brief_lists_decision_headings(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    _set_decision(
        entry,
        "ADR-002",
        "### 1. `audit_log` — снять\n\nтело\n\n```\n### не заголовок\n```\n\n### 2. run_id — оставить\n",
    )
    r = client.get(f"/p/{entry.name}/adr/ADR-002").text
    assert "Decision in brief" in r
    brief = r.split('class="adr-brief-points"', 1)[1].split("</ol>", 1)[0]
    assert brief.count("<li>") == 2, "заголовок внутри code fence пунктом не считается"
    assert "<code>audit_log</code> — снять" in brief
    assert "1. " not in brief, "ведущий номер срезан — нумерует <ol>"
    # Ссылка пункта ведёт на якорь отрисованного заголовка.
    anchor = brief.split('href="#', 1)[1].split('"', 1)[0]
    assert f'id="{anchor}"' in r
    assert brief.index('<li><a href="#') >= 0


def test_adr_brief_falls_back_to_first_paragraph(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-001").text
    assert 'class="adr-brief-lead">4-layer + DIP</p>' in r


def test_adr_brief_absent_without_decision(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    _set_decision(entry, "ADR-002", "")
    r = client.get(f"/p/{entry.name}/adr/ADR-002").text
    assert "adr-brief" not in r


# ── ADO-230: Referenced by ──────────────────────────────────────────────


def test_adr_show_lists_referencing_adrs(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    _set_decision(entry, "ADR-002", "Опирается на ADR-001.")
    r = client.get(f"/p/{entry.name}/adr/ADR-001").text
    block = r.split("Referenced by", 1)[1].split("adr-side-block", 1)[0]
    assert f'href="/p/{entry.name}/adr/ADR-002"' in block
    empty = client.get(f"/p/{entry.name}/adr/ADR-002").text
    assert "Nothing mentions this ADR yet." in empty


# ── ADO-231: история правок ─────────────────────────────────────────────


def _history_count(html: str) -> int:
    label = html.split('History <span class="count-chip">', 1)[1]
    return int(label.split("<", 1)[0])


def test_adr_show_links_revision_history(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    before = client.get(f"/p/{entry.name}/adr/ADR-002").text
    assert "/revisions?entity_kind=adr&amp;entity_id=" in before
    _set_decision(entry, "ADR-002", "Новая формулировка")
    after = client.get(f"/p/{entry.name}/adr/ADR-002").text
    assert _history_count(after) == _history_count(before) + 1

    url = after.split('href="/p/adr-demo/revisions?', 1)[1].split('"', 1)[0].replace("&amp;", "&")
    page = client.get(f"/p/{entry.name}/revisions?{url}")
    assert page.status_code == 200


# ── ADO-232: пропущенная дата у accepted ─────────────────────────────────


def test_adr_list_flags_accepted_without_date(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    client.post(
        f"/p/{entry.name}/adr/new",
        data={"title": "Undated", "status": "accepted", "decision": "d"},
        follow_redirects=False,
    )
    r = client.get(f"/p/{entry.name}/adr").text
    assert r.count('class="adr-date-missing"') == 1, "proposed без даты — норма, не пробел"


# ── ADO-233: mermaid без CDN ────────────────────────────────────────────


def test_mermaid_loads_from_vendored_static(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    client.post(
        f"/p/{entry.name}/adr/ADR-002/supersede",
        data={"superseded_adr_id": "ADR-001"},
        follow_redirects=False,
    )
    r = client.get(f"/p/{entry.name}/adr/graph").text
    assert '"/static/vendor/mermaid.min.js?v=' in r
    assert "cdn.jsdelivr.net/npm/mermaid" not in r
    # ADO-234: узлы кликабельны нашим скриптом, а не ослабленным mermaid.
    assert 'class="mermaid adr-graph-canvas"' in r
    assert "mermaid:rendered" in r
    assert "securityLevel: 'strict'" in r
    asset = client.get("/static/vendor/mermaid.min.js")
    assert asset.status_code == 200
    assert 'globalThis["mermaid"]' in asset.text


# ── ADO-235: форма — подсказки и предпросмотр ───────────────────────────


def test_adr_new_form_hints_and_next_id(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/new").text
    assert 'placeholder="ADR-003"' in r
    assert r.count('class="adr-hint"') >= 4
    assert f'hx-post="/p/{entry.name}/adr/preview"' in r


def test_adr_preview_renders_without_saving(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.post(
        f"/p/{entry.name}/adr/preview",
        data={
            "decision": "### Пункт\n\n**важно** <script>alert(1)</script> ADR-001",
            "context": "",
        },
    )
    assert r.status_code == 200
    assert ">Decision</h2>" in r.text
    assert ">Context</h2>" not in r.text, "пустой раздел не показываем"
    assert "<strong>важно</strong>" in r.text
    assert "<script>" not in r.text and "&lt;script&gt;" in r.text
    assert f'href="/p/{entry.name}/adr/ADR-001"' in r.text
    listing = client.get(f"/p/{entry.name}/adr").text
    assert "ADR-003" not in listing


def test_adr_edit_form_has_preview(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-002").text  # proposed → Edit
    assert 'class="adr-preview"' in r
    assert "SQLite via SQLAlchemy</textarea>" in r


# ── ADO-236: автор в подсказке, сайдбар сворачивается ───────────────────


def test_adr_list_author_moves_to_tooltip(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr").text
    assert "<th>Author</th>" not in r and ">Author<" not in r
    assert 'title="by human"' in r


def test_adr_show_sidebar_is_collapsible(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-001").text
    # Без JS блок раскрыт: сворачивает его только скрипт на узком экране.
    assert '<details class="adr-side-toggle" open>' in r
    assert "matchMedia('(max-width: 960px)')" in r


# ── ARG-004: сигналы списка ──────────────────────────────────────────────


def _relate(entry, from_id: str, to_id: str, kind: str) -> None:  # type: ignore[no-untyped-def]
    from pathlib import Path

    db = Path(entry.path) / ".cod-doc" / "state.db"
    engine = make_engine(f"sqlite:///{db}")
    with transactional(make_session_factory(engine)) as session:
        proj = ProjectRepository(session).get_by_slug("adr-demo")
        assert proj is not None and proj.row_id is not None
        adr_service.relate(
            session, project_id=proj.row_id, from_adr_id=from_id, to_adr_id=to_id, kind=kind
        )
    engine.dispose()


def test_adr_list_when_column(adr_client) -> None:  # type: ignore[no-untyped-def]
    """«Когда»: дата у действующего, срок ожидания у черновика, «no date» у пробела."""
    client, entry = adr_client
    client.post(
        f"/p/{entry.name}/adr/new",
        data={"title": "Undated", "status": "accepted", "decision": "d"},
        follow_redirects=False,
    )
    r = client.get(f"/p/{entry.name}/adr").text
    assert "2026-04-05" in r
    assert '<span class="adr-when-waiting">waiting 0 d</span>' in r
    assert '<span class="adr-date-missing">no date</span>' in r


def test_adr_list_facts_only_on_flagged_rows(adr_client) -> None:  # type: ignore[no-untyped-def]
    """Строка фактов — у черновика и у записи с пробелом; действующее молчит."""
    client, entry = adr_client
    client.post(
        f"/p/{entry.name}/adr/new",
        data={"title": "Undated", "status": "accepted", "decision": "d"},
        follow_redirects=False,
    )
    r = client.get(f"/p/{entry.name}/adr").text
    assert r.count('class="adr-facts"') == 2  # ADR-002 (proposed) и ADR-003 (без даты)
    assert '<span class="adr-fact-gap">accepted without a decision date</span>' in r
    assert "no doc references" in r
    gaps = client.get(f"/p/{entry.name}/adr?view=gaps").text
    assert 'data-adr="ADR-003"' in gaps
    assert 'data-adr="ADR-001"' not in gaps


def test_adr_list_relation_notes(adr_client) -> None:  # type: ignore[no-untyped-def]
    """Пометки связей: исходящие всегда, входящая amends — только от не-черновика."""
    client, entry = adr_client
    _relate(entry, "ADR-002", "ADR-001", "amends")  # ADR-002 — proposed
    r = client.get(f"/p/{entry.name}/adr").text
    assert "· will amend" in r
    assert "amended by" not in r
    assert "adr-rel-link" in r
    assert "HOLD_MS = 2000" in r  # подсветка связанной строки гаснет сама


# ── ARG-005: карточка — «Needs a decision», принятие, связи ──────────────


def _adr_row(entry, adr_id: str):  # type: ignore[no-untyped-def]
    from pathlib import Path

    db = Path(entry.path) / ".cod-doc" / "state.db"
    engine = make_engine(f"sqlite:///{db}")
    with transactional(make_session_factory(engine)) as session:
        proj = ProjectRepository(session).get_by_slug("adr-demo")
        assert proj is not None and proj.row_id is not None
        row = adr_service.get(session, proj.row_id, adr_id)
        assert row is not None
        out = {"status": row.status, "decided_at": row.decided_at}
    engine.dispose()
    return out


def test_proposed_card_shows_decision_context(adr_client) -> None:  # type: ignore[no-untyped-def]
    """ADR-002 (proposed) уточнит ADR-001; ADR-003 опирается на ADR-002."""
    client, entry = adr_client
    client.post(
        f"/p/{entry.name}/adr/new",
        data={"title": "Builds on 002", "status": "proposed"},
        follow_redirects=False,
    )
    _relate(entry, "ADR-002", "ADR-001", "amends")
    _relate(entry, "ADR-003", "ADR-002", "depends_on")
    r = client.get(f"/p/{entry.name}/adr/ADR-002").text
    assert "Needs a decision" in r
    assert "gets “amended by” this ADR; both stay in force" in r
    assert "depends on this ADR — it can be accepted after this one" in r
    # Полнота записи: Alternatives и Consequences пусты.
    assert '<span class="adr-record-ok">Context</span>' in r
    assert '<span class="adr-record-gap">Alternatives</span>' in r
    assert f'action="/p/{entry.name}/adr/ADR-002/accept"' in r
    assert "confirm(" not in r.split('class="adr-decide"', 1)[1].split("</section>", 1)[0]


def test_accepted_card_has_no_decision_block(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-001").text
    assert "Needs a decision" not in r
    assert f'href="/p/{entry.name}/adr/new?amends=ADR-001"' in r


def test_accept_from_card_stamps_today(adr_client) -> None:  # type: ignore[no-untyped-def]
    """Без даты в форме сервис ставит сегодняшнюю (ARG-002); повтор — 409."""
    from datetime import UTC, datetime

    client, entry = adr_client
    resp = client.post(f"/p/{entry.name}/adr/ADR-002/accept", data={}, follow_redirects=False)
    assert resp.status_code == 303
    row = _adr_row(entry, "ADR-002")
    assert row == {"status": "accepted", "decided_at": datetime.now(UTC).date()}
    again = client.post(f"/p/{entry.name}/adr/ADR-002/accept", data={}, follow_redirects=False)
    assert again.status_code == 409


def test_accept_from_card_keeps_given_date(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    resp = client.post(
        f"/p/{entry.name}/adr/ADR-002/accept",
        data={"decided_at": "2026-09-30"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert _adr_row(entry, "ADR-002")["decided_at"] == date(2026, 9, 30)


def test_reject_from_card(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    resp = client.post(
        f"/p/{entry.name}/adr/ADR-002/reject",
        data={"reason": "covered by ADR-001"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert _adr_row(entry, "ADR-002")["status"] == "rejected"


def test_accepted_card_shows_proposed_amendment(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    _relate(entry, "ADR-002", "ADR-001", "amends")
    r = client.get(f"/p/{entry.name}/adr/ADR-001").text
    assert "Proposed amendment." in r
    assert "would amend this decision" in r
    assert "amendment proposed by" in r  # сайдбар «Relations»


def test_amend_with_new_adr_records_relation(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    form = client.get(f"/p/{entry.name}/adr/new?amends=ADR-001").text
    assert '<input type="hidden" name="amends" value="ADR-001">' in form
    resp = client.post(
        f"/p/{entry.name}/adr/new",
        data={"title": "Narrower layering", "status": "proposed", "amends": "ADR-001"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    card = client.get(resp.headers["location"]).text
    assert "will amend" in card
    assert f'href="/p/{entry.name}/adr/ADR-001"' in card


def test_alternative_titles_from_live_formats() -> None:
    """Названия альтернатив из двух форм живого реестра: жирный абзац и список."""
    from cod_doc.api.web.pages.adr import _alternative_titles

    adr_016 = (
        "**A. Общая PostgreSQL (ADR-010 / RFC 23).** Отвергнуто как командный путь: нет офлайна.\n\n"
        "**B. Синхронизация файла state.db (облачная папка, rsync, S3 целиком).** Отвергнуто."
    )
    assert _alternative_titles(adr_016) == [
        "Общая PostgreSQL (ADR-010 / RFC 23)",
        "Синхронизация файла state.db (облачная папка, rsync, S3 целиком)",
    ]
    adr_009 = (
        "1. TencentDB-Agent-Memory (Tencent, TypeScript) — иерархическая память.\n\n"
        "2. Mem0 (Python, pip install mem0ai) — абстракция памяти."
    )
    assert _alternative_titles(adr_009) == [
        "TencentDB-Agent-Memory (Tencent, TypeScript)",
        "Mem0 (Python, pip install mem0ai)",
    ]
    assert _alternative_titles("Просто абзац прозы без перечисления.") == []
