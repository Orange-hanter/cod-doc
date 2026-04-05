"""Интерактивный мастер первоначальной настройки COD-DOC."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Input, Label, RadioButton, RadioSet, Static

from cod_doc.config import Config, ProjectEntry
from cod_doc.logging_config import get_logger

log = get_logger("tui.wizard")

# (model_id, display_label)
MODELS: list[tuple[str, str]] = [
    ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6  ⭐ рекомендуется"),
    ("anthropic/claude-opus-4-6",   "Claude Opus 4.6    💪 мощнее, дороже"),
    ("anthropic/claude-haiku-4-5",  "Claude Haiku 4.5   ⚡ быстрее, дешевле"),
    ("openai/gpt-4o",               "GPT-4o"),
    ("openai/gpt-4o-mini",          "GPT-4o Mini"),
    ("meta-llama/llama-3.1-70b-instruct", "Llama 3.1 70B (open-source)"),
    ("google/gemini-pro-1.5",       "Gemini Pro 1.5"),
]

STEPS = ["Добро пожаловать", "API & модель", "Проект", "Готово"]


class _StepBar(Static):
    """Горизонтальный индикатор шагов."""

    DEFAULT_CSS = """
    _StepBar {
        height: 3;
        content-align: center middle;
        background: $surface-darken-1;
        padding: 0 2;
    }
    """

    def __init__(self, steps: list[str], current: int = 0) -> None:
        super().__init__("")
        self._steps = steps
        self._current = current
        self._render()

    def update_step(self, current: int) -> None:
        self._current = current
        self._render()

    def _render(self) -> None:
        parts: list[str] = []
        for i, name in enumerate(self._steps):
            if i < self._current:
                parts.append(f"[dim]✓ {name}[/dim]")
            elif i == self._current:
                parts.append(f"[bold $accent]● {name}[/bold $accent]")
            else:
                parts.append(f"[dim]○ {name}[/dim]")
            if i < len(self._steps) - 1:
                parts.append("[dim] → [/dim]")
        self.update("".join(parts))


class WizardScreen(Screen):
    """Интерактивный экран первоначальной настройки."""

    BINDINGS = [
        Binding("escape", "quit_wizard", "Выход"),
        Binding("enter", "next_step", "Далее", show=False),
    ]

    DEFAULT_CSS = """
    WizardScreen {
        align: center middle;
    }

    #wizard-frame {
        width: 80;
        height: auto;
        max-height: 90vh;
        border: double $primary;
        background: $surface;
        padding: 0;
    }

    #wizard-title-bar {
        background: $primary;
        color: $text;
        text-style: bold;
        padding: 1 2;
        height: 3;
        content-align: center middle;
    }

    #wizard-content {
        padding: 2 4;
        height: auto;
    }

    .step-heading {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    .field-label {
        color: $text-muted;
        margin-top: 1;
    }

    .hint {
        color: $text-muted;
        text-style: italic;
        padding: 0 0 1 0;
    }

    .error-label {
        color: $error;
        text-style: bold;
        height: 1;
    }

    #wizard-nav {
        height: 5;
        align: center middle;
        padding: 1 2;
        border-top: solid $surface-darken-2;
    }

    #wizard-nav Button { margin: 0 1; min-width: 18; }

    RadioSet { height: auto; margin: 1 0; }
    RadioButton { height: 1; }

    #welcome-art {
        color: $accent;
        text-style: bold;
        content-align: center middle;
        height: 5;
    }

    #welcome-body { margin: 1 0; }

    #done-art {
        color: $success;
        text-style: bold;
        content-align: center middle;
        height: 3;
    }
    """

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self._step = 0
        self._error_msg = ""

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard-frame"):
            yield Static("🧭  COD-DOC  Setup Wizard", id="wizard-title-bar")
            yield _StepBar(STEPS, current=0)
            with ScrollableContainer(id="wizard-content"):
                # Шаг 0: приветствие
                with Vertical(id="step-0"):
                    yield Static("COD-DOC", id="welcome-art")
                    yield Static(
                        "[bold]Context Orchestrator for Documentation[/bold]\n"
                        "Автономный агент управления документацией.\n\n"
                        "Он работает с вашими репозиториями — читает и обновляет [cyan]MASTER.md[/cyan],\n"
                        "генерирует спецификации, отслеживает задачи.\n\n"
                        "Настройка займёт меньше минуты:\n"
                        "  [dim]1.[/dim] API-ключ OpenRouter и модель LLM\n"
                        "  [dim]2.[/dim] Путь к первому проекту\n"
                        "  [dim]3.[/dim] Готово — открывается дашборд",
                        id="welcome-body",
                    )

                # Шаг 1: API
                with Vertical(id="step-1", classes="hidden"):
                    yield Static("🔑  API & Модель", classes="step-heading")
                    yield Label("OpenRouter API Key", classes="field-label")
                    yield Label("Получить ключ: openrouter.ai/keys", classes="hint")
                    yield Input(
                        placeholder="sk-or-v1-...",
                        password=True,
                        id="input-api-key",
                    )
                    yield Static("", id="err-api-key", classes="error-label")

                    yield Label("Модель LLM", classes="field-label")
                    with RadioSet(id="model-set"):
                        for model_id, label in MODELS:
                            yield RadioButton(label, id=f"model-{model_id}")

                    yield Label("Base URL  [dim](необязательно)[/dim]", classes="field-label")
                    yield Input(
                        value="https://openrouter.ai/api/v1",
                        id="input-base-url",
                    )

                # Шаг 2: Проект
                with Vertical(id="step-2", classes="hidden"):
                    yield Static("📁  Первый проект", classes="step-heading")
                    yield Label(
                        "Можно пропустить — добавите проект позже через дашборд.",
                        classes="hint",
                    )
                    yield Checkbox("Пропустить этот шаг", id="cb-skip-project", value=False)

                    with Vertical(id="project-fields"):
                        yield Label("Путь к репозиторию", classes="field-label")
                        yield Input(
                            placeholder=str(Path.cwd()),
                            value=str(Path.cwd()),
                            id="input-project-path",
                        )
                        yield Static("", id="err-project-path", classes="error-label")

                        yield Label("Имя проекта", classes="field-label")
                        yield Input(
                            placeholder=Path.cwd().name,
                            value=Path.cwd().name,
                            id="input-project-name",
                        )
                        yield Static("", id="err-project-name", classes="error-label")

                        yield Label("Путь к MASTER.md  [dim](от корня проекта)[/dim]", classes="field-label")
                        yield Input(value="MASTER.md", id="input-master-md")

                        yield Static(
                            "MASTER.md будет создан автоматически если не найден.",
                            classes="hint",
                        )

                # Шаг 3: Готово
                with Vertical(id="step-3", classes="hidden"):
                    yield Static("✅  Настройка завершена!", id="done-art")
                    yield Static("", id="done-summary")

            with Horizontal(id="wizard-nav"):
                yield Button("← Назад", id="btn-back", variant="default", disabled=True)
                yield Button("Начать →", id="btn-next", variant="primary")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        log.debug("Wizard mounted", extra={"event_type": "wizard_mount"})
        # Выбрать первую модель по умолчанию
        self.query_one(f"#model-{MODELS[0][0]}", RadioButton).value = True
        # Заполнить поле API-ключа если уже есть
        if self.config.api_key:
            self.query_one("#input-api-key", Input).value = self.config.api_key
        # Модель из конфига
        for model_id, _ in MODELS:
            if model_id == self.config.model:
                try:
                    self.query_one(f"#model-{model_id}", RadioButton).value = True
                except Exception:
                    pass
                break
        self._show_step(0)

    # ── Skip project checkbox ─────────────────────────────────────────────────

    @on(Checkbox.Changed, "#cb-skip-project")
    def _toggle_project_fields(self, event: Checkbox.Changed) -> None:
        fields = self.query_one("#project-fields")
        if event.value:
            fields.add_class("hidden")
        else:
            fields.remove_class("hidden")

    # ── Navigation ────────────────────────────────────────────────────────────

    def _show_step(self, step: int) -> None:
        self._step = step
        log.debug(f"Wizard step -> {step}", extra={"event_type": "wizard_step"})

        for i in range(4):
            widget = self.query_one(f"#step-{i}")
            if i == step:
                widget.remove_class("hidden")
            else:
                widget.add_class("hidden")

        self.query_one(_StepBar).update_step(step)

        back_btn = self.query_one("#btn-back", Button)
        next_btn = self.query_one("#btn-next", Button)
        back_btn.disabled = step == 0
        if step == 3:
            next_btn.label = "Открыть дашборд →"
        elif step == 0:
            next_btn.label = "Начать →"
        else:
            next_btn.label = "Далее →"

        # Фокус на первое поле ввода текущего шага
        self._focus_first_input(step)

    def _focus_first_input(self, step: int) -> None:
        targets = {
            1: "#input-api-key",
            2: "#input-project-path",
        }
        if step in targets:
            try:
                self.query_one(targets[step], Input).focus()
            except Exception:
                pass

    @on(Button.Pressed, "#btn-next")
    def action_next_step(self, event: Button.Pressed | None = None) -> None:
        log.debug("Next pressed", extra={"event_type": "wizard_next", "task_id": str(self._step)})
        if self._step == 3:
            self._finish()
            return
        if self._step == 1 and not self._validate_and_save_api():
            return
        if self._step == 2 and not self._validate_and_save_project():
            return
        self._show_step(self._step + 1)
        if self._step == 3:
            self._build_done_summary()

    @on(Button.Pressed, "#btn-back")
    def _on_back(self, event: Button.Pressed) -> None:
        log.debug("Back pressed", extra={"event_type": "wizard_back", "task_id": str(self._step)})
        if self._step > 0:
            self._show_step(self._step - 1)

    def action_quit_wizard(self) -> None:
        self.app.exit()

    # ── Validation & saving ───────────────────────────────────────────────────

    def _set_error(self, widget_id: str, msg: str) -> None:
        """Показать/скрыть инлайн-сообщение об ошибке."""
        try:
            self.query_one(f"#err-{widget_id}", Static).update(msg)
        except Exception:
            pass

    def _validate_and_save_api(self) -> bool:
        key = self.query_one("#input-api-key", Input).value.strip()
        if not key:
            self._set_error("api-key", "⚠ Введите API-ключ")
            self.query_one("#input-api-key", Input).focus()
            log.debug("API key empty", extra={"event_type": "wizard_validation"})
            return False
        self._set_error("api-key", "")

        # Определить выбранную модель
        model = MODELS[0][0]
        for model_id, _ in MODELS:
            try:
                rb = self.query_one(f"#model-{model_id}", RadioButton)
                if rb.value:
                    model = model_id
                    break
            except Exception:
                pass

        base_url = self.query_one("#input-base-url", Input).value.strip()
        self.config.api_key = key
        self.config.model = model
        self.config.base_url = base_url or "https://openrouter.ai/api/v1"
        self.config.save()
        log.debug("API step saved", extra={"event_type": "wizard_save_api", "tool": model})
        return True

    def _validate_and_save_project(self) -> bool:
        skip = self.query_one("#cb-skip-project", Checkbox).value
        if skip:
            log.debug("Project step skipped", extra={"event_type": "wizard_skip_project"})
            return True

        path_str = self.query_one("#input-project-path", Input).value.strip()
        name = self.query_one("#input-project-name", Input).value.strip()
        master_md = self.query_one("#input-master-md", Input).value.strip() or "MASTER.md"

        ok = True
        if not path_str:
            self._set_error("project-path", "⚠ Укажите путь")
            ok = False
        else:
            path = Path(path_str).expanduser()
            if not path.exists():
                self._set_error("project-path", f"⚠ Директория не найдена: {path}")
                ok = False
            else:
                self._set_error("project-path", "")

        if not name:
            self._set_error("project-name", "⚠ Введите имя проекта")
            ok = False
        else:
            self._set_error("project-name", "")

        if not ok:
            return False

        path = Path(path_str).expanduser().resolve()
        entry = ProjectEntry(name=name, path=str(path), master_md=master_md)
        self.config.add_project(entry)

        from cod_doc.core.project import Project
        Project(entry).init()
        log.debug("Project step saved", extra={"event_type": "wizard_save_project", "project": name})
        return True

    # ── Done summary ──────────────────────────────────────────────────────────

    def _build_done_summary(self) -> None:
        projects = self.config.list_projects()
        proj_list = "\n".join(f"  • [cyan]{p.name}[/cyan]  {p.path}" for p in projects) or "  [dim](нет проектов)[/dim]"
        summary = (
            f"[bold]Модель:[/bold]   [cyan]{self.config.model}[/cyan]\n"
            f"[bold]Base URL:[/bold] [dim]{self.config.base_url}[/dim]\n\n"
            f"[bold]Проекты:[/bold]\n{proj_list}\n\n"
            "Теперь вы можете запустить агент через дашборд\n"
            "или добавить ещё проекты: [cyan]cod-doc project add[/cyan]"
        )
        self.query_one("#done-summary", Static).update(summary)

    # ── Finish ────────────────────────────────────────────────────────────────

    def _finish(self) -> None:
        from cod_doc.tui.screens.dashboard import DashboardScreen
        log.debug("Wizard finished, switching to dashboard", extra={"event_type": "wizard_finish"})
        self.app.switch_screen(DashboardScreen(self.config))
