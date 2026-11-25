"""WEB-010: tasks list page through TaskService over the embedded sqlite DB."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cod_doc.api.web.pages.tasks import BOARD_PAGE_ROWS, BOARD_VISIBLE_ROWS
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
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
from cod_doc.services.project_lookup import require_project_id
from tests._task_scope import sole_project_id


@pytest.fixture
def tasks_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "demo-tasks"
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
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        plan = PlanRepository(session).add(
            Plan(project_id=proj.row_id, scope="auth-bootstrap", principle="test-first")
        )
        plan.created = now
        plan.last_updated = now
        session.flush()

        section = PlanSectionRepository(session).add(
            PlanSection(
                plan_id=plan.row_id,
                letter="A",
                title="Test Coverage",
                slug="test-coverage",
                position=0,
            )
        )
        session.flush()

        # Three tasks: one pending, one in-progress, one done.
        tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Test: getMyAgency returns profile",
            type=TaskType.TEST,
            priority=Priority.HIGH,
            author="human:dakh",
            id_prefix="AUTH",
        )
        t2 = tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Implement: account deactivation flow",
            type=TaskType.FEATURE,
            priority=Priority.CRITICAL,
            author="human:dakh",
            id_prefix="AUTH",
        )
        t3 = tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Refactor: token rotation",
            type=TaskType.REFACTOR,
            priority=Priority.LOW,
            author="human:dakh",
            id_prefix="AUTH",
        )
        tasks.update_status(
            session,
            task_id=t2.task_id,
            new_status=TaskStatus.IN_PROGRESS,
            author="human:dakh",
            via_checkout=True,
            project_id=sole_project_id(session),
        )
        tasks.complete(
            session, task_id=t3.task_id, author="human:dakh", project_id=sole_project_id(session)
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_tasks_list_renders_all(tasks_client) -> None:
    """Default landing page shows every task on the kanban board."""
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200
    # All three tasks present, each as a kanban card with anchor id.
    assert 'id="card-AUTH-001"' in r.text
    assert 'id="card-AUTH-002"' in r.text
    assert 'id="card-AUTH-003"' in r.text
    assert "Implement: account deactivation flow" in r.text
    # Status groups rendered (status = group), WUI-013.
    assert 'data-status-group="todo"' in r.text
    assert 'data-status-group="in_progress"' in r.text
    assert 'data-status-group="done"' in r.text
    # Пустые группы не рендерятся.
    assert 'data-status-group="blocked"' not in r.text
    # Row carries a status tag; ADO-156: он собирается из хранимого канона.
    assert 'l-tag-neutral">todo<' in r.text
    assert 'l-tag-signal">in_progress<' in r.text
    assert 'l-tag-ok">done<' in r.text
    # tab strip: Tasks active
    assert 'class="active" href="/p/demo/tasks"' in r.text
    # count footer
    assert "Показано 3 из 3" in r.text


def test_tasks_list_done_column_collapsed_by_default(tasks_client) -> None:
    """Done/Cancelled lanes are pre-collapsed so the eye lands on open work."""
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200

    # Find the Done column markup and confirm it has no `open` attribute.
    # Other columns with tasks should be open.
    done_block = re.search(
        r'<details class="l-group[^"]*"([^>]*data-status-group="done"[^>]*)>', r.text
    )
    assert done_block is not None, "Done column must render"
    assert "open" not in done_block.group(1), "Done column should be collapsed by default"

    in_progress_block = re.search(
        r'<details class="l-group[^"]*"([^>]*data-status-group="in_progress"[^>]*)>', r.text
    )
    assert in_progress_block is not None
    assert "open" in in_progress_block.group(1), "Non-empty active lane should start open"


def test_tasks_list_status_highlights_column(tasks_client) -> None:
    """`?status=done` highlights the Done column but keeps every task visible."""
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks?status=done")
    assert r.status_code == 200
    # Highlight applied to the Done group.
    assert re.search(
        r'<details class="l-group l-group-highlighted"[^>]*data-status-group="done"', r.text
    )
    # Other tasks are still on the board (kanban shows everything).
    assert 'id="card-AUTH-001"' in r.text
    assert 'id="card-AUTH-003"' in r.text


def test_tasks_list_status_in_progress_highlights_column(tasks_client) -> None:

    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks?status=in-progress")
    assert r.status_code == 200
    assert re.search(
        r'<details class="l-group l-group-highlighted"[^>]*data-status-group="in_progress"',
        r.text,
    )
    assert 'id="card-AUTH-002"' in r.text


def test_tasks_list_plan_filter(tasks_client) -> None:
    """`?plan=<scope>` restricts the board to one plan; unknown plan → empty."""
    client, entry = tasks_client
    # All three demo tasks belong to plan `auth-bootstrap` — they remain visible.
    r = client.get(f"/p/{entry.name}/tasks?plan=auth-bootstrap")
    assert r.status_code == 200
    assert "l-filter-active" in r.text
    assert 'id="card-AUTH-001"' in r.text
    # Unknown plan → no tasks shown.
    r2 = client.get(f"/p/{entry.name}/tasks?plan=does-not-exist")
    assert r2.status_code == 200
    assert "AUTH-001" not in r2.text


def test_tasks_list_chains_view(tasks_client) -> None:
    """`?view=chains` renders the dependency-graph layout per plan."""
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks?view=chains")
    assert r.status_code == 200
    # View toggle present and chains tab active.
    assert "tasks-view-toggle" in r.text
    assert "tasks-view-btn-active" in r.text
    # Chain section for the demo plan rendered.
    assert 'id="chain-auth-bootstrap"' in r.text
    assert "chain-lane" in r.text
    # All tasks are nodes on the chain board.
    assert 'id="card-AUTH-001"' in r.text
    assert 'id="card-AUTH-002"' in r.text
    assert 'id="card-AUTH-003"' in r.text
    # With no dependency edges set, the "no edges" notice is shown.
    assert "нет зависимостей" in r.text


def test_tasks_list_invalid_status_warns(tasks_client) -> None:
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks?status=garbage&view=all")
    assert r.status_code == 200
    assert "Неизвестное значение status" in r.text
    # falls back to the requested view — `view=all` shows every task.
    assert "AUTH-001" in r.text
    assert "AUTH-002" in r.text
    assert "AUTH-003" in r.text


def test_tasks_list_warns_when_db_absent(tmp_path: Path) -> None:
    repo = tmp_path / "no-db-tasks"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))

    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200
    assert "DB-проект не инициализирован" in r.text


def test_tasks_list_404_unknown_project(tasks_client) -> None:
    client, _ = tasks_client
    r = client.get("/p/nope/tasks")
    assert r.status_code == 404


# ── WEB-011: HTMX inline status update ──────────────────────────────────────


def test_status_post_htmx_returns_row_fragment(tasks_client) -> None:
    client, entry = tasks_client
    r = client.post(
        f"/p/{entry.name}/tasks/AUTH-001/status",
        data={"status": "in-progress"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # Row id present, swap-friendly
    assert 'id="task-AUTH-001"' in r.text
    # ADO-156: легаси-написание в форме принято (см. data= выше), но
    # отрисовано канонически — записан в БД именно канон.
    assert "badge-in_progress" in r.text
    assert '<option value="in_progress" selected>' in r.text
    # No row-error span when success
    assert "row-error" not in r.text


def test_status_post_form_redirects_back_to_list(tasks_client) -> None:
    client, entry = tasks_client
    r = client.post(
        f"/p/{entry.name}/tasks/AUTH-001/status",
        data={"status": "in-progress"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/p/{entry.name}/tasks"


def test_status_post_no_op_same_status(tasks_client) -> None:
    """Posting the same status as current → service no-op, returns row unchanged."""
    client, entry = tasks_client
    r = client.post(
        f"/p/{entry.name}/tasks/AUTH-001/status",
        data={"status": "pending"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    # `pending` на входе = тот же бакет, что уже стоит: сервис ничего не пишет,
    # а бейдж показывает хранимое каноническое написание.
    assert "badge-todo" in r.text


def test_status_post_unknown_task_404(tasks_client) -> None:
    client, entry = tasks_client
    r = client.post(
        f"/p/{entry.name}/tasks/NO-999/status",
        data={"status": "done"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


def test_status_post_invalid_status_value_400(tasks_client) -> None:
    client, entry = tasks_client
    r = client.post(
        f"/p/{entry.name}/tasks/AUTH-001/status",
        data={"status": "garbage"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 400


def test_status_post_404_unknown_project(tasks_client) -> None:
    client, _ = tasks_client
    r = client.post(
        "/p/nope/tasks/AUTH-001/status",
        data={"status": "done"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


def test_status_post_404_db_absent(tmp_path: Path) -> None:
    repo = tmp_path / "no-db-post"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))

    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.post(
            f"/p/{entry.name}/tasks/X-001/status",
            data={"status": "done"},
            headers={"HX-Request": "true"},
        )
    assert r.status_code == 404


def test_status_post_persists_change(tasks_client) -> None:
    """After a HTMX post, GET /tasks shows the updated status."""
    client, entry = tasks_client
    client.post(
        f"/p/{entry.name}/tasks/AUTH-001/status",
        data={"status": "done"},
        headers={"HX-Request": "true"},
    )
    r = client.get(f"/p/{entry.name}/tasks?status=done")
    assert r.status_code == 200
    assert "AUTH-001" in r.text  # now also done
    assert "AUTH-003" in r.text  # was already done


# ── ADO-156: счётчики в шапке цепочек и словарь статусов в форме ─────────────


def test_chains_header_counts_done_tasks(tasks_client) -> None:
    """Шапка цепочки несёт счётчик `done`, а `Ready` подписан как «к старту».

    Без счётчика закрытых единственным числом со смыслом статуса в строке был
    `Ready: 0`, и на полностью закрытом плане он читался как «план не закрыт».
    """
    client, entry = tasks_client
    body = client.get(f"/p/{entry.name}/tasks?view=chains").text
    # Одна из трёх задач фикстуры закрыта.
    assert "chain-count-done" in body
    assert "<strong>1</strong> done" in body
    # Готова к старту одна — задача в `todo` без пререквизитов. ADO-078:
    # критерий тот же, что у view `ready_tasks`; задача в `in_progress` уже
    # начата и к старту не готова (прежний предикат «не done» считал обе).
    assert "Ready to start: <strong>1</strong>" in body
    # Голого «Ready:» на странице не осталось — метка врала о смысле числа.
    assert "Ready:" not in body


def test_status_select_offers_no_legacy_spelling(tasks_client) -> None:
    """HTMX-фрагмент строки предлагает канонические семь, без `pending`."""
    client, entry = tasks_client
    # Задача в каноническом статусе: легаси-пункту взяться в списке неоткуда.
    r = client.post(
        f"/p/{entry.name}/tasks/AUTH-001/status",
        data={"status": "done"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert '<option value="done" selected>' in r.text
    assert '<option value="todo"' in r.text
    assert '<option value="pending"' not in r.text
    assert '<option value="in-progress"' not in r.text


# ── Вложенность ссылок в карточке цепочки ───────────────────────────────────


class _AnchorNesting(HTMLParser):
    """Ищет `<a>` внутри `<a>`: парсер браузера такую пару не сохраняет.

    По adoption agency внешняя ссылка закрывается на первой внутренней, и всё,
    что шло после чипа, становится соседом пустого огрызка. В тексте ответа
    ошибка видна только как вложенность — DOM её уже «починил».
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.nested: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        if self.depth:
            self.nested.append(dict(attrs))
        self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.depth:
            self.depth -= 1


def test_chain_cards_have_no_nested_anchors(tasks_client) -> None:
    """Карточка цепочки с чипами ↑/↓ не вкладывает одну ссылку в другую.

    Карточка была `<a>`, а чипы пререквизитов и зависимостей внутри неё —
    тоже `<a>`. Браузер закрывал внешнюю ссылку на первом чипе: в ленте
    оставался пустой прямоугольник `chain-card`, а тело, мета и чипы
    вываливались соседями в `.chain-lane-body` — без рамки, без приоритетной
    полосы и без клика по карточке.
    """
    client, entry = tasks_client

    # Фикстура идёт без рёбер; чипы появляются только у связанных задач.
    db_path = Path(entry.path) / ".cod-doc" / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        anchor_task = tasks.get(
            session, task_id="AUTH-001", project_id=require_project_id(session, entry.name)
        )
        tasks.create(
            session,
            project_id=anchor_task.project_id,
            plan_id=anchor_task.plan_id,
            section_id=anchor_task.section_id,
            title="Implement: follow-up blocked by AUTH-001",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:dakh",
            id_prefix="AUTH",
            blocked_by=["AUTH-001"],
        )
    engine.dispose()

    body = client.get(f"/p/{entry.name}/tasks?view=chains").text
    # Ребро действительно доехало до разметки — иначе тест зеленел бы впустую.
    assert "chain-card-prereqs" in body
    assert "chain-card-deps" in body

    parser = _AnchorNesting()
    parser.feed(body)
    assert parser.nested == [], f"вложенные <a>: {parser.nested}"


# ── WUI-013: доска не рендерит бакет целиком ────────────────────────────────

_ROW = re.compile(r'<article class="l-row"[^>]*\bid="card-([^"]+)"')


def _seed_todo(entry: ProjectEntry, extra: int, other_plan: int = 0) -> None:
    """Довести `todo` до `1 + extra` задач в auth-bootstrap и `other_plan` — в плане `side`."""
    db_path = Path(entry.path) / ".cod-doc" / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    with transactional(make_session_factory(engine)) as session:
        anchor = tasks.get(
            session, task_id="AUTH-001", project_id=require_project_id(session, entry.name)
        )
        side_plan_id = side_section_id = None
        if other_plan:
            side = PlanRepository(session).add(
                Plan(project_id=anchor.project_id, scope="side", principle="x")
            )
            session.flush()
            side_plan_id = side.row_id
            side_section = PlanSectionRepository(session).add(
                PlanSection(plan_id=side_plan_id, letter="S", title="Side", slug="side", position=0)
            )
            session.flush()
            side_section_id = side_section.row_id
        for i in range(extra):
            tasks.create(
                session,
                project_id=anchor.project_id,
                plan_id=anchor.plan_id,
                section_id=anchor.section_id,
                title=f"Bulk task {i}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:dakh",
                id_prefix="AUTH",
            )
        for i in range(other_plan):
            tasks.create(
                session,
                project_id=anchor.project_id,
                plan_id=side_plan_id,
                section_id=side_section_id,
                title=f"Side task {i}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:dakh",
                id_prefix="SIDE",
            )
    engine.dispose()


def _group_block(html: str, key: str) -> str:
    start = html.index(f'data-status-group="{key}"')
    end = html.find("</details>", start)
    return html[start:end]


def test_board_renders_only_first_rows_of_big_group(tasks_client) -> None:
    """В бакете 60 задач: страница несёт 25 строк и кнопку «Показать ещё 35 из 60»."""
    client, entry = tasks_client
    _seed_todo(entry, extra=59)
    body = client.get(f"/p/{entry.name}/tasks").text
    todo = _group_block(body, "todo")
    assert len(_ROW.findall(todo)) == BOARD_VISIBLE_ROWS == 25
    assert "Показать ещё 35 из 60" in todo
    assert "/tasks/rows?group=todo&amp;offset=25" in todo
    assert 'hx-swap="outerHTML"' in todo
    # Счётчик группы — полный размер бакета, а не число отрендеренных строк.
    assert '<span class="l-group-count" data-filter-count>60</span>' in todo
    assert "Показано 27 из 62" in body


def test_board_rows_endpoint_continues_page_order(tasks_client) -> None:
    """`/tasks/rows` продолжает очередь страницы: те же порядок и сортировка."""
    client, entry = tasks_client
    _seed_todo(entry, extra=59)
    base = f"/p/{entry.name}/tasks"
    page_ids = _ROW.findall(_group_block(client.get(f"{base}?sort=id").text, "todo"))
    rest = client.get(f"{base}/rows?group=todo&offset=25&sort=id")
    assert rest.status_code == 200
    rest_ids = _ROW.findall(rest.text)
    assert len(page_ids) == 25
    assert [*page_ids, *rest_ids] == [f"AUTH-{n:03d}" for n in range(1, 63) if n not in (2, 3)]
    assert "l-more-btn" not in rest.text  # всё догружено

    # offset=0 забирает весь бакет разом (60 < BOARD_PAGE_ROWS): кнопки нет.
    first = client.get(f"{base}/rows?group=todo&offset=0&sort=id")
    assert len(_ROW.findall(first.text)) == 60
    assert "Показать ещё" not in first.text


def test_board_rows_endpoint_pages_by_page_size(tasks_client) -> None:
    client, entry = tasks_client
    _seed_todo(entry, extra=BOARD_PAGE_ROWS + 29)  # todo = PAGE + 30
    r = client.get(f"/p/{entry.name}/tasks/rows?group=todo&offset=0&sort=id&dir=desc")
    assert len(_ROW.findall(r.text)) == BOARD_PAGE_ROWS
    assert f"/tasks/rows?group=todo&amp;offset={BOARD_PAGE_ROWS}" in r.text
    assert "sort=id&amp;dir=desc" in r.text
    assert f"Показать ещё 30 из {BOARD_PAGE_ROWS + 30}" in r.text
    # offset < 0 приводится к 0.
    neg = client.get(f"/p/{entry.name}/tasks/rows?group=todo&offset=-5&sort=id&dir=desc")
    assert _ROW.findall(neg.text) == _ROW.findall(r.text)


def test_board_rows_endpoint_respects_plan_filter(tasks_client) -> None:
    client, entry = tasks_client
    _seed_todo(entry, extra=30, other_plan=3)
    base = f"/p/{entry.name}/tasks/rows?group=todo&offset=0"
    side = _ROW.findall(client.get(f"{base}&plan=side").text)
    assert len(side) == 3
    assert all(i.startswith("SIDE") for i in side)
    main = _ROW.findall(client.get(f"{base}&plan=auth-bootstrap").text)
    assert len(main) == 31
    assert not any(i.startswith("SIDE") for i in main)
    # Кнопка страницы с фильтром плана переносит его в догрузку.
    page = client.get(f"/p/{entry.name}/tasks?plan=auth-bootstrap").text
    assert "Показать ещё 6 из 31" in page
    assert "plan=auth-bootstrap" in _group_block(page, "todo")


def test_board_rows_endpoint_unknown_group_404(tasks_client) -> None:
    client, entry = tasks_client
    assert client.get(f"/p/{entry.name}/tasks/rows?group=nope").status_code == 404
    assert client.get("/p/nope/tasks/rows?group=todo").status_code == 404


_HIDE_BOX = re.compile(r'<input type="checkbox" name="hide_closed" value="1"( checked)?')


def test_board_hide_closed_drops_closed_group(tasks_client) -> None:
    client, entry = tasks_client
    plain = client.get(f"/p/{entry.name}/tasks").text
    box = _HIDE_BOX.search(plain)
    assert box is not None
    assert box.group(1) is None
    assert "Скрыть закрытые (1)" in plain
    assert 'data-status-group="done"' in plain

    hidden = client.get(f"/p/{entry.name}/tasks?hide_closed=1").text
    box = _HIDE_BOX.search(hidden)
    assert box is not None
    assert box.group(1) == " checked"
    assert 'data-status-group="done"' not in hidden
    assert 'id="card-AUTH-003"' not in hidden
    assert 'id="card-AUTH-001"' in hidden
    assert "Показано 2 из 2 · скрыто закрытых: 1" in hidden
    # Счётчик шапки по-прежнему считает все задачи.
    assert "3 задач" in hidden


def test_hide_closed_survives_other_controls(tasks_client) -> None:
    """Сортировка, вид и план не сбрасывают чекбокс, чекбокс не сбрасывает их."""
    client, entry = tasks_client
    url = f"/p/{entry.name}/tasks?plan=auth-bootstrap&sort=title&status=blocked&hide_closed=1"
    body = client.get(url).text
    for name, value in (("plan", "auth-bootstrap"), ("sort", "title"), ("status", "blocked")):
        assert f'<input type="hidden" name="{name}" value="{value}">' in body
    assert '<input type="hidden" name="hide_closed"' not in body
    chains_href = re.search(r'href="([^"]*view=chains[^"]*)"', body)
    assert chains_href is not None
    assert "hide_closed=1" in chains_href.group(1)
    sort_href = re.search(r'href="([^"]*sort=updated[^"]*)"', body)
    assert sort_href is not None
    assert "hide_closed=1" in sort_href.group(1)


def test_chains_hide_closed_drops_cards_and_dangling_refs(tasks_client) -> None:
    client, entry = tasks_client
    factory = make_session_factory(make_engine(f"sqlite:///{entry.path}/.cod-doc/state.db"))
    with transactional(factory) as session:
        tasks.add_dependency(
            session,
            task_id="AUTH-001",
            blocker_task_id="AUTH-003",
            note="needs token rotation",
            author="human:test",
            project_id=sole_project_id(session),
        )
    full = client.get(f"/p/{entry.name}/tasks?view=chains").text
    assert 'id="card-AUTH-003"' in full
    assert "#card-AUTH-003" in full

    hidden = client.get(f"/p/{entry.name}/tasks?view=chains&hide_closed=1").text
    assert 'id="card-AUTH-003"' not in hidden
    assert "#card-AUTH-003" not in hidden
    assert 'id="card-AUTH-001"' in hidden
    # Шапка цепочки считает закрытые, пустых колонок нет.
    assert "<strong>1</strong> done" in hidden
    lanes = hidden.count('class="chain-lane"')
    assert f"repeat({lanes}," in hidden


def test_hide_closed_when_everything_is_closed(tasks_client) -> None:
    client, entry = tasks_client
    factory = make_session_factory(make_engine(f"sqlite:///{entry.path}/.cod-doc/state.db"))
    with transactional(factory) as session:
        for task_id in ("AUTH-001", "AUTH-002"):
            tasks.complete(
                session,
                task_id=task_id,
                author="human:test",
                project_id=sole_project_id(session),
            )
    for view in ("board", "chains"):
        body = client.get(f"/p/{entry.name}/tasks?view={view}&hide_closed=1").text
        assert "Открытых задач нет, закрытые скрыты: 3." in body
        assert 'id="card-' not in body
