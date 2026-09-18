"""ADO-115: реплей прогона показывает шаги, а не «run упал».

До этой правки `grep run_detail tests/` был пуст — страница реплея не была
покрыта ничем, и именно поэтому пустая лента прожила так долго: на живом
WebSocket всё видно, а на перезагрузке нет, и никто не проверял.

Здесь и acceptance-пункты ADO-115 («replay показывает thinking/tool/error»,
«события переживают reload», «empty-state не утверждает, что run упал»), и
две вещи, которых в acceptance нет, но без которых реплей врёт: счётчики
фильтров и потолок в 500 строк.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.api.server import app
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import AgentRunModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import run_context

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

SLUG = "runner"


@pytest.fixture
def run_client(tmp_path: Path, migrate_db) -> Iterator[tuple[TestClient, Path]]:  # type: ignore[no-untyped-def]
    repo = tmp_path / "run-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name=SLUG, path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)
    Project(entry).init()

    engine = make_engine(f"sqlite:///{db_path}")
    try:
        with transactional(make_session_factory(engine)) as session:
            now = datetime.now(UTC)
            proj = ProjectRepository(session).add(
                ProjectEntity(slug=SLUG, title="Runner", root_path=str(repo), config={})
            )
            proj.created = now
            proj.updated = now
    finally:
        engine.dispose()

    with TestClient(app) as client:
        yield client, repo


def _add_run(repo: Path, run_id: str, status: str = "done") -> None:
    engine = make_engine(f"sqlite:///{repo / '.cod-doc' / 'state.db'}")
    try:
        with transactional(make_session_factory(engine)) as session:
            proj = ProjectRepository(session).get_by_slug(SLUG)
            assert proj is not None
            session.add(
                AgentRunModel(
                    run_id=run_id,
                    project_id=proj.row_id,
                    status=status,
                    wake_reason="test",
                )
            )
    finally:
        engine.dispose()


def _record(repo: Path, run_id: str, steps: list[tuple[str, object]]) -> None:
    handle = run_context.start_orchestrator_run(project_path=str(repo), run_id=run_id)
    try:
        for kind, data in steps:
            run_context.record_step(kind, data)
    finally:
        run_context.finalize_orchestrator_run(handle, project_path=str(repo), run_id=run_id)


# ── acceptance ────────────────────────────────────────────────────────


def test_unknown_run_is_404(run_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = run_client
    assert client.get(f"/p/{SLUG}/run/01JNOPE0000000000000000000").status_code == 404


def test_replay_shows_steps_oldest_first(run_client) -> None:  # type: ignore[no-untyped-def]
    """Первый пункт acceptance: реплей показывает thinking/tool/error, не ∅."""
    client, repo = run_client
    run_id = "01JREPLAY000000000000000A"
    _add_run(repo, run_id)
    _record(
        repo,
        run_id,
        [
            ("agent.thinking", {"data": "первый шаг"}),
            ("agent.tool_call", {"data": "task_get"}),
            ("agent.error", {"data": "упс"}),
        ],
    )

    html = client.get(f"/p/{SLUG}/run/{run_id}").text
    assert "kind-thinking" in html
    assert 'data-kind="tool_call"' in html
    assert "kind-error" in html
    # Порядок — старые сверху.
    assert html.index("первый шаг") < html.index("task_get") < html.index("упс")


def test_replay_survives_a_second_request(run_client) -> None:  # type: ignore[no-untyped-def]
    """«События переживают reload» — второй GET отдаёт то же самое."""
    client, repo = run_client
    run_id = "01JREPLAY000000000000000B"
    _add_run(repo, run_id)
    _record(repo, run_id, [("agent.thinking", {"data": "устойчивый"})])

    first = client.get(f"/p/{SLUG}/run/{run_id}").text
    second = client.get(f"/p/{SLUG}/run/{run_id}").text
    assert "устойчивый" in first
    assert "устойчивый" in second


def test_counters_come_from_the_server(run_client) -> None:  # type: ignore[no-untyped-def]
    """Захардкоженные нули в шаблоне делали реплей пустым на вид.

    Три thinking, два tool_call, один error → в разметке 3/2/1. Считается
    `tool_call`, а не сумма call+result: иначе число не сойдётся с тем, что
    покажет одноимённый фильтр.
    """
    client, repo = run_client
    run_id = "01JREPLAY000000000000000C"
    _add_run(repo, run_id)
    _record(
        repo,
        run_id,
        [("agent.thinking", {"data": f"t{i}"}) for i in range(3)]
        + [("agent.tool_call", {"data": "a"}), ("agent.tool_result", {"data": "ra"})]
        + [("agent.tool_call", {"data": "b"}), ("agent.tool_result", {"data": "rb"})]
        + [("agent.error", {"data": "boom"})],
    )

    html = client.get(f"/p/{SLUG}/run/{run_id}").text
    assert 'data-kind="thinking" aria-pressed="false">Thinking <span class="count">3</span>' in html
    assert 'data-kind="tool_call" aria-pressed="false">Tool <span class="count">2</span>' in html
    assert 'data-kind="error" aria-pressed="false">Error <span class="count">1</span>' in html


def test_live_page_counters_start_at_zero(run_client) -> None:  # type: ignore[no-untyped-def]
    """На живой консоли `event_counts` пуст — рендерится 0, дальше ведёт JS."""
    client, _ = run_client
    html = client.get(f"/p/{SLUG}/run").text
    assert 'Thinking <span class="count">0</span>' in html


def test_empty_state_does_not_blame_write_tools(run_client) -> None:  # type: ignore[no-untyped-def]
    """Четвёртый пункт acceptance.

    Все прогоны, созданные до ADO-115, останутся с этим экраном навсегда,
    поэтому он обязан говорить правду, а не «run упал до первого write-tool».
    """
    client, repo = run_client
    run_id = "01JREPLAY000000000000000D"
    _add_run(repo, run_id)

    html = client.get(f"/p/{SLUG}/run/{run_id}").text
    assert "write-tool" not in html
    assert "Шаги этого прогона не записаны." in html


def test_failed_run_empty_state_mentions_the_failure(run_client) -> None:  # type: ignore[no-untyped-def]
    """Упавший прогон без шагов — единственный случай, когда про падение уместно."""
    client, repo = run_client
    run_id = "01JREPLAY000000000000000E"
    _add_run(repo, run_id, status="failed")

    html = client.get(f"/p/{SLUG}/run/{run_id}").text
    assert "завершился с ошибкой" in html


def test_replay_renders_at_most_the_query_limit(run_client) -> None:  # type: ignore[no-untyped-def]
    """Страница просит 500 — столько же пишется в БД, значит потолок совпадает."""
    client, repo = run_client
    run_id = "01JREPLAY000000000000000F"
    _add_run(repo, run_id)
    _record(
        repo,
        run_id,
        [("agent.thinking", {"data": f"s{i}"}) for i in range(run_context.MAX_STEPS_PER_RUN + 100)],
    )

    html = client.get(f"/p/{SLUG}/run/{run_id}").text
    assert html.count("kind-thinking") == run_context.MAX_STEPS_PER_RUN


# ── полный текст шага ─────────────────────────────────────────────────


def test_step_route_returns_the_full_body(run_client) -> None:  # type: ignore[no-untyped-def]
    """Обрезка в БД не должна означать потерю деталей."""
    client, repo = run_client
    run_id = "01JREPLAY000000000000000G"
    _add_run(repo, run_id)
    body = "я" * 50_000
    _record(repo, run_id, [("agent.tool_result", {"data": body})])

    r = client.get(f"/p/{SLUG}/run/{run_id}/step/0")
    assert r.status_code == 200
    assert r.json()["data"]["data"] == body

    # А в самой ленте — обрезано.
    html = client.get(f"/p/{SLUG}/run/{run_id}").text
    assert body not in html


def test_step_route_404s_on_unknown_run(run_client) -> None:  # type: ignore[no-untyped-def]
    client, _ = run_client
    assert client.get(f"/p/{SLUG}/run/01JNOPE0000000000000000000/step/0").status_code == 404


def test_step_route_404s_on_unrecorded_step(run_client) -> None:  # type: ignore[no-untyped-def]
    """Прогон старше ADO-115: строки есть, sidecar нет — это не ошибка."""
    client, repo = run_client
    run_id = "01JREPLAY000000000000000H"
    _add_run(repo, run_id)
    assert client.get(f"/p/{SLUG}/run/{run_id}/step/7").status_code == 404


@pytest.mark.parametrize("evil", ["..", "../etc", "%2e%2e%2fetc%2fpasswd"])
def test_step_route_rejects_path_traversal(run_client, evil: str) -> None:  # type: ignore[no-untyped-def]
    """`run_id` становится именем файла — путь наружу не должен открываться."""
    client, _ = run_client
    r = client.get(f"/p/{SLUG}/run/{evil}/step/0")
    assert r.status_code in (404, 400), r.status_code


# ── вёрстка строки шага ───────────────────────────────────────────────


def test_step_body_has_no_leading_template_whitespace(run_client) -> None:  # type: ignore[no-untyped-def]
    """У `.body` стоит `white-space: pre-wrap` — отступ шаблона попадает В ТЕКСТ.

    Без управления пробелами (`{%- ... -%}`) каждый шаг начинался с 19
    пробелов, и первая строка уезжала вправо примерно на 130px относительно
    переноса. Поймано показом страницы в браузере: до ADO-115 лента всегда
    была пуста, и увидеть это было негде.
    """
    client, repo = run_client
    run_id = "01JREPLAY000000000000000I"
    _add_run(repo, run_id)
    _record(repo, run_id, [("agent.thinking", {"data": "без отступа"})])

    html = client.get(f"/p/{SLUG}/run/{run_id}").text
    assert '<span class="body">без отступа</span>' in html, (
        "в тело шага просочился отступ шаблона — при `pre-wrap` это видно на экране"
    )


def test_started_and_stopped_rows_are_not_blank(run_client) -> None:  # type: ignore[no-untyped-def]
    """У started/stopped нет поля `data`, но им есть что показать.

    Без веток на `title` и `reason` самая информативная строка ленты — какая
    задача началась — показывала прочерк.
    """
    client, repo = run_client
    run_id = "01JREPLAY000000000000000J"
    _add_run(repo, run_id)
    _record(
        repo,
        run_id,
        [
            ("agent.started", {"task_id": "ADO-115", "title": "Реплей консоли"}),
            ("agent.stopped", {"task_id": "ADO-115", "reason": "completed"}),
        ],
    )

    html = client.get(f"/p/{SLUG}/run/{run_id}").text
    assert '<span class="body">Реплей консоли</span>' in html
    assert '<span class="body">completed</span>' in html
