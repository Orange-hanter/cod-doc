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
        self._log("Готов к запуску. Нажмите ▶ Запустить.")

    def _log(self, message: str, style: str = "white", prefix: str = "") -> None:
        log = self.query_one("#agent-log", RichLog)
        ts = datetime.now().strftime("%H:%M:%S")
        if prefix:
            log.write(f"[dim]{ts}[/dim] {prefix} [{style}]{message}[/{style}]")
        else:
            log.write(f"[dim]{ts}[/dim] [{style}]{message}[/{style}]")

    def _log_event(self, event: AgentEvent) -> None:
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
        block = self.query_one("#human-input-block")
        block.remove_class("hidden")
        self.query_one("#question-label", Label).update(f"❓ {question}")
        from textual.widgets import Input
        self.query_one("#human-answer", Input).focus()

    def _hide_human_input(self) -> None:
        self.query_one("#human-input-block").add_class("hidden")

    def _on_ask_human(self, question: str, context: str) -> str:
        """Callback для агента: показать вопрос, дождаться ответа через очередь."""
        self._answer_queue = asyncio.Queue()
        self.call_from_thread(self._show_human_input, question)
        # Синхронное ожидание (запускаем в thread executor)
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self._answer_queue.get())

    # ── Button handlers ───────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
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
            return
        self._running = True
        self._stop_event.clear()
        self.query_one("#btn-start", Button).disabled = True
        self.query_one("#btn-stop", Button).disabled = False

        auto_commit = self.query_one("#cb-auto-commit", Checkbox).value
        autonomous = self.query_one("#cb-autonomous", Checkbox).value

        # Временно обновить auto_commit
        cfg = self.config
        cfg.auto_commit = auto_commit

        orch = Orchestrator(self.project, cfg, on_ask_human=self._on_ask_human)

        try:
            if autonomous:
                gen = orch.run_autonomous()
            else:
                task = self.project.next_pending_task()
                if not task:
                    self._log("Нет задач в очереди. Добавьте задачу через дашборд.", "yellow", "⚠️")
                    return
                gen = orch.run_task(task)

            async for event in gen:
                if self._stop_event.is_set():
                    break
                self._log_event(event)

        except Exception as e:
            self._log(str(e), "red", "❌")
        finally:
            self._running = False
            self.query_one("#btn-start", Button).disabled = False
            self.query_one("#btn-stop", Button).disabled = True

    def action_stop_agent(self) -> None:
        self._stop_event.set()
        self.app.pop_screen()

    def action_clear_log(self) -> None:
        self.query_one("#agent-log", RichLog).clear()
