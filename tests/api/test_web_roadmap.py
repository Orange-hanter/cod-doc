"""ROAD-007/008, ADO-137 (RFC 36 §3.5): экран дорожной карты и Гант на странице плана.

Тесты разметки, не вёрстки: порядок строк, ссылки полос, подписи (C-19),
перестановка планов htmx, факт на странице плана. Вёрстку проверяет браузер.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    TaskModel,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

SLUG = "webroad"


def _cli(args: list[str]) -> None:
    result = CliRunner().invoke(main, args, env={"FORCE_COLOR": ""})
    assert result.exit_code == 0, result.output


def _factory() -> tuple[sessionmaker[Session], Engine]:
    entry = Config.load().get_project(SLUG)
    assert entry is not None
    return db_for_entry(entry)


def _seed(*, closed_weeks: int) -> None:
    """Планы alpha и beta; в каждом открытые задачи; закрытые — по одной в неделю."""
    for scope in ("alpha", "beta"):
        _cli(["plan", "create", scope, "-p", SLUG, "--principle", f"{scope} work"])
        _cli(["plan", "section", "create", scope, "A", "Main", "-p", SLUG])
    factory, engine = _factory()
    try:
        with transactional(factory) as s:
            ids = {
                scope: (plan_id, section_id)
                for scope, plan_id, section_id in s.execute(
                    select(PlanModel.scope, PlanModel.row_id, PlanSectionModel.row_id).join(
                        PlanSectionModel, PlanSectionModel.plan_id == PlanModel.row_id
                    )
                ).all()
            }
            project_id = s.scalar(select(PlanModel.project_id).limit(1))
            now = datetime.now(UTC)
            rows = [("AL-1", "alpha", "todo", None), ("AL-2", "alpha", "in_progress", None)]
            rows += [("BE-1", "beta", "todo", None), ("BE-2", "beta", "todo", None)]
            rows += [
                (f"DN-{w}", "alpha", "done", now - timedelta(weeks=w + 1))
                for w in range(closed_weeks)
            ]
            for task_id, scope, status, completed in rows:
                stamp = completed - timedelta(days=2) if completed else now
                s.add(
                    TaskModel(
                        project_id=project_id,
                        task_id=task_id,
                        plan_id=ids[scope][0],
                        section_id=ids[scope][1],
                        title=f"title {task_id}",
                        status=status,
                        type="feature",
                        priority="medium",
                        created=stamp,
                        last_updated=stamp,
                        completed_at=completed,
                    )
                )
    finally:
        engine.dispose()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    root = tmp_path / SLUG
    root.mkdir()
    _cli(["project", "add", str(root), "--name", SLUG])
    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _queue_order(html: str) -> list[str]:
    return re.findall(r'<tr data-task="([^"]+)">', html)


def _assert_no_deadline_wording(html: str) -> None:
    """C-19: «срок» только в отрицании «не срок(и)», «дедлайн» — нигде."""
    text = re.sub(r"<[^>]+>", " ", html).lower()
    assert "дедлайн" not in text
    assert "deadline" not in text.replace("not deadlines", "")
    for match in re.finditer(r"срок", text):
        assert text[max(0, match.start() - 3) : match.start()] == "не ", text[
            match.start() - 40 : match.end() + 10
        ]


def test_roadmap_page_shows_queue_and_forecast(client: TestClient) -> None:
    _seed(closed_weeks=6)
    page = client.get(f"/p/{SLUG}/roadmap")
    assert page.status_code == 200
    html = page.text
    assert 'href="/p/webroad/roadmap"' in html  # вкладка
    assert _queue_order(html) == ["AL-2", "AL-1", "BE-1", "BE-2"]
    assert 'class="rm-bar rm-bar-forecast' in html
    assert 'href="/p/webroad/tasks/BE-1" class="rm-bar' in html
    _assert_no_deadline_wording(html)


def test_thin_history_gives_order_without_dates_and_says_why(client: TestClient) -> None:
    _seed(closed_weeks=0)
    html = client.get(f"/p/{SLUG}/roadmap").text
    assert _queue_order(html) == ["AL-2", "AL-1", "BE-1", "BE-2"]
    assert 'class="rm-bar rm-bar-forecast' not in html
    assert "без даты" in html
    assert "показан только порядок" in html


def test_history_toggle_draws_facts(client: TestClient) -> None:
    _seed(closed_weeks=3)
    plain = client.get(f"/p/{SLUG}/roadmap").text
    with_history = client.get(f"/p/{SLUG}/roadmap", params={"history": 1}).text
    assert 'class="rm-bar rm-bar-done' not in plain
    assert 'href="/p/webroad/tasks/DN-0" class="rm-bar rm-bar-done"' in with_history
    assert 'class="rm-bar rm-bar-working' in with_history  # AL-2 в работе


def test_move_plan_via_htmx_redraws_and_records(client: TestClient) -> None:
    _seed(closed_weeks=6)
    response = client.post(
        f"/p/{SLUG}/roadmap/plans/beta/move",
        data={"direction": "up"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert response.text.lstrip().startswith('<div id="roadmap-body">')
    assert _queue_order(response.text) == ["AL-2", "BE-1", "BE-2", "AL-1"]

    factory, engine = _factory()
    try:
        with transactional(factory) as s:
            authors = s.scalars(
                select(ActivityEventModel.actor_id).where(ActivityEventModel.kind == "plan.moved")
            ).all()
    finally:
        engine.dispose()
    assert len(authors) == 1


def test_move_plan_without_htmx_redirects(client: TestClient) -> None:
    _seed(closed_weeks=6)
    response = client.post(
        f"/p/{SLUG}/roadmap/plans/alpha/move",
        data={"direction": "down", "history": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/p/{SLUG}/roadmap?history=1"
    html = client.get(f"/p/{SLUG}/roadmap").text
    assert [p for p in re.findall(r'data-group="([^"]+)"', html)] == ["beta", "alpha"]


def test_first_plan_cannot_move_up(client: TestClient) -> None:
    _seed(closed_weeks=6)
    html = client.get(f"/p/{SLUG}/roadmap").text
    up_alpha = re.search(r'title="Выше: alpha"[^>]*>', html)
    down_beta = re.search(r'title="Ниже: beta"[^>]*>', html)
    assert up_alpha is not None and "disabled" in up_alpha.group(0)
    assert down_beta is not None and "disabled" in down_beta.group(0)


def test_plan_page_has_timeline(client: TestClient) -> None:
    """ADO-137: секции × время; закрытые — полосы created→completed_at со ссылкой."""
    _seed(closed_weeks=3)
    factory, engine = _factory()
    try:
        with transactional(factory) as s:
            plan_id = s.scalar(select(PlanModel.row_id).where(PlanModel.scope == "alpha"))
    finally:
        engine.dispose()
    html = client.get(f"/p/{SLUG}/plans/{plan_id}").text
    timeline = html[html.index('id="timeline"') : html.index("Next batch")]
    assert "A · Main" in timeline
    assert 'href="/p/webroad/tasks/DN-0" class="rm-bar rm-bar-done"' in timeline
    assert 'class="rm-bar rm-bar-working' in timeline
    assert "BE-1" not in timeline  # чужой план
    assert "спринт" not in timeline.lower()
    assert "Dependency graph" in html
    _assert_no_deadline_wording(timeline)


def test_cycle_is_explained_not_crashed(client: TestClient) -> None:
    _seed(closed_weeks=6)
    factory, engine = _factory()
    try:
        with transactional(factory) as s:
            rows = dict(
                s.execute(
                    select(TaskModel.task_id, TaskModel.row_id).where(
                        TaskModel.task_id.in_(["BE-1", "BE-2"])
                    )
                ).all()
            )
            s.add(
                DependencyModel(from_task_id=rows["BE-1"], to_task_id=rows["BE-2"], kind="blocks")
            )
            s.add(
                DependencyModel(from_task_id=rows["BE-2"], to_task_id=rows["BE-1"], kind="blocks")
            )
    finally:
        engine.dispose()
    page = client.get(f"/p/{SLUG}/roadmap")
    assert page.status_code == 200
    assert "Очередь не построена" in page.text
    assert "BE-1" in page.text and "BE-2" in page.text


def test_duration_source_is_shown_per_bar(client: TestClient) -> None:
    """ROAD-010: очередь в чел·ч — у полосы источник веса, легенда его объясняет."""
    _seed(closed_weeks=6)
    plain = client.get(f"/p/{SLUG}/roadmap").text
    factory, engine = _factory()
    try:
        with transactional(factory) as s:
            task = s.scalars(select(TaskModel).where(TaskModel.task_id == "BE-1")).one()
            task.estimate_person_hours = 6.0
    finally:
        engine.dispose()
    weighted = client.get(f"/p/{SLUG}/roadmap").text

    assert "rm-src-" not in plain  # очередь в задачах — источник у всех один
    assert 'href="/p/webroad/tasks/BE-1" class="rm-bar rm-bar-forecast rm-src-explicit"' in weighted
    assert 'class="rm-bar rm-bar-forecast rm-src-uniform"' in weighted  # остальные — средняя
    assert "Очередь в чел·ч" in weighted
