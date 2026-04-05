"""Мастер первоначальной настройки COD-DOC."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Markdown, Select, Static

from cod_doc.config import Config, ProjectEntry

WELCOME_TEXT = """
# 🧭 COD-DOC — Мастер настройки

**Context Orchestrator for Documentation** — автономный агент управления документацией.

Он работает с **вашими репозиториями** — читает и обновляет MASTER.md,
генерирует спецификации, отслеживает задачи.

Нужно настроить:
1. API-ключ OpenRouter
2. Модель LLM
3. Добавить первый проект
"""

MODELS = [
    ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6 (рекомендуется)"),
    ("anthropic/claude-opus-4-6", "Claude Opus 4.6 (мощнее, дороже)"),
    ("anthropic/claude-haiku-4-5", "Claude Haiku 4.5 (быстрее, дешевле)"),
    ("openai/gpt-4o", "GPT-4o"),
    ("openai/gpt-4o-mini", "GPT-4o Mini"),
    ("meta-llama/llama-3.1-70b-instruct", "Llama 3.1 70B"),
    ("google/gemini-pro-1.5", "Gemini Pro 1.5"),
]


class WizardScreen(Screen):
    """Экран первоначальной настройки."""

    BINDINGS = [Binding("escape", "app.quit", "Выход")]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self._step = 0  # 0=welcome, 1=api, 2=project, 3=done

    def compose(self) -> ComposeResult:
        yield Static("🧭 COD-DOC Setup Wizard", id="wizard-title")
        with Vertical(id="wizard-body"):
            yield Markdown(WELCOME_TEXT, id="welcome-md")
            # Шаг 1: API Key
            with Vertical(id="step-api", classes="wizard-step hidden"):
                yield Label("🔑 OpenRouter API Key", classes="field-label")
                yield Label(
                    "Получить ключ: https://openrouter.ai/keys",
                    classes="hint",
                )
                yield Input(
                    placeholder="sk-or-v1-...",
                    password=True,
                    id="input-api-key",
                )
                yield Label("🤖 Модель LLM", classes="field-label")
                yield Select(
                    [(label, value) for value, label in MODELS],
                    id="select-model",
                    value=MODELS[0][0],
                )
                yield Label("⚙️ Дополнительно", classes="field-label")
                yield Input(
                    placeholder="https://openrouter.ai/api/v1",
                    id="input-base-url",
                    value="https://openrouter.ai/api/v1",
                )
            # Шаг 2: Первый проект
            with Vertical(id="step-project", classes="wizard-step hidden"):
                yield Label("📁 Путь к репозиторию проекта", classes="field-label")
                yield Input(
                    placeholder="/home/user/my-project или ~/projects/my-app",
                    id="input-project-path",
                )
                yield Label("📛 Имя проекта", classes="field-label")
                yield Input(placeholder="my-project", id="input-project-name")
                yield Label("📄 Путь к MASTER.md (относительно корня)", classes="field-label")
                yield Input(placeholder="MASTER.md", id="input-master-md", value="MASTER.md")
                yield Static(
                    "COD-DOC создаст MASTER.md если его нет, "
                    "и инициализирует .cod-doc/ в вашем репозитории.",
                    classes="hint",
                )
            # Шаг 3: Готово
            with Vertical(id="step-done", classes="wizard-step hidden"):
                yield Markdown("## ✅ Настройка завершена!\n\nCOD-DOC готов к работе.", id="done-md")

        # Кнопки навигации
        with Center(id="wizard-nav"):
            yield Button("← Назад", id="btn-back", variant="default", disabled=True)
            yield Button("Начать →", id="btn-next", variant="primary")

    def on_mount(self) -> None:
        self._show_step(0)

    def _show_step(self, step: int) -> None:
        self._step = step
        # Скрыть все шаги
        for s in self.query(".wizard-step"):
            s.add_class("hidden")
        # Показать welcome или нужный шаг
        step_map = {0: "#welcome-md", 1: "#step-api", 2: "#step-project", 3: "#step-done"}
        if step in step_map:
            self.query_one(step_map[step]).remove_class("hidden")

        back_btn = self.query_one("#btn-back", Button)
        next_btn = self.query_one("#btn-next", Button)
        back_btn.disabled = step == 0
        next_btn.label = "Готово" if step == 3 else ("Начать →" if step == 0 else "Далее →")

    @on(Button.Pressed, "#btn-next")
    def _next(self) -> None:
        if self._step == 3:
            self._finish()
            return
        if self._step == 1:
            if not self._save_api_step():
                return
        elif self._step == 2:
            if not self._save_project_step():
                return
        self._show_step(self._step + 1)

    @on(Button.Pressed, "#btn-back")
    def _back(self) -> None:
        if self._step > 0:
            self._show_step(self._step - 1)

    def _save_api_step(self) -> bool:
        key = self.query_one("#input-api-key", Input).value.strip()
        if not key:
            self.notify("Введите API-ключ", severity="error")
            return False
        model_select = self.query_one("#select-model", Select)
        model = str(model_select.value) if model_select.value else MODELS[0][0]
        base_url = self.query_one("#input-base-url", Input).value.strip()
        self.config.api_key = key
        self.config.model = model
        self.config.base_url = base_url or "https://openrouter.ai/api/v1"
        self.config.save()
        return True

    def _save_project_step(self) -> bool:
        path_str = self.query_one("#input-project-path", Input).value.strip()
        name = self.query_one("#input-project-name", Input).value.strip()
        master_md = self.query_one("#input-master-md", Input).value.strip() or "MASTER.md"

        if not path_str:
            self.notify("Укажите путь к проекту", severity="error")
            return False
        if not name:
            self.notify("Введите имя проекта", severity="error")
            return False

        path = Path(path_str).expanduser()
        if not path.exists():
            self.notify(f"Директория не найдена: {path}", severity="error")
            return False

        entry = ProjectEntry(name=name, path=str(path), master_md=master_md)
        self.config.add_project(entry)

        # Инициализировать проект
        from cod_doc.core.project import Project
        proj = Project(entry)
        proj.init()
        return True

    def _finish(self) -> None:
        from cod_doc.tui.screens.dashboard import DashboardScreen
        self.app.switch_screen(DashboardScreen(self.config))
