"""Одно действие — одно событие: MCP-тулы checkout/release.

До этой правки `mcp/tools/checkout_tools.py` звал `activity_service.emit`
**поверх** сервиса, который уже эмитит сам (`checkout_service.checkout` →
`emit_for_write`). На одно действие в журнал ложились две строки с разными
ключами payload (`from_status`/`to_status` против `old_status`/`new_status`).

Баг прожил долго, потому что MCP-поверхность checkout'а не была покрыта ни
одним тестом: `grep -rl checkout_tools tests/` до этого файла не находил
ничего, кроме анти-дрейфа на докстринги. CLI-двойник проверяется в
tests/cli/test_task_checkout_cli.py::test_one_activity_event_per_action —
здесь та же гарантия для машинной поверхности.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

AGENT = "agent:test-runner"


def _seed(session: Session) -> tuple[int, str]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="ev", title="EV", root_path="/tmp/ev", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="ev-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="a-core", position=0)
    session.add(sec)
    session.flush()
    t = tasks.create(
        session,
        project_id=proj.row_id,
        plan_id=plan.row_id,
        section_id=sec.row_id,
        task_id="EV-001",
        title="Задача под протокол",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
    )
    assert proj.row_id is not None
    return proj.row_id, t.task_id


def _tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _register(monkeypatch, factory, project_id: int) -> FastMCP:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import checkout_tools

    monkeypatch.setattr(checkout_tools, "session_factory", lambda project: (factory, None))
    if hasattr(checkout_tools, "require_project_id"):
        monkeypatch.setattr(
            checkout_tools, "require_project_id", lambda session, project: project_id
        )
    mcp = FastMCP("test")
    checkout_tools.register(mcp)
    return mcp


def _kinds(factory, task_id: str) -> list[str]:
    with transactional(factory) as session:
        return [
            row.kind
            for row in session.execute(
                select(ActivityEventModel)
                .where(ActivityEventModel.scope_id == task_id)
                .order_by(ActivityEventModel.row_id)
            ).scalars()
        ]


def test_mcp_checkout_emits_exactly_one_event(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, task_id = _seed(session)

    checkout = _tool(_register(monkeypatch, factory, project_id), "task_checkout")
    checkout(project="ev", task_id=task_id, agent=AGENT)

    kinds = _kinds(factory, task_id)
    assert kinds.count("task.checked_out") == 1, kinds


def test_mcp_release_emits_exactly_one_event(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, task_id = _seed(session)

    mcp = _register(monkeypatch, factory, project_id)
    _tool(mcp, "task_checkout")(project="ev", task_id=task_id, agent=AGENT)
    _tool(mcp, "task_release")(project="ev", task_id=task_id, agent=AGENT)

    kinds = _kinds(factory, task_id)
    assert kinds.count("task.checked_out") == 1, kinds
    assert kinds.count("task.released") == 1, kinds


def test_idempotent_checkout_does_not_double_the_event(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Повторный checkout тем же агентом идемпотентен — значит и событие одно.

    Это тот кейс, на котором двойной emit был заметнее всего: сервис
    возвращался рано, а тул всё равно писал свою строку.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id, task_id = _seed(session)

    checkout = _tool(_register(monkeypatch, factory, project_id), "task_checkout")
    checkout(project="ev", task_id=task_id, agent=AGENT)
    checkout(project="ev", task_id=task_id, agent=AGENT)

    kinds = _kinds(factory, task_id)
    assert kinds.count("task.checked_out") == 1, kinds


def test_no_activity_emit_left_in_checkout_tools() -> None:
    """Анти-дрейф: событие пишет сервис, тул — никогда.

    Проверка по исходнику, а не по поведению: вернуть второй `emit` можно в
    ветке, которую тесты выше не проходят (например, в обработчике ошибки).
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "cod_doc" / "mcp" / "tools" / "checkout_tools.py"
    ).read_text(encoding="utf-8")

    offenders = [
        line.strip()
        for line in src.splitlines()
        if "activity_service.emit" in line and not line.strip().startswith("#")
    ]
    assert not offenders, (
        "тул снова пишет событие поверх сервиса — на одно действие ляжет две "
        "строки журнала:\n  " + "\n  ".join(offenders)
    )
