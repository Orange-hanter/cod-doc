"""Экран запуска агента — стриминг вывода в реальном времени."""

from __future__ import annotations

import asyncio
from datetime import datetime

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Label, RichLog, Static

from cod_doc.agent.orchestrator import AgentEvent, Orchestrator
from cod_doc.config import Config
from cod_doc.core.project import Project, Task, TaskStatus
from cod_doc.logging_config import get_logger


log = get_logger("tui.agent_run")


EVENT_STYLES = {
    "thinking": ("dim italic", "💭"),
    "tool_call": ("bold cyan", "🔧"),
    "tool_result": ("dim green", "📤"),
    "message": ("white", "🤖"),
    "done": ("bold green", "✅"),
    "error": ("bold red", "❌"),
    "blocked": ("bold yellow", "⚠️"),
}


class AgentRunScreen(Screen):
    """Экран выполнения задачи агентом."""

    BINDINGS = [
        Binding("escape", "stop_agent", "Остановить", show=True),
        Binding("c", "clear_log", "Очистить лог", show=True),
    ]

    def __init__(self, project: Project, config: Config) -> None:
        super().__init__()
        self.project = project
        self.config = config
        self._running = False
        self._stop_event = asyncio.Event()
        self._answer_queue: asyncio.Queue[str] = asyncio.Queue()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="agent-layout"):
            yield Static(
                f"🤖 Агент: {self.project.entry.name}", id="agent-header", classes="panel-title"
            )
            # Опции
            with Vertical(id="agent-options"):
                yield Checkbox("Авто-коммит после задачи", id="cb-auto-commit", value=self.project.entry.auto_commit)
                yield Checkbox("Автономный режим (генерировать задачи из MASTER.md)", id="cb-autonomous", value=True)

            # Панель управления
            with Vertical(id="agent-controls"):
                yield Button("▶ Запустить", id="btn-start", variant="success")
                yield Button("⏹ Остановить", id="btn-stop", disabled=True, variant="error")

            # Лог вывода
            yield RichLog(id="agent-log", highlight=True, markup=True, wrap=True)

            # Блок ввода ответа (показывается когда агент ждёт)
            with Vertical(id="human-input-block", classes="hidden"):
                yield Label("❓ Агент ожидает вашего ответа:", id="question-label")
                from textual.widgets import Input
                yield Input(placeholder="Введите ответ...", id="human-answer")
                yield Button("Отправить →", id="btn-send-answer", variant="primary")

        yield Footer()

    def on_mount(self) -> None:
        log.debug(
            "Agent screen mounted",
            extra={"event_type": "agent_mount", "project": self.project.entry.name},
        )
        self._log("Готов к запуску. Нажмите ▶ Запустить.")

    def _log(self, message: str, style: str = "white", prefix: str = "") -> None:
        rich_log = self.query_one("#agent-log", RichLog)
        ts = datetime.now().strftime("%H:%M:%S")
        if prefix:
            rich_log.write(f"[dim]{ts}[/dim] {prefix} [{style}]{message}[/{style}]")
        else:
            rich_log.write(f"[dim]{ts}[/dim] [{style}]{message}[/{style}]")

    def _log_event(self, event: AgentEvent) -> None:
        log.debug(
            f"Agent event: {event.type}",
            extra={"event_type": event.type, "project": self.project.entry.name},
        )
        style, icon = EVENT_STYLES.get(event.type, ("white", "•"))
        if event.type == "tool_call":
            data = event.data if isinstance(event.data, dict) else {}
            self._log(f"{data.get('name', '')}({data.get('args', '')[:100]})", style, icon)
        elif event.type == "tool_result":
            data = event.data if isinstance(event.data, dict) else {}
            result_str = str(data.get("result", ""))[:200]
            self._log(f"  → {result_str}", style, "")
        elif event.type == "blocked":
            self._show_human_input(str(event.data))
        else:
            self._log(str(event.data)[:500], style, icon)

    def _show_human_input(self, question: str) -> None:
        log.debug(
            "Agent requested human input",
            extra={"event_type": "agent_blocked", "project": self.project.entry.name},
        )
        block = self.query_one("#human-input-block")
        block.remove_class("hidden")
        self.query_one("#question-label", Label).update(f"❓ {question}")
        from textual.widgets import Input
        self.query_one("#human-answer", Input).focus()

    def _hide_human_input(self) -> None:
        log.debug(
            "Human input block hidden",
            extra={"event_type": "agent_input_hidden", "project": self.project.entry.name},
        )
        self.query_one("#human-input-block").add_class("hidden")

    async def _async_on_ask_human(self, question: str, context: str) -> str:
        """Async-callback: показать вопрос и await ответа из очереди.
        Вызывается напрямую из _agent_loop который уже в asyncio event loop.
        """
        self._answer_queue = asyncio.Queue()
        self._show_human_input(question)
        return await self._answer_queue.get()

    # ── Button handlers ───────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        log.debug(
            f"Button pressed: {event.button.id}",
            extra={"event_type": "agent_button", "project": self.project.entry.name},
        )
        if event.button.id == "btn-start":
            self._start_agent()
        elif event.button.id == "btn-stop":
            self._stop_event.set()
            self._log("Остановка агента...", "yellow", "⏹")
        elif event.button.id == "btn-send-answer":
            from textual.widgets import Input
            answer = self.query_one("#human-answer", Input).value.strip()
            if answer:
                self._answer_queue.put_nowait(answer)
                self._hide_human_input()
                self._log(f"Ответ отправлен: {answer}", "green", "👤")

    @work(exclusive=True, thread=False)
    async def _start_agent(self) -> None:
        if self._running:
            log.debug(
                "Start ignored because agent is already running",
                extra={"event_type": "agent_duplicate_start", "project": self.project.entry.name},
            )
            return
        self._running = True
        self._stop_event.clear()
        self.query_one("#btn-start", Button).disabled = True
        self.query_one("#btn-stop", Button).disabled = False

        auto_commit = self.query_one("#cb-auto-commit", Checkbox).value
        autonomous = self.query_one("#cb-autonomous", Checkbox).value
        log.debug(
            f"Starting agent: autonomous={autonomous} auto_commit={auto_commit}",
            extra={"event_type": "agent_start", "project": self.project.entry.name},
        )

        # Временно обновить auto_commit
        cfg = self.config
        cfg.auto_commit = auto_commit

        orch = Orchestrator(self.project, cfg, async_on_ask_human=self._async_on_ask_human)

        try:
            if autonomous:
                gen = orch.run_autonomous()
            else:
                task = self.project.next_pending_task()
                if not task:
                    log.debug(
                        "No pending task available",
                        extra={"event_type": "agent_no_task", "project": self.project.entry.name},
                    )
                    self._log("Нет задач в очереди. Добавьте задачу через дашборд.", "yellow", "⚠️")
                    return
                gen = orch.run_task(task)

            async for event in gen:
                if self._stop_event.is_set():
                    log.debug(
                        "Stop event detected during stream",
                        extra={"event_type": "agent_stop_requested", "project": self.project.entry.name},
                    )
                    break
                self._log_event(event)

        except Exception as e:
            log.exception(
                "Agent run failed",
                extra={"event_type": "agent_error", "project": self.project.entry.name},
            )
            self._log(str(e), "red", "❌")
        finally:
            log.debug(
                "Agent run finished",
                extra={"event_type": "agent_finish", "project": self.project.entry.name},
            )
            self._running = False
            self.query_one("#btn-start", Button).disabled = False
            self.query_one("#btn-stop", Button).disabled = True

    def action_stop_agent(self) -> None:
        log.debug(
            "Agent screen stop action",
            extra={"event_type": "agent_action_stop", "project": self.project.entry.name},
        )
        self._stop_event.set()
        self.app.pop_screen()

    def action_clear_log(self) -> None:
        log.debug(
            "Agent log cleared",
            extra={"event_type": "agent_clear_log", "project": self.project.entry.name},
        )
        self.query_one("#agent-log", RichLog).clear()
