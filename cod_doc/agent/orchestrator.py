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
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from cod_doc.agent.prompts import SYSTEM_PROMPT
from cod_doc.agent.retry import (
    ContextLengthExceededError,
    LLMError,
    with_retry,
)
from cod_doc.agent.tools import TOOL_DEFINITIONS, ToolExecutor
from cod_doc.core.project import Project, Task, TaskStatus

if TYPE_CHECKING:
    from cod_doc.agent.wake_context import WakeContext
    from cod_doc.config import Config

# Тип async-callback для запроса к человеку
AskHumanAsync = Callable[[str, str], Awaitable[str]]

logger = logging.getLogger("cod_doc.agent")

# Максимальное число retry-попыток при context_length_exceeded
_MAX_CONTEXT_RETRIES = 3


class AgentEvent:
    """Событие агента для стриминга в TUI/API."""

    def __init__(self, event_type: str, data: Any) -> None:
        self.type = (
            event_type  # thinking | tool_call | tool_result | message | done | error | blocked
        )
        self.data = data

    def to_dict(self) -> dict[str, Any]:
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
            api_key=config.api_key,
            base_url=config.base_url,
            embedding_model=config.embedding_model,
            embedding_backend=config.embedding_backend,
        )

    # ── Public API ───────────────────────────────────────────────────────────

    async def run_task(
        self,
        task: Task,
        wake: WakeContext | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Выполнить одну задачу. Стримит AgentEvent.

        ``wake`` (PCA-022): опциональный WakeContext, инжектится первым
        сообщением conversation. Для scoped-причин (task_assigned /
        doc_drift / approval_resolved) — ``MASTER.md`` не читается на
        первом round-trip; агент действует по wake-payload и ``context_refs``.
        Cold-start (wake is None или wake.reason in {COLD_START, MANUAL})
        — прежний flow с full MASTER-секциями.

        PCA-034: на старте run_task вписывается ``agent_run`` row + ставится
        contextvar run_id; все мутации сервисов внутри heartbeat'а
        автоматически получают этот run_id (см. PCA-031). На выходе — row
        помечается ``done``/``failed`` + contextvar reset'ится.
        """
        from datetime import UTC, datetime

        from ulid import ULID

        from cod_doc.services import event_bus
        from cod_doc.services.run_context import (
            finalize_orchestrator_run,
            start_orchestrator_run,
        )

        slug = self.project.entry.name
        run_id = str(ULID.from_datetime(datetime.now(UTC)))
        run_token = start_orchestrator_run(
            project_path=str(self.project.entry.path),
            run_id=run_id,
            wake_reason=(wake.reason.value if wake is not None else None),
            triggering_task_id=task.id,
            triggering_doc_ref=(wake.triggering_doc_ref if wake is not None else None),
        )

        self.project.update_task(task.id, status=TaskStatus.IN_PROGRESS)
        self.project.set_status("running")
        scoped = wake is not None and wake.is_scoped
        messages = self._build_messages(
            task, context_mode="no_master" if scoped else "full"
        )
        if wake is not None:
            messages.insert(0, {"role": "user", "content": wake.to_message_block()})

        await event_bus.publish(
            slug, "agent.started", {"task_id": task.id, "title": task.title, "run_id": run_id}
        )
        yield AgentEvent("thinking", f"Начинаю задачу: {task.title}")

        run_status = "done"
        try:
            iterations = 0
            async for event in self._agent_loop(messages, task):
                # Mirror thinking/tool events to the UI as agent.step.
                await event_bus.publish(
                    slug,
                    f"agent.{event.type}",
                    {"task_id": task.id, "data": event.data, "iteration": iterations},
                )
                yield event
                iterations += 1
                if iterations > self.config.max_iterations:
                    yield AgentEvent("error", "Превышен лимит итераций")
                    self.project.update_task(
                        task.id, status=TaskStatus.FAILED, result="Max iterations exceeded"
                    )
                    await event_bus.publish(
                        slug,
                        "agent.stopped",
                        {"task_id": task.id, "reason": "max_iterations"},
                    )
                    run_status = "failed"
                    break
            else:
                await event_bus.publish(
                    slug, "agent.stopped", {"task_id": task.id, "reason": "completed"}
                )
        except BaseException:
            run_status = "failed"
            raise
        finally:
            finalize_orchestrator_run(
                run_token,
                project_path=str(self.project.entry.path),
                run_id=run_id,
                status=run_status,
            )
            self.project.set_status("idle")

    async def run_autonomous(self) -> AsyncGenerator[AgentEvent, None]:
        """
        Автономный режим: читает MASTER.md, формирует задачи, выполняет их.
        Возвращает после завершения всех текущих задач.
        """
        yield AgentEvent(
            "thinking", f"Запуск автономного режима для проекта: {self.project.entry.name}"
        )

        # Шаг 1: Проверить очередь
        task = self.project.next_pending_task()

        # Шаг 2: Если задач нет — сгенерировать из MASTER.md
        if not task:
            yield AgentEvent("thinking", "Нет задач в очереди. Анализирую MASTER.md...")
            async for event in self._generate_tasks_from_master():
                yield event
            task = self.project.next_pending_task()

        if not task:
            yield AgentEvent(
                "done", "Задач для выполнения не найдено. Проект в актуальном состоянии."
            )
            return

        # Шаг 3: Выполнить задачу
        async for event in self.run_task(task):
            yield event

        yield AgentEvent("done", f"Задача завершена: {task.title}")

    # ── Internal ──────────────────────────────────────────────────────────────

    # ── Context-building helpers ──────────────────────────────────────────────

    @staticmethod
    def _parse_ref_path(ref: str) -> str | None:
        """Extract the file path from a hybrid ref string (📁 /path | ...)."""
        import re

        m = re.match(r"📁\s*([^|]+?)(?:\s*\||\s*$)", ref)
        return m.group(1).strip() if m else None

    def _render_context_refs(self, refs: list[str], max_lines: int = 200) -> str:
        """Render context_refs as fenced file previews (≤max_lines each)."""
        if not refs:
            return ""
        blocks: list[str] = ["## Файлы задачи (context_refs):"]
        for ref in refs:
            path = self._parse_ref_path(ref)
            if not path:
                blocks.append(f"- {ref} [путь не распознан]")
                continue
            read = self.executor._tool_read_file(path)
            if "error" in read:
                blocks.append(f"**`{path}`** [не найден: {read['error']}]")
                continue
            content = read.get("content", "")
            lines = content.splitlines()
            preview = "\n".join(lines[:max_lines])
            tail = f"\n[... +{len(lines) - max_lines} строк пропущено]" if len(lines) > max_lines else ""
            blocks.append(f"**`{path}`**\n```\n{preview}{tail}\n```")
        return "\n\n".join(blocks)

    def _render_context_refs_compact(self, refs: list[str]) -> str:
        """Render context_refs as a compact path-only list (no file content)."""
        if not refs:
            return ""
        lines = ["## Файлы задачи (context_refs, без превью):"]
        lines.extend(f"- {ref}" for ref in refs)
        return "\n".join(lines)

    def _render_prerequisites(self, task: Task) -> str:
        """Render blocked_by tasks with their result (≤500 chars each)."""
        if not task.blocked_by:
            return ""
        task_map = {t.id: t for t in self.project._load_tasks()}
        blocks: list[str] = ["## Prerequisites (что завершено до этой задачи):"]
        for prereq_id in task.blocked_by:
            prereq = task_map.get(prereq_id)
            if prereq is None:
                blocks.append(f"⚠️ [{prereq_id}] (задача не найдена)")
                continue
            icon = "✅" if prereq.status == TaskStatus.DONE else "🔄"
            result = prereq.result or "(нет результата)"
            if len(result) > 500:
                result = result[:500] + "…"
            blocks.append(f"{icon} **[{prereq.id}] {prereq.title}** [{prereq.status}]\n{result}")
        return "\n\n".join(blocks)

    @staticmethod
    def _extract_relevant_master_sections(master_content: str, task: Task) -> str:
        """Return only MASTER.md sections relevant to the task, not a blind [:3000] slice."""
        import re

        # Split into (header, body) pairs
        parts = re.split(r"(^#{1,3} .+$)", master_content, flags=re.MULTILINE)
        sections: list[tuple[str, str]] = []
        header, body = "(preface)", parts[0]
        for i in range(1, len(parts), 2):
            sections.append((header, body))
            header = parts[i]
            body = parts[i + 1] if i + 1 < len(parts) else ""
        sections.append((header, body))

        task_text = (task.title + " " + task.description).lower()
        task_paths = set()
        for ref in task.context_refs:
            m = re.match(r"📁\s*([^|]+?)(?:\s*\||\s*$)", ref)
            if m:
                task_paths.add(m.group(1).strip().lower())

        always = {"executive", "summary", "context map", "навигат", "цель", "overview"}
        hash_kw = {"hash", "хэш", "sha:", "verify", "stale", "broken", "validation"}
        action_kw = {"commit", "changelog", "docker", "lint", "test"}

        selected: list[str] = []
        omitted = 0
        for hdr, bdy in sections:
            h_lo = hdr.lower()
            combined = (hdr + bdy).lower()

            if any(kw in h_lo for kw in always):
                selected.append(hdr + bdy)
                continue
            if task_paths and any(p in combined for p in task_paths):
                selected.append(hdr + bdy)
                continue
            if any(kw in task_text for kw in hash_kw) and "validation" in h_lo:
                selected.append(hdr + bdy)
                continue
            if any(kw in task_text for kw in action_kw) and "quick actions" in h_lo:
                selected.append(hdr + bdy)
                continue
            omitted += 1

        if omitted:
            selected.append(f"[... {omitted} секций MASTER.md пропущено как нерелевантные]")
        return "".join(selected)

    # ── Story rendering (A3) ─────────────────────────────────────────────────

    def _render_story(self, story_id: str) -> str:
        """Render linked story with acceptance criteria as a prompt block."""
        data = self.executor._tool_story_get(story_id)
        if "error" in data:
            return f"[Story {story_id}: {data['error']}]"
        criteria = data.get("acceptance_criteria", [])
        lines = [
            f"## User Story [{data['story_id']}]: "
            f"{data['persona']} — {data['narrative']}",
            "### Acceptance Criteria:",
        ]
        for c in criteria:
            mark = "x" if c.get("met") else " "
            lines.append(f"- [{mark}] {c['criterion']}")
        return "\n".join(lines)

    # ── Budget-aware joining (A5) ─────────────────────────────────────────────

    def _budget_join(
        self,
        header: str,
        optional: list[tuple[str, str]],
        footer: str,
        budget_chars: int,
    ) -> str:
        """Join blocks respecting a character budget.

        ``optional`` is a list of ``(name, content)`` in *priority order*
        (first = highest priority). Blocks that would exceed the budget are
        replaced with a one-line skip marker.
        """
        used = len(header) + len(footer) + 2  # two separators
        parts = [header]
        for name, block in optional:
            cost = len(block) + 2  # "\n\n" separator
            if used + cost <= budget_chars:
                parts.append(block)
                used += cost
            else:
                avail = max(0, budget_chars - used - 60)
                parts.append(
                    f"[блок «{name}» пропущен — "
                    f"бюджет {budget_chars // 1000}K символов, "
                    f"осталось ≈{avail // 4} токенов]"
                )
        parts.append(footer)
        return "\n\n".join(parts)

    # ── Message builder ───────────────────────────────────────────────────────

    def _build_messages(
        self, task: Task, context_mode: str = "full"
    ) -> list[dict[str, Any]]:
        """Построить начальные сообщения для задачи.

        context_mode:
          - "full":      MASTER.md (релевантные секции) + context_refs превью + prerequisites + story
          - "no_master": context_refs превью + prerequisites + story (без MASTER.md)
          - "refs_only": только пути context_refs (без превью, без MASTER, без story)
          - "minimal":   только title + description + acceptance
        """
        _FOOTER = (
            "Выполни задачу, используя доступные инструменты. "
            "В конце завершения обнови хэши и добавь запись в changelog MASTER.md."
        )
        budget_chars = self.config.max_context_tokens * 4

        header = f"## Задача [{task.id}]: {task.title}\n\n{task.description}"
        if task.acceptance:
            header += f"\n\n**Критерий приёмки:** {task.acceptance}"

        if context_mode == "minimal":
            return [{"role": "user", "content": f"{header}\n\n{_FOOTER}"}]

        if context_mode == "refs_only":
            optional: list[tuple[str, str]] = []
            prereqs = self._render_prerequisites(task)
            if prereqs:
                optional.append(("prerequisites", prereqs))
            refs = self._render_context_refs_compact(task.context_refs)
            if refs:
                optional.append(("context_refs", refs))
            return [{"role": "user", "content": self._budget_join(header, optional, _FOOTER, budget_chars)}]

        # full / no_master — build optional blocks in priority order
        optional = []
        prereqs = self._render_prerequisites(task)
        if prereqs:
            optional.append(("prerequisites", prereqs))
        refs = self._render_context_refs(task.context_refs)
        if refs:
            optional.append(("context_refs", refs))
        if context_mode == "full":
            master_content = self.project.read_master() or "MASTER.md не найден."
            master_block = self._extract_relevant_master_sections(master_content, task)
            optional.append(("MASTER.md", f"## MASTER.md (L0)\n\n```markdown\n{master_block}\n```"))
        if task.story_id:
            optional.append(("story", self._render_story(task.story_id)))

        return [{"role": "user", "content": self._budget_join(header, optional, _FOOTER, budget_chars)}]

    async def _agent_loop(
        self, messages: list[dict[str, Any]], task: Task
    ) -> AsyncGenerator[AgentEvent, None]:
        """Основной цикл агент ↔ LLM ↔ инструменты.

        При ошибке context_length_exceeded: повтор с урезанным контекстом.
        """
        context_retry = 0
        # Флаг: задача выполняется в degraded mode (контекст урезан)
        degraded_mode = False

        while True:
            if self.executor.is_blocked:
                yield AgentEvent("blocked", self.executor._blocked_question)
                return

            # Запрос к LLM с retry
            try:
                # PCA-002: подмешиваем триггерные SKILL.md в system-prompt
                # на каждой итерации. Матчер чистый и кеширован, so this is
                # cheap; в случае пустого вывода возвращается base SYSTEM_PROMPT.
                from cod_doc.agent.skill_matcher import (
                    compose_system_prompt,
                    select_skills,
                )

                _system_text = compose_system_prompt(
                    SYSTEM_PROMPT, select_skills(task)
                )
                llm_messages = [{"role": "system", "content": _system_text}, *messages]
                # G1: log token budget before sending
                approx_tokens = sum(len(str(m.get("content", ""))) for m in llm_messages) // 4
                logger.debug(
                    "[task %s] retry=%d context≈%d t messages=%d",
                    task.id, context_retry, approx_tokens, len(llm_messages),
                )
                if approx_tokens > self.config.max_context_tokens * 2:
                    logger.warning(
                        "[task %s] context ≈%d t exceeds 2× budget (%d t). "
                        "Consider decomposing this task.",
                        task.id, approx_tokens, self.config.max_context_tokens,
                    )
                response = await with_retry(
                    lambda msgs=llm_messages: self.client.chat.completions.create(  # type: ignore[call-overload,misc]
                        model=self.config.model,
                        messages=msgs,
                        tools=TOOL_DEFINITIONS,
                        tool_choice="auto",
                        max_tokens=self.config.max_tokens,
                    )
                )
            except ContextLengthExceededError as e:
                if context_retry < _MAX_CONTEXT_RETRIES:
                    context_retry += 1
                    degraded_mode = True

                    # retry 1: drop MASTER.md, keep context_refs previews + prerequisites
                    if context_retry == 1:
                        mode = "no_master"
                        hint = "убрал MASTER.md, сохранил context_refs"
                    # retry 2: drop file previews, keep ref paths only
                    elif context_retry == 2:
                        mode = "refs_only"
                        hint = "убрал превью файлов, оставил пути refs"
                    # retry 3: minimal — only task fields
                    else:
                        mode = "minimal"
                        hint = "оставил только title + description + acceptance"

                    logger.warning(
                        f"[task {task.id}] context_length_exceeded, "
                        f"retry {context_retry}/{_MAX_CONTEXT_RETRIES}: {hint}"
                    )
                    yield AgentEvent(
                        "thinking",
                        f"⚠️ Контекст превышен. "
                        f"Retry {context_retry}/{_MAX_CONTEXT_RETRIES} ({hint}).",
                    )
                    messages = self._build_messages(task, mode)
                    continue

                # Исчерпаны retry-попытки
                logger.error(
                    f"[task {task.id}] context_length_exceeded исчерпаны все "
                    f"{_MAX_CONTEXT_RETRIES} retry-попыток"
                )
                yield AgentEvent("error", str(e))
                self.project.update_task(
                    task.id,
                    status=TaskStatus.FAILED,
                    result=f"Context overflow после {_MAX_CONTEXT_RETRIES} retry-попыток: {e}",
                )
                return
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
                    tasks_list = [t for t in self.project._load_tasks() if t.id == task.id]
                    if tasks_list and tasks_list[0].status == TaskStatus.IN_PROGRESS:
                        result = content[:500]
                        if degraded_mode:
                            result = f"[degraded-{context_retry}] " + result
                        self.project.update_task(
                            task.id, status=TaskStatus.DONE, result=result
                        )
                return

            # Обработка вызовов инструментов
            tool_results = []
            for tc in msg.tool_calls:
                if tc.type != "function":
                    continue
                fn_name = tc.function.name
                fn_args = tc.function.arguments

                yield AgentEvent("tool_call", {"name": fn_name, "args": fn_args})

                # ask_human — единственный инструмент с async-путём
                if fn_name == "ask_human" and self._async_on_ask_human is not None:
                    args: dict[str, Any] = (
                        json.loads(fn_args) if isinstance(fn_args, str) else fn_args
                    )
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
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

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
            llm_msgs = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
            allowed = {
                t["function"]["name"]
                for t in TOOL_DEFINITIONS
                if t["function"]["name"] in ("create_task", "get_project_status")
            }
            tools_subset = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in allowed]
            response = await with_retry(
                lambda: self.client.chat.completions.create(  # type: ignore[call-overload]
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
                if tc.type != "function":
                    continue
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
            if not entry.enabled or not entry.daemon_enabled:
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
