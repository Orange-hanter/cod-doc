"""Тесты cod_doc.agent.orchestrator с MockAdapter (PCA-302)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cod_doc.agent.adapters.mock import MockAdapter
from cod_doc.agent.adapters.base import ChatResponse
from cod_doc.agent.orchestrator import Orchestrator
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project, Task, TaskStatus

if TYPE_CHECKING:
    from pathlib import Path

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def project(tmp_path: Path) -> Project:
    entry = ProjectEntry(name="test", path=str(tmp_path))
    proj = Project(entry)
    proj.init()
    return proj


@pytest.fixture
def config() -> Config:
    return Config(
        api_key="test-key",
        model="test/model",
        base_url="https://example.com",
        max_iterations=10,
    )


def _orch(project: Project, config: Config, responses: list[ChatResponse], **kw: Any) -> Orchestrator:
    """Create an Orchestrator backed by a MockAdapter."""
    adapter = MockAdapter(responses=responses)
    orch = Orchestrator(project, config, adapter=adapter, **kw)
    return orch


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_task_simple_message(project: Project, config: Config) -> None:
    """Агент завершает задачу текстовым сообщением без инструментов."""
    task = Task(title="Тестовая задача")
    project.add_task(task)

    orch = _orch(project, config, [MockAdapter.text_response("Задача выполнена успешно.")])
    events = []
    async for event in orch.run_task(task):
        events.append(event)

    types = [e.type for e in events]
    assert "thinking" in types
    assert "message" in types
    tasks = project.get_tasks(TaskStatus.DONE)
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_run_task_with_tool_call(project: Project, config: Config) -> None:
    """Агент вызывает инструмент read_file, затем завершает."""
    task = Task(title="Прочитать файл")
    project.add_task(task)

    orch = _orch(project, config, [
        MockAdapter.tool_call_response("read_file", {"path": "MASTER.md"}, call_id="call_1"),
        MockAdapter.text_response("Файл прочитан."),
    ])
    events = []
    async for event in orch.run_task(task):
        events.append(event)

    types = [e.type for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    assert "message" in types


@pytest.mark.asyncio
async def test_run_task_llm_error(project: Project, config: Config) -> None:
    """LLM-ошибка приводит к event 'error' и статусу FAILED."""
    from cod_doc.agent.retry import LLMError

    task = Task(title="Задача с ошибкой")
    project.add_task(task)

    adapter = MockAdapter()
    adapter.chat = AsyncMock(side_effect=LLMError("Неверный ключ", retryable=False))  # type: ignore[method-assign]

    orch = Orchestrator(project, config, adapter=adapter)
    events = []
    async for event in orch.run_task(task):
        events.append(event)

    error_events = [e for e in events if e.type == "error"]
    assert len(error_events) == 1
    assert "Неверный ключ" in str(error_events[0].data)
    assert len(project.get_tasks(TaskStatus.FAILED)) == 1


@pytest.mark.asyncio
async def test_async_on_ask_human(project: Project, config: Config) -> None:
    """Async ask_human callback вызывается и ответ попадает в историю."""
    task = Task(title="Задача с вопросом")
    project.add_task(task)

    async def fake_ask(question: str, context: str) -> str:
        return "синий"

    orch = _orch(project, config, [
        MockAdapter.tool_call_response(
            "ask_human",
            {"question": "Какой цвет?", "context": "тест"},
            call_id="call_1",
        ),
        MockAdapter.text_response("Ответ получен."),
    ], async_on_ask_human=fake_ask)
    events = []
    async for event in orch.run_task(task):
        events.append(event)

    blocked = [e for e in events if e.type == "blocked"]
    assert len(blocked) == 1
    assert "Какой цвет?" in blocked[0].data


@pytest.mark.asyncio
async def test_run_autonomous_no_tasks_generates_from_master(
    project: Project, config: Config
) -> None:
    """Если задач нет, агент анализирует MASTER.md и создаёт задачи."""
    orch = _orch(project, config, [
        MockAdapter.tool_call_response(
            "create_task",
            {"title": "Создать спецификацию", "priority": 1},
            call_id="call_1",
        ),
        MockAdapter.text_response("Создал задачу."),
        MockAdapter.text_response("Задача выполнена."),
    ])
    events = []
    async for event in orch.run_autonomous():
        events.append(event)

    types = [e.type for e in events]
    assert "thinking" in types
    all_tasks = project.get_tasks()
    assert len(all_tasks) >= 1


# --------------------------------------------------------------------------- #
# PCA-022 / PCA-024: WakeContext injection + scoped fast-path                  #
# --------------------------------------------------------------------------- #


def _get_sent_messages(adapter: MockAdapter) -> list[dict]:  # type: ignore[no-untyped-def]
    """Return messages sent on the first LLM call."""
    return adapter.calls[0]["messages"]


@pytest.mark.asyncio
async def test_run_task_cold_start_includes_master_block(
    project: Project, config: Config
) -> None:
    """Без WakeContext — _build_messages идёт по 'full' пути с MASTER.md (L0) блоком."""
    task = Task(title="Cold start task", description="do thing")
    project.add_task(task)

    adapter = MockAdapter([MockAdapter.text_response("done")])
    orch = Orchestrator(project, config, adapter=adapter)
    async for _ in orch.run_task(task):
        pass

    msgs = _get_sent_messages(adapter)
    user_msgs = [m for m in msgs if m.get("role") == "user"]
    first_user = user_msgs[0]["content"]
    assert "MASTER.md (L0)" in first_user
    assert "WAKE PAYLOAD" not in first_user


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,extra_kw",
    [
        ("task_assigned", {"task_id": "stub"}),
        ("approval_resolved", {"task_id": "stub"}),
        ("doc_drift", {"triggering_doc_ref": "doc:foo"}),
    ],
)
async def test_run_task_scoped_wake_skips_master_and_prepends_payload(
    project: Project,
    config: Config,
    reason: str,
    extra_kw: dict,  # type: ignore[type-arg]
) -> None:
    """Scoped wake_reason → MASTER.md (L0) НЕ грузится; WAKE PAYLOAD идёт первым."""
    from cod_doc.agent.wake_context import WakeContext, WakeReason

    task = Task(title="scoped wake task")
    project.add_task(task)
    wake = WakeContext(reason=WakeReason(reason), **extra_kw)

    adapter = MockAdapter([MockAdapter.text_response("ack")])
    orch = Orchestrator(project, config, adapter=adapter)
    async for _ in orch.run_task(task, wake=wake):
        pass

    msgs = _get_sent_messages(adapter)
    user_msgs = [m for m in msgs if m.get("role") == "user"]
    assert user_msgs[0]["content"].startswith("WAKE PAYLOAD"), user_msgs[0]["content"][:120]
    full_user_text = "\n".join(m["content"] for m in user_msgs)
    assert "MASTER.md (L0)" not in full_user_text


@pytest.mark.asyncio
async def test_run_task_cold_start_wake_still_reads_master(
    project: Project, config: Config
) -> None:
    """Wake с reason=cold_start не считается scoped: MASTER грузится, WAKE PAYLOAD остаётся."""
    from cod_doc.agent.wake_context import WakeContext, WakeReason

    task = Task(title="cold wake")
    project.add_task(task)
    wake = WakeContext(reason=WakeReason.COLD_START)

    adapter = MockAdapter([MockAdapter.text_response("ack")])
    orch = Orchestrator(project, config, adapter=adapter)
    async for _ in orch.run_task(task, wake=wake):
        pass

    msgs = _get_sent_messages(adapter)
    user_msgs = [m for m in msgs if m.get("role") == "user"]
    assert user_msgs[0]["content"].startswith("WAKE PAYLOAD")
    full_user_text = "\n".join(m["content"] for m in user_msgs)
    assert "MASTER.md (L0)" in full_user_text


# --------------------------------------------------------------------------- #
# PCA-034: run_id propagates through the heartbeat                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_task_sets_run_id_during_heartbeat(
    project: Project, config: Config
) -> None:
    """During Orchestrator.run_task, get_current_run_id() returns the heartbeat run_id."""
    from cod_doc.services.run_context import get_current_run_id

    captured: list[str | None] = []

    async def _capture_then_complete(  # type: ignore[no-untyped-def]
        messages, tools, *, model, max_tokens, temperature=None
    ):
        captured.append(get_current_run_id())
        return MockAdapter.text_response("ack")

    task = Task(title="run-id propagation")
    project.add_task(task)

    adapter = MockAdapter()
    adapter.chat = _capture_then_complete  # type: ignore[method-assign]
    orch = Orchestrator(project, config, adapter=adapter)
    async for _ in orch.run_task(task):
        pass

    assert captured, "expected at least one LLM call"
    assert all(r is not None for r in captured), captured
    assert len(set(captured)) == 1, f"run_id changed mid-heartbeat: {captured}"


@pytest.mark.asyncio
async def test_run_task_resets_run_id_after_heartbeat(
    project: Project, config: Config
) -> None:
    """After Orchestrator.run_task returns, get_current_run_id() is None."""
    from cod_doc.services.run_context import get_current_run_id

    task = Task(title="reset check")
    project.add_task(task)

    assert get_current_run_id() is None
    orch = _orch(project, config, [MockAdapter.text_response("done")])
    async for _ in orch.run_task(task):
        pass
    assert get_current_run_id() is None


@pytest.mark.asyncio
async def test_run_task_resets_run_id_after_max_iterations(
    project: Project, config: Config
) -> None:
    """Max-iterations branch hits finalize_orchestrator_run via the finally block."""
    from cod_doc.services.run_context import get_current_run_id

    task = Task(title="max-iter check")
    project.add_task(task)
    config.max_iterations = 1

    # Always return a tool call so the loop never terminates on its own.
    adapter = MockAdapter()
    adapter.chat = AsyncMock(  # type: ignore[method-assign]
        return_value=MockAdapter.tool_call_response("read_file", {"path": "MASTER.md"})
    )
    orch = Orchestrator(project, config, adapter=adapter)
    async for _ in orch.run_task(task):
        pass
    assert get_current_run_id() is None
