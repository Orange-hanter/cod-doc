"""ADO-182: фильтр по статусу не должен резать класс эквивалентности.

`pending ≡ todo` и `in-progress ≡ in_progress` — одни бакеты по машине
состояний, но в базе лежат оба написания одновременно (на живой БД было 123
`pending` и 6 `todo`). Репозиторий сравнивал статус точной строкой, поэтому
вопрос «что готово к работе» возвращал либо одну группу, либо другую, и
получить все 129 было нельзя.

Карта алиасов переехала в `domain`, потому что читать её нужно из `infra`, а
импорт `services` оттуда запрещён слоями.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import (
    TASK_STATUS_ALIASES,
    Priority,
    TaskStatus,
    TaskType,
    canonical_task_status,
    equivalent_task_statuses,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ── чистая функция ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (TaskStatus.TODO, {"todo", "pending"}),
        (TaskStatus.PENDING, {"todo", "pending"}),
        (TaskStatus.IN_PROGRESS_NEW, {"in_progress", "in-progress"}),
        (TaskStatus.IN_PROGRESS, {"in_progress", "in-progress"}),
    ],
)
def test_aliases_collapse_to_the_same_set(status: TaskStatus, expected: set[str]) -> None:
    """Оба написания дают одно и то же множество — с какой стороны ни спроси."""
    assert equivalent_task_statuses(status) == expected


@pytest.mark.parametrize("status", [TaskStatus.BLOCKED, TaskStatus.CANCELLED, TaskStatus.DONE])
def test_buckets_without_synonyms_stay_single(status: TaskStatus) -> None:
    """Расширение не должно протечь на бакеты без синонимов."""
    assert equivalent_task_statuses(status) == {status.value}


def test_every_alias_target_is_reachable_from_both_sides() -> None:
    """Карта алиасов и функция эквивалентности не разъезжаются."""
    for raw, canon in TASK_STATUS_ALIASES.items():
        assert canonical_task_status(raw) == canon
        assert equivalent_task_statuses(raw) == equivalent_task_statuses(canon)


# ── фильтр репозитория ──────────────────────────────────────────────────────


def _seed(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="a", position=0)
    session.add(sec)
    session.flush()

    # Обе формы «готово к работе» плюс контрольные записи в других бакетах.
    for task_id, status in (
        ("MIX-001", TaskStatus.PENDING),
        ("MIX-002", TaskStatus.PENDING),
        ("MIX-003", TaskStatus.TODO),
        ("MIX-004", TaskStatus.BLOCKED),
        ("MIX-005", TaskStatus.CANCELLED),
    ):
        tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=sec.row_id,
            task_id=task_id,
            title=f"задача {task_id}",
            type=TaskType.FEATURE,
            priority=Priority.LOW,
            author="t",
        )
        if status is not TaskStatus.PENDING:
            tasks.update_status(session, task_id=task_id, new_status=status, author="t")
    return proj.row_id


def test_both_spellings_return_the_same_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Ядро задачи: спросить бакет целиком можно с любой стороны."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed(session)

        by_pending = {
            t.task_id
            for t in tasks.list_for_project(session, project_id, status=TaskStatus.PENDING)
        }
        by_todo = {
            t.task_id for t in tasks.list_for_project(session, project_id, status=TaskStatus.TODO)
        }

        assert by_pending == by_todo == {"MIX-001", "MIX-002", "MIX-003"}


def test_filter_without_synonyms_is_unchanged(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """У бакетов без алиасов поведение прежнее — расширение не протекло."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed(session)

        blocked = {
            t.task_id
            for t in tasks.list_for_project(session, project_id, status=TaskStatus.BLOCKED)
        }
        cancelled = {
            t.task_id
            for t in tasks.list_for_project(session, project_id, status=TaskStatus.CANCELLED)
        }

        assert blocked == {"MIX-004"}
        assert cancelled == {"MIX-005"}


# ── контракт, который нельзя сдвинуть ───────────────────────────────────────


def test_state_machine_still_exposes_the_alias_map() -> None:
    """`_LEGACY_ALIASES` уезжает в payload MCP — имя и содержимое не менялись.

    `mcp/tools/agent_tools.py` и `mcp/tools/context_tools.py` кладут его как
    `task_status_legacy_aliases`; переезд карты в `domain` обязан быть
    незаметным снаружи.
    """
    from cod_doc.services.task_status_machine import _LEGACY_ALIASES, normalise

    assert _LEGACY_ALIASES == {"pending": "todo", "in-progress": "in_progress"}
    assert normalise("pending") == "todo"
    assert normalise(TaskStatus.IN_PROGRESS) == "in_progress"
    assert normalise("blocked") == "blocked"
