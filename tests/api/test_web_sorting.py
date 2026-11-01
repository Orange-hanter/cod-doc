"""WUI-006: сортировка списков (`?sort=&dir=`) и палитра поиска (`/palette`).

Хелпер `cod_doc.api.web.sorting` проверяется напрямую, страницы — по порядку
идентификаторов в отрендеренном HTML.
"""

from __future__ import annotations

import re
import sqlite3
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from cod_doc.api.web.sorting import SortKey, SortSpec, SortState, parse
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Priority,
    ScenarioKind,
    TaskType,
)
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import (
    adr_service,
    doc_service,
    question_service,
    scenario_service,
    search_service,
    story_service,
)
from cod_doc.services import plan_service as plans
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

AUTHOR = "human:dakh"

# ── helper ───────────────────────────────────────────────────────────────

SPEC = SortSpec(
    keys={
        "id": SortKey("id", lambda r: r["id"]),
        "when": SortKey("when", lambda r: r["when"], desc_first=True),
    },
    default="id",
    tiebreak=lambda r: r["id"],
)
ROWS = [
    {"id": 3, "when": "2026-01-02"},
    {"id": 1, "when": None},
    {"id": 2, "when": "2026-01-05"},
    {"id": 4, "when": "2026-01-02"},
]


def _state(sort: str | None, direction: str | None, **params: str) -> SortState:
    key, d = parse(SPEC, sort, direction)
    return SortState(spec=SPEC, key=key, direction=d, path="/x", params=params)


def _ids(rows: list[dict[str, Any]]) -> list[int]:
    return [r["id"] for r in rows]


def test_empty_values_stay_last_in_both_directions() -> None:
    assert _ids(_state("when", "desc").apply(ROWS)) == [2, 3, 4, 1]
    assert _ids(_state("when", "asc").apply(ROWS)) == [3, 4, 2, 1]


def test_ties_keep_tiebreak_order_when_descending() -> None:
    # 3 и 4 равны по дате: тай-брейк по id по возрастанию при любом dir.
    assert _ids(_state("when", "desc").apply(ROWS))[1:3] == [3, 4]


def test_unknown_sort_and_dir_fall_back_to_defaults() -> None:
    st = _state("bogus", "sideways")
    assert (st.key, st.direction) == ("id", "asc")
    assert _ids(st.apply(ROWS)) == [1, 2, 3, 4]
    # Ключ с desc_first без dir получает своё естественное направление.
    assert _state("when", None).direction == "desc"


def test_href_toggles_active_key_and_omits_defaults() -> None:
    st = _state(None, None, group="node")
    assert st.href("id") == "/x?group=node&dir=desc"
    assert st.href("when") == "/x?group=node&sort=when"
    assert _state("when", "desc").href("when") == "/x?sort=when&dir=asc"
    assert _state("id", "desc").href("id") == "/x"
    assert st.query == ""
    assert _state("when", "asc").query == "sort=when&dir=asc"


# ── pages ────────────────────────────────────────────────────────────────


@pytest.fixture
def sort_client(tmp_path: Path, migrate_db: Any) -> Iterator[SimpleNamespace]:
    repo = tmp_path / "sort-demo"
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
    with transactional(factory) as session:
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        session.flush()
        pid = proj.row_id
        assert pid is not None
        # Ранг планов = порядок заведения: zeta раньше alpha.
        for scope in ("zeta", "alpha"):
            plans.create_plan(session, project_id=pid, scope=scope, principle=None, author=AUTHOR)
        section = plans.create_section(
            session, project_id=pid, plan_scope="zeta", letter="A", title="S", author=AUTHOR
        )
        zeta = next(p for p in plans.list_for_project(session, pid) if p.scope == "zeta")
        for task_id, title, prio, desc in (
            ("ZET-001", "beta task", Priority.LOW, None),
            ("ZET-002", "alpha task", Priority.CRITICAL, "touches <b>quokka</b> markup"),
            ("ZET-003", "gamma task", Priority.HIGH, None),
        ):
            tasks.create(
                session,
                project_id=pid,
                plan_id=zeta.row_id,
                section_id=section.row_id,
                task_id=task_id,
                title=title,
                description=desc,
                type=TaskType.FEATURE,
                priority=prio,
                author=AUTHOR,
            )
        for key, title in (("docs/b.md", "Alpha doc"), ("docs/a.md", "Bravo doc")):
            doc_service.create(
                session,
                project_id=pid,
                doc_key=key,
                type=DocumentType.GUIDE,
                status=DocumentStatus.DRAFT,
                title=title,
                author=AUTHOR,
            )
        for qid, prio in (("Q-001", Priority.LOW), ("Q-002", Priority.CRITICAL)):
            question_service.create(
                session,
                project_id=pid,
                title=f"question {qid}",
                question="why?",
                author=AUTHOR,
                priority=prio,
                question_id=qid,
            )
        for sid, prio in (("US-001", Priority.LOW), ("US-002", Priority.CRITICAL)):
            story_service.create(
                session,
                project_id=pid,
                story_id=sid,
                persona="dev",
                narrative=f"As a dev I want {sid} so that it sorts",
                priority=prio,
                author=AUTHOR,
            )
        for adr_id, title in (("ADR-001", "Zeta decision"), ("ADR-002", "Alpha decision")):
            adr_service.create(session, project_id=pid, title=title, adr_id=adr_id, author=AUTHOR)
        # Ранг сценария = порядок заведения: «zeta» раньше «alpha» в одной группе.
        scenario_ids = [
            scenario_service.create(
                session,
                project_id=pid,
                title=title,
                kind=ScenarioKind.HAPPY_PATH,
                doc_key="docs/system/capabilities/plan-management",
                preconditions="-",
                expected="-",
                steps=["do it"],
                author=AUTHOR,
            ).scenario_id
            for title in ("zeta scenario", "alpha scenario")
        ]
        search_service.reindex_all(session, pid)
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield SimpleNamespace(client=client, db_path=db_path, scenario_ids=scenario_ids)


def _order(html: str, *needles: str) -> list[str]:
    """Иглы в порядке появления в HTML (каждая обязана найтись)."""
    for n in needles:
        assert n in html, n
    return sorted(needles, key=html.index)


def test_tasks_board_sorts_inside_column(sort_client: SimpleNamespace) -> None:
    c = sort_client.client
    cards = ('id="card-ZET-001"', 'id="card-ZET-002"', 'id="card-ZET-003"')
    default = c.get("/p/demo/tasks").text
    assert _order(default, *cards) == [cards[1], cards[2], cards[0]]  # critical, high, low
    by_id = c.get("/p/demo/tasks?sort=id").text
    assert _order(by_id, *cards) == [cards[0], cards[1], cards[2]]  # 001 < 002 < 003
    by_id_desc = c.get("/p/demo/tasks?sort=id&dir=desc").text
    assert _order(by_id_desc, *cards) == [cards[2], cards[1], cards[0]]
    by_title = c.get("/p/demo/tasks?sort=title").text
    assert _order(by_title, *cards) == [cards[1], cards[0], cards[2]]


def test_tasks_view_links_keep_sort(sort_client: SimpleNamespace) -> None:
    html = sort_client.client.get("/p/demo/tasks?sort=id").text
    assert "/p/demo/tasks?view=chains&amp;sort=id" in html
    # Без плана ссылка «Board» не начинается с пустого параметра (`?&`).
    assert 'href="/p/demo/tasks?sort=id"' in html
    assert "tasks?&amp;" not in html


def test_tasks_column_count_marked_for_filter(sort_client: SimpleNamespace) -> None:
    html = sort_client.client.get("/p/demo/tasks").text
    assert 'class="kanban-col-count" data-filter-count' in html


def test_stories_sort_by_priority_and_direction(sort_client: SimpleNamespace) -> None:
    c = sort_client.client
    ids = ('data-story-id="US-001"', 'data-story-id="US-002"')
    assert _order(c.get("/p/demo/stories").text, *ids) == list(ids)
    assert _order(c.get("/p/demo/stories?sort=priority").text, *ids) == [ids[1], ids[0]]
    by_prio_desc = c.get("/p/demo/stories?sort=priority&dir=desc").text
    assert _order(by_prio_desc, *ids) == list(ids)


def test_adr_sort_by_title_inside_shelf(sort_client: SimpleNamespace) -> None:
    c = sort_client.client
    rows = ('data-adr="ADR-001"', 'data-adr="ADR-002"')
    assert _order(c.get("/p/demo/adr").text, *rows) == list(rows)
    by_title = c.get("/p/demo/adr?sort=title").text
    assert _order(by_title, *rows) == [rows[1], rows[0]]
    assert 'href="/p/demo/adr"' in by_title  # «↺ Default order»


def test_scenarios_default_rank_and_title_sort(sort_client: SimpleNamespace) -> None:
    c = sort_client.client
    first, second = (f">{sid}</a>" for sid in sort_client.scenario_ids)
    assert _order(c.get("/p/demo/scenarios").text, first, second) == [first, second]
    by_title = c.get("/p/demo/scenarios?sort=title").text
    assert _order(by_title, first, second) == [second, first]


def test_plans_default_rank_and_scope_sort(sort_client: SimpleNamespace) -> None:
    c = sort_client.client
    names = (">zeta</a>", ">alpha</a>")
    assert _order(c.get("/p/demo/plans").text, *names) == list(names)
    by_scope = c.get("/p/demo/plans?sort=scope").text
    assert _order(by_scope, *names) == [names[1], names[0]]
    # Активный заголовок предлагает обратное направление.
    assert 'href="/p/demo/plans?sort=scope&amp;dir=desc"' in by_scope


def test_docs_sort_by_title_survives_drift_reload(sort_client: SimpleNamespace) -> None:
    c = sort_client.client
    keys = (">docs/a.md<", ">docs/b.md<")
    assert _order(c.get("/p/demo/docs").text, *keys) == list(keys)
    page = c.get("/p/demo/docs?sort=title").text
    assert _order(page, *keys) == [keys[1], keys[0]]  # «Alpha doc» лежит в b.md
    assert "drift-marks?group=node&sort=title" in page
    frag = c.get("/p/demo/docs/drift-marks?group=node&sort=title").text
    assert _order(frag, *keys) == [keys[1], keys[0]]
    # Ссылки заголовков фрагмента ведут на страницу, а не на сам фрагмент.
    assert 'href="/p/demo/docs?group=node&amp;sort=title&amp;dir=desc"' in frag
    assert "drift-marks?group=node&amp;sort=type" not in frag


def test_docs_reset_link_and_node_chips_keep_sort(sort_client: SimpleNamespace) -> None:
    html = sort_client.client.get("/p/demo/docs?sort=title").text
    assert "Default order" in html
    assert "group=node&amp;node=" in html
    chip_hrefs = re.findall(r'class="docs-node-chip[^"]*" href="([^"]*)"', html)
    assert chip_hrefs, "no node chips rendered"
    assert all("sort=title" in h for h in chip_hrefs)
    assert "Default order" not in sort_client.client.get("/p/demo/docs").text


def test_questions_sort_by_id_desc(sort_client: SimpleNamespace) -> None:
    c = sort_client.client
    rows = (">Q-001</a>", ">Q-002</a>")
    assert _order(c.get("/p/demo/questions").text, *rows) == [rows[1], rows[0]]  # priority
    assert _order(c.get("/p/demo/questions?sort=id").text, *rows) == list(rows)
    assert _order(c.get("/p/demo/questions?sort=id&dir=desc").text, *rows) == [rows[1], rows[0]]


def test_unknown_sort_renders_default_not_error(sort_client: SimpleNamespace) -> None:
    for path in ("tasks", "docs", "plans", "stories", "adr", "questions", "scenarios"):
        r = sort_client.client.get(f"/p/demo/{path}?sort=nope&dir=up")
        assert r.status_code == 200, path


# ── palette ──────────────────────────────────────────────────────────────


def test_palette_finds_entities_and_escapes_snippet(sort_client: SimpleNamespace) -> None:
    html = sort_client.client.get("/palette?project=demo&q=quokka").text
    assert 'href="/p/demo/tasks/ZET-002"' in html
    assert "<mark>quokka</mark>" in html
    assert "<b>" not in html  # сырой HTML из описания задачи экранирован
    assert 'href="/p/demo/search?q=quokka"' in html


def test_search_page_escapes_snippet(sort_client: SimpleNamespace) -> None:
    """Сниппет FTS несёт сырой текст: HTML из описания задачи не исполняется."""
    html = sort_client.client.get("/p/demo/search?q=quokka").text
    assert "<mark>quokka</mark>" in html
    assert "<b>" not in html
    assert "&lt;b&gt;" in html


def test_palette_marks_active_option_for_screen_readers(sort_client: SimpleNamespace) -> None:
    html = sort_client.client.get("/p/demo/tasks").text
    assert 'role="combobox"' in html
    assert 'class="cmdk-mod"' in html


def test_palette_drops_hits_without_page(
    sort_client: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Хит вида без страницы не рендерится ссылкой `href="None"`."""
    import cod_doc.api.web.pages.palette as palette

    monkeypatch.setattr(palette, "hit_url", lambda slug, kind, ref: None)
    html = sort_client.client.get("/palette?project=demo&q=quokka").text
    assert 'href="None"' not in html
    assert "ZET-002" not in html


def test_palette_empty_query_offers_tabs(sort_client: SimpleNamespace) -> None:
    html = sort_client.client.get("/palette?project=demo&q=").text
    assert 'href="/p/demo/plans"' in html
    assert 'href="/p/demo/docs"' in html
    filtered = sort_client.client.get("/palette?project=demo&q=plan").text
    assert 'href="/p/demo/plans"' in filtered
    assert 'href="/p/demo/docs"' not in filtered


def test_palette_outside_project_lists_projects(sort_client: SimpleNamespace) -> None:
    html = sort_client.client.get("/palette?q=dem").text
    assert 'href="/p/demo"' in html
    assert "Nothing found" in sort_client.client.get("/palette?q=zzz").text


def test_palette_without_index_shows_hint(sort_client: SimpleNamespace) -> None:
    with sqlite3.connect(sort_client.db_path) as conn:
        conn.execute("DROP TABLE db_search_idx")
    r = sort_client.client.get("/palette?project=demo&q=alpha")
    assert r.status_code == 200
    assert 'href="/p/demo/search"' in r.text


def test_every_page_carries_palette_shell(sort_client: SimpleNamespace) -> None:
    for path in ("/", "/p/demo/tasks", "/settings"):
        html = sort_client.client.get(path).text
        assert 'id="cmdk"' in html, path
        assert "cod_doc_palette.js" in html, path
