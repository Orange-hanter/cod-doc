"""ADO-078: отменённая задача уходит из остатка, но не в «сделано».

Дефект был в одном выражении: `remaining = total - done`
(`plan_service/_types.py`). Закрытых состояний два —
`task_status_machine.TERMINAL_STATUSES` = `{done, cancelled}`, — и второе в
остатке оставалось навсегда. На живом плане `adoption-2026-08` четыре
отменённые задачи числились несделанными третий месяц, а план с восемью
закрытыми и двумя отменёнными задачами из десяти висел в `in-progress`, не
имея ни одной задачи, которую можно взять.

Ловушка, от которой эти тесты и стоят: «починить» это можно, прибавив
`cancelled` к `done`. Тогда число отменённых исчезает, и «сделано 10» врёт про
план, где сделано 8. Проверяется ОБА утверждения сразу: остаток честный И
`done` не раздут.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import TaskStatus
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.services import plan_service as plans

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed(session: Session, sections: dict[str, list[str]]) -> tuple[int, dict[str, int]]:
    """Проект/план/секции; в каждой секции по задаче на каждый статус."""
    now = datetime.now(UTC)
    project = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    project.created = now
    project.updated = now
    session.add(project)
    session.flush()

    plan = PlanModel(
        project_id=project.row_id,
        scope="p-plan",
        principle="test-first",
        created=now,
        last_updated=now,
    )
    session.add(plan)
    session.flush()

    section_ids: dict[str, int] = {}
    counter = 0
    for position, (letter, statuses) in enumerate(sections.items()):
        section = PlanSectionModel(
            plan_id=plan.row_id,
            letter=letter,
            title=f"Section {letter}",
            slug=f"{letter}-Section",
            position=position,
        )
        session.add(section)
        session.flush()
        section_ids[letter] = section.row_id
        for status in statuses:
            counter += 1
            session.add(
                TaskModel(
                    project_id=project.row_id,
                    task_id=f"P-{counter:03d}",
                    plan_id=plan.row_id,
                    section_id=section.row_id,
                    title=f"Task {counter}",
                    status=status,
                    type="feature",
                    priority="medium",
                    created=now,
                    last_updated=now,
                )
            )
    session.flush()
    return plan.row_id, section_ids


def test_remaining_excludes_cancelled_without_inflating_done(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Ядро задачи: done + cancelled + todo — остаток только реальный."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(
            session,
            {"A": ["done", "done", "cancelled", "todo", "todo", "todo"]},
        )

    with transactional(factory) as session:
        progress = plans.recalc(session, plan_id)

    assert progress.total == 6
    assert progress.done == 2, "cancelled не должен прибавляться к done"
    assert progress.cancelled == 1
    assert progress.remaining == 3, "осталось три todo, а не четыре"
    assert progress.total == progress.done + progress.cancelled + progress.remaining


def test_cancelled_is_visible_per_section(engine_with_schema) -> None:
    """Секционные счётчики несут то же число, а не только план целиком."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(
            session,
            {"A": ["done", "cancelled"], "B": ["todo", "todo", "cancelled"]},
        )

    with transactional(factory) as session:
        progress = plans.recalc(session, plan_id)

    by_letter = {s.letter: s for s in progress.sections}
    assert (by_letter["A"].cancelled, by_letter["A"].remaining) == (1, 0)
    assert (by_letter["B"].cancelled, by_letter["B"].remaining) == (1, 2)
    assert progress.cancelled == 2


def test_plan_reaches_done_when_only_cancelled_remain(engine_with_schema) -> None:
    """Живой симптом: 8 done + 2 cancelled из 10 больше не висят вечно.

    Статус доходит до `done`, потому что взять нечего, — но `done` остаётся
    равным 8, а не 10: число отменённых видно рядом.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(session, {"A": ["done"] * 8 + ["cancelled"] * 2})

    with transactional(factory) as session:
        progress = plans.recalc(session, plan_id)

    assert progress.status is plans.DerivedStatus.DONE
    assert progress.sections[0].status is plans.DerivedStatus.DONE
    assert (progress.done, progress.cancelled, progress.remaining) == (8, 2, 0)


def test_backlog_stays_in_remaining(engine_with_schema) -> None:
    """Решение по acceptance §1: `backlog` — припаркованная работа, не закрытая.

    Переход `backlog → todo` разрешён `ALLOWED_TRANSITIONS`, а «решили не
    делать» выражается переводом в `cancelled`. Считать backlog закрытым
    значило бы завести третье определение «закрыто» рядом с
    `TERMINAL_STATUSES` и литералами вьюх.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(session, {"A": ["done", "backlog", "cancelled"]})

    with transactional(factory) as session:
        progress = plans.recalc(session, plan_id)

    assert progress.remaining == 1, "backlog остаётся в остатке"
    assert progress.status is plans.DerivedStatus.IN_PROGRESS


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["cancelled"], 0),
        (["blocked", "in_review", "todo", "backlog"], 4),
        (["done", "in_progress", "cancelled"], 1),
    ],
)
def test_remaining_counts_every_non_terminal_bucket(  # type: ignore[no-untyped-def]
    engine_with_schema, statuses: list[str], expected: int
) -> None:
    """Из остатка выходят ровно два статуса — те, что терминальны."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(session, {"A": statuses})

    with transactional(factory) as session:
        progress = plans.recalc(session, plan_id)

    assert progress.remaining == expected


def test_recalc_for_project_agrees_with_recalc(engine_with_schema) -> None:
    """Пакетный путь overview-страницы считает то же, что поштучный recalc.

    У них разные SQL (COD-075 убирал N+1), и разойтись они могут молча.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(session, {"A": ["done", "cancelled", "todo"]})
        project_id = session.get(PlanModel, plan_id).project_id  # type: ignore[union-attr]

    with transactional(factory) as session:
        one = plans.recalc(session, plan_id)
        batch = plans.recalc_for_project(session, project_id)[plan_id]

    assert (batch.total, batch.done, batch.cancelled, batch.remaining) == (
        one.total,
        one.done,
        one.cancelled,
        one.remaining,
    )
    assert batch.status is one.status


def test_export_progress_overview_shows_cancelled_column(engine_with_schema) -> None:
    """Проекция в markdown: без колонки строка «3 / 1 / 1» читается как ошибка."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(session, {"A": ["done", "cancelled", "todo"]})

    with transactional(factory) as session:
        overview = plans.export(session, plan_id)["progress_overview"]

    assert "| Section | Total | Done | Cancelled | Remaining | Status |" in overview
    assert "| **TOTAL** | **3** | **1** | **1** | **1** |" in overview


def test_cancelled_task_status_is_still_reachable(engine_with_schema) -> None:
    """Побочная находка задачи: `cancelled` — законный статус, а не дыра.

    Внешний агент на пилоте решил, что статуса нет вовсе («только pending /
    in-progress / done»), и собирался удалять строки из БД руками. Выдача
    прогресса теперь называет его вслух.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(session, {"A": [TaskStatus.CANCELLED.value]})

    with transactional(factory) as session:
        progress = plans.recalc(session, plan_id)

    assert progress.cancelled == 1


def test_pct_closed_agrees_with_done_status(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ai-review #87 (major): страницы считали процент как `done / total`.

    План со статусом DONE, где две задачи из десяти отменены, рисовал «80%» —
    статус и процент отвечали на разные вопросы. `pct_closed` считает
    закрытое: `done` и `cancelled` вместе.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(session, {"A": ["done"] * 8 + ["cancelled"] * 2})

    with transactional(factory) as session:
        progress = plans.recalc(session, plan_id)

    assert progress.status is plans.DerivedStatus.DONE
    assert progress.pct_closed == 100
    assert progress.sections[0].pct_closed == 100


def test_pct_closed_when_every_task_is_cancelled(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Крайний случай из ревью: всё отменено — DONE и 100, а не DONE и 0."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(session, {"A": ["cancelled"] * 3})

    with transactional(factory) as session:
        progress = plans.recalc(session, plan_id)

    assert progress.status is plans.DerivedStatus.DONE
    assert progress.pct_closed == 100


def test_pct_closed_of_an_open_plan_counts_only_closed_work(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Открытая работа — `todo` и `in_progress` — в процент не входит."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(session, {"A": ["done", "cancelled", "todo", "in_progress"]})

    with transactional(factory) as session:
        progress = plans.recalc(session, plan_id)

    assert progress.pct_closed == 50
    assert progress.remaining == 2
