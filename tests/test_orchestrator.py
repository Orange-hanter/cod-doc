"""Тесты cod_doc.agent.orchestrator с моком LLM"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cod_doc.agent.orchestrator import AgentEvent, Orchestrator
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project, Task, TaskStatus


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


def _make_mock_response(content: str | None = None, tool_calls: list | None = None):
    """Создать мок ответа от OpenAI."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    msg.model_dump.return_value = {"role": "assistant", "content": content, "tool_calls": []}

    choice = MagicMock()
    choice.message = msg

    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_tool_call(name: str, args: dict, call_id: str = "call_1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args, ensure_ascii=False)
    return tc


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_task_simple_message(project: Project, config: Config) -> None:
    """Агент завершает задачу текстовым сообщением без инструментов."""
    task = Task(title="Тестовая задача")
    project.add_task(task)

    mock_resp = _make_mock_response(content="Задача выполнена успешно.")

    with patch("cod_doc.agent.orchestrator.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = AsyncMock(return_value=mock_resp)
        orch = Orchestrator(project, config)

        events = []
        async for event in orch.run_task(task):
            events.append(event)

    types = [e.type for e in events]
    assert "thinking" in types
    assert "message" in types
    # Задача должна быть помечена как выполненная
    tasks = project.get_tasks(TaskStatus.DONE)
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_run_task_with_tool_call(project: Project, config: Config) -> None:
    """Агент вызывает инструмент read_file, затем завершает."""
    task = Task(title="Прочитать файл")
    project.add_task(task)

    # Первый ответ — вызов инструмента
    tool_resp = _make_mock_response(
        tool_calls=[_make_tool_call("read_file", {"path": "MASTER.md"})]
    )
    tool_resp.choices[0].message.content = None
    tool_resp.choices[0].message.model_dump.return_value = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "function": {"name": "read_file", "arguments": '{"path": "MASTER.md"}'}}],
    }

    # Второй ответ — финальное сообщение
    final_resp = _make_mock_response(content="Файл прочитан.")

    with patch("cod_doc.agent.orchestrator.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = AsyncMock(
            side_effect=[tool_resp, final_resp]
        )
        orch = Orchestrator(project, config)
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

    with patch("cod_doc.agent.orchestrator.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = AsyncMock(
            side_effect=LLMError("Неверный ключ", retryable=False)
        )
        with patch("cod_doc.agent.orchestrator.with_retry", side_effect=LLMError("Неверный ключ")):
            orch = Orchestrator(project, config)
            events = []
            async for event in orch.run_task(task):
                events.append(event)

    error_events = [e for e in events if e.type == "error"]
    assert len(error_events) == 1
    assert "Неверный ключ" in str(error_events[0].data)

    failed_tasks = project.get_tasks(TaskStatus.FAILED)
    assert len(failed_tasks) == 1


@pytest.mark.asyncio
async def test_async_on_ask_human(project: Project, config: Config) -> None:
    """Async ask_human callback вызывается и ответ попадает в историю."""
    task = Task(title="Задача с вопросом")
    project.add_task(task)

    ask_call = _make_tool_call("ask_human", {"question": "Какой цвет?", "context": "тест"})
    tool_resp = _make_mock_response(tool_calls=[ask_call])
    tool_resp.choices[0].message.content = None
    tool_resp.choices[0].message.model_dump.return_value = {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "call_1", "function": {"name": "ask_human", "arguments": '{"question": "Какой цвет?", "context": "тест"}'}}],
    }
    final_resp = _make_mock_response(content="Ответ получен.")

    async def fake_ask(question: str, context: str) -> str:
        return "синий"

    with patch("cod_doc.agent.orchestrator.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = AsyncMock(
            side_effect=[tool_resp, final_resp]
        )
        orch = Orchestrator(project, config, async_on_ask_human=fake_ask)
        events = []
        async for event in orch.run_task(task):
            events.append(event)

    blocked = [e for e in events if e.type == "blocked"]
    assert len(blocked) == 1
    assert "Какой цвет?" in blocked[0].data


@pytest.mark.asyncio
async def test_run_autonomous_no_tasks_generates_from_master(project: Project, config: Config) -> None:
    """Если задач нет, агент анализирует MASTER.md и создаёт задачи."""
    create_call = _make_tool_call("create_task", {"title": "Создать спецификацию", "priority": 1})
    gen_resp = _make_mock_response(tool_calls=[create_call])
    gen_resp.choices[0].message.content = "Создал задачу."
    gen_resp.choices[0].message.model_dump.return_value = {
        "role": "assistant", "content": "Создал задачу.",
        "tool_calls": [{"id": "call_1", "function": {"name": "create_task", "arguments": '{"title": "Создать спецификацию", "priority": 1}'}}],
    }

    run_resp = _make_mock_response(content="Задача выполнена.")

    with patch("cod_doc.agent.orchestrator.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = AsyncMock(
            side_effect=[gen_resp, run_resp]
        )
        orch = Orchestrator(project, config)
        events = []
        async for event in orch.run_autonomous():
            events.append(event)

    types = [e.type for e in events]
    assert "thinking" in types
    # Задача была создана
    all_tasks = project.get_tasks()
    assert len(all_tasks) >= 1
