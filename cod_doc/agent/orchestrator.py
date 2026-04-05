"""
Автономный агент-оркестратор COD-DOC.

Алгоритм:
1. Прочитать MASTER.md (L0)
2. Спарсить next_actions или взять следующую задачу из очереди
3. Если задач нет — сгенерировать их из состояния MASTER.md
4. Выполнить задачу через цикл LLM + инструменты
5. Обновить MASTER.md, хэши, changelog
6. Повторить или встать в idle
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Awaitable
from typing import Any, Callable

from openai import AsyncOpenAI

from cod_doc.agent.prompts import SYSTEM_PROMPT
from cod_doc.agent.retry import LLMError, with_retry
from cod_doc.agent.tools import TOOL_DEFINITIONS, ToolExecutor
from cod_doc.config import Config
from cod_doc.core.project import Project, Task, TaskStatus

# Тип async-callback для запроса к человеку
AskHumanAsync = Callable[[str, str], Awaitable[str]]

logger = logging.getLogger("cod_doc.agent")


class AgentEvent:
    """Событие агента для стриминга в TUI/API."""

    def __init__(self, event_type: str, data: Any) -> None:
        self.type = event_type  # thinking | tool_call | tool_result | message | done | error | blocked
        self.data = data

    def to_dict(self) -> dict:
        return {"type": self.type, "data": self.data}


class Orchestrator:
    """Автономный агент управления документацией."""

    def __init__(
        self,
        project: Project,
        config: Config,
        on_ask_human: Callable[[str, str], str] | None = None,
        async_on_ask_human: AskHumanAsync | None = None,
    ) -> None:
        self.project = project
        self.config = config
        self._async_on_ask_human = async_on_ask_human
        self.client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            default_headers={
                "HTTP-Referer": "https://github.com/cod-doc",
                "X-Title": "COD-DOC Orchestrator",
            },
        )
        # Передаём sync-callback только в daemon/CLI режиме
        self.executor = ToolExecutor(
            project,
            on_ask_human=on_ask_human if not async_on_ask_human else None,
            chroma_path=config.chroma_path,
        )

    # ── Public API ───────────────────────────────────────────────────────────

    async def run_task(self, task: Task) -> AsyncGenerator[AgentEvent, None]:
        """Выполнить одну задачу. Стримит AgentEvent."""
        self.project.update_task(task.id, status=TaskStatus.IN_PROGRESS)
        self.project.set_status("running")
        messages = self._build_messages(task)

        yield AgentEvent("thinking", f"Начинаю задачу: {task.title}")

        iterations = 0
        async for event in self._agent_loop(messages, task):
            yield event
            iterations += 1
            if iterations > self.config.max_iterations:
                yield AgentEvent("error", "Превышен лимит итераций")
                self.project.update_task(task.id, status=TaskStatus.FAILED, result="Max iterations exceeded")
                break

        self.project.set_status("idle")

    async def run_autonomous(self) -> AsyncGenerator[AgentEvent, None]:
        """
        Автономный режим: читает MASTER.md, формирует задачи, выполняет их.
        Возвращает после завершения всех текущих задач.
        """
        yield AgentEvent("thinking", f"Запуск автономного режима для проекта: {self.project.entry.name}")

        # Шаг 1: Проверить очередь
        task = self.project.next_pending_task()

        # Шаг 2: Если задач нет — сгенерировать из MASTER.md
        if not task:
            yield AgentEvent("thinking", "Нет задач в очереди. Анализирую MASTER.md...")
            async for event in self._generate_tasks_from_master():
                yield event
            task = self.project.next_pending_task()

        if not task:
            yield AgentEvent("done", "Задач для выполнения не найдено. Проект в актуальном состоянии.")
            return

        # Шаг 3: Выполнить задачу
        async for event in self.run_task(task):
            yield event

        yield AgentEvent("done", f"Задача завершена: {task.title}")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_messages(self, task: Task) -> list[dict]:
        """Построить начальные сообщения для задачи."""
        master_content = self.project.read_master() or "MASTER.md не найден."
        user_message = (
            f"## Задача [{task.id}]: {task.title}\n\n"
            f"{task.description}\n\n"
            f"## MASTER.md (L0)\n\n```markdown\n{master_content[:3000]}\n```\n\n"
            "Выполни задачу, используя доступные инструменты. "
            "В конце завершения обнови хэши и добавь запись в changelog MASTER.md."
        )
        return [{"role": "user", "content": user_message}]

    async def _agent_loop(
        self, messages: list[dict], task: Task
    ) -> AsyncGenerator[AgentEvent, None]:
        """Основной цикл агент ↔ LLM ↔ инструменты."""
        while True:
            if self.executor.is_blocked:
                yield AgentEvent("blocked", self.executor._blocked_question)
                return

            # Запрос к LLM с retry
            try:
                llm_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
                response = await with_retry(
                    lambda: self.client.chat.completions.create(
                        model=self.config.model,
                        messages=llm_messages,
                        tools=TOOL_DEFINITIONS,
                        tool_choice="auto",
                        max_tokens=self.config.max_tokens,
                    )
                )
            except LLMError as e:
                yield AgentEvent("error", str(e))
                self.project.update_task(task.id, status=TaskStatus.FAILED, result=str(e))
                return

            msg = response.choices[0].message

            # Добавить ответ в историю
            messages.append(msg.model_dump(exclude_none=True))

            # Нет вызовов инструментов → задача завершена
            if not msg.tool_calls:
                content = msg.content or ""
                yield AgentEvent("message", content)
                # Пометить задачу как выполненную если агент не сделал это сам
                if self.project._load_tasks():
                    tasks = [t for t in self.project._load_tasks() if t.id == task.id]
                    if tasks and tasks[0].status == TaskStatus.IN_PROGRESS:
                        self.project.update_task(task.id, status=TaskStatus.DONE, result=content[:500])
                return

            # Обработка вызовов инструментов
            tool_results = []
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = tc.function.arguments

                yield AgentEvent("tool_call", {"name": fn_name, "args": fn_args})

                # ask_human — единственный инструмент с async-путём
                if fn_name == "ask_human" and self._async_on_ask_human is not None:
                    args: dict = json.loads(fn_args) if isinstance(fn_args, str) else fn_args
                    question = args.get("question", "")
                    context = args.get("context", "")
                    yield AgentEvent("blocked", question)
                    answer = await self._async_on_ask_human(question, context)
                    result = json.dumps({"answer": answer}, ensure_ascii=False)
                else:
                    result = self.executor.execute(fn_name, fn_args)
                    if self.executor.is_blocked:
                        yield AgentEvent("blocked", self.executor._blocked_question)
                        return

                yield AgentEvent("tool_result", {"name": fn_name, "result": result})
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            messages.extend(tool_results)

    async def _generate_tasks_from_master(self) -> AsyncGenerator[AgentEvent, None]:
        """Попросить LLM сгенерировать задачи на основе MASTER.md."""
        master = self.project.read_master()
        if not master:
            yield AgentEvent("error", "MASTER.md не найден — невозможно сгенерировать задачи")
            return

        prompt = (
            "Проанализируй MASTER.md и создай задачи для приведения документации в актуальное состояние. "
            "Используй инструмент create_task для каждой задачи. "
            "Если документация полностью актуальна — ничего не создавай.\n\n"
            f"## MASTER.md\n\n```markdown\n{master[:4000]}\n```"
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            llm_msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
            allowed = {t["function"]["name"] for t in TOOL_DEFINITIONS if t["function"]["name"] in ("create_task", "get_project_status")}
            tools_subset = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in allowed]
            response = await with_retry(
                lambda: self.client.chat.completions.create(
                    model=self.config.model,
                    messages=llm_msgs,
                    tools=tools_subset,
                    tool_choice="auto",
                    max_tokens=2048,
                )
            )
        except LLMError as e:
            yield AgentEvent("error", f"Ошибка генерации задач: {e}")
            return

        msg = response.choices[0].message
        if msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.function.name == "create_task":
                    result = self.executor.execute("create_task", tc.function.arguments)
                    yield AgentEvent("tool_result", {"name": "create_task", "result": result})

        if msg.content:
            yield AgentEvent("message", msg.content)


# ── Daemon runner ─────────────────────────────────────────────────────────────

async def run_daemon(config: Config, log_callback: Callable[[str], None] | None = None) -> None:
    """
    Daemon-режим: бесконечный цикл обработки задач по всем проектам.
    Используется в Docker production-режиме.
    """
    from cod_doc.core.project import Project

    def log(msg: str) -> None:
        logger.info(msg)
        if log_callback:
            log_callback(msg)

    log(f"COD-DOC daemon запущен. Интервал: {config.agent_interval}s")

    while True:
        for entry in config.list_projects():
            if not entry.enabled:
                continue
            project = Project(entry)
            try:
                project.init()
            except Exception as e:
                log(f"[{entry.name}] Ошибка инициализации: {e}")
                continue

            orch = Orchestrator(project, config)
            async for event in orch.run_autonomous():
                log(f"[{entry.name}] {event.type}: {event.data}")

        await asyncio.sleep(config.agent_interval)
