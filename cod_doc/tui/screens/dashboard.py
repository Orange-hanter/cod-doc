"""Главный экран TUI — список проектов и управление."""

from __future__ import annotations

from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project, Task, TaskStatus
from cod_doc.logging_config import get_logger
from cod_doc.tui.screens.agent_run import AgentRunScreen


log = get_logger("tui.dashboard")


class ProjectCard(Static):
    """Карточка одного проекта."""

    DEFAULT_CSS = """
    ProjectCard {
        border: solid $primary;
        padding: 1 2;
        margin-bottom: 1;
        height: auto;
    }
    ProjectCard .card-title { text-style: bold; color: $accent; }
    ProjectCard .card-stats { color: $text-muted; }
    """

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project

    def compose(self) -> ComposeResult:
        stats = self.project.stats()
        status_icon = {"idle": "🟢", "running": "🔵", "error": "🔴"}.get(stats["status"], "⚪")
        yield Label(f"{status_icon} {self.project.entry.name}", classes="card-title")
        yield Label(f"📁 {self.project.entry.path}", classes="card-stats")
        yield Label(
            f"Задачи: {stats['pending']} ожидает | {stats['in_progress']} выполняется | "
            f"{stats['done']} готово | {stats['failed']} ошибок",
            classes="card-stats",
        )
        if stats["last_run"]:
            yield Label(f"Последний запуск: {stats['last_run'][:19]}", classes="card-stats")


class AddProjectDialog(Screen):
    """Диалог добавления нового проекта."""

    BINDINGS = [Binding("escape", "dismiss", "Закрыть")]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-body"):
            yield Static("➕ Добавить проект", id="dialog-title")
            yield Label("Путь к репозиторию:", classes="field-label")
            yield Input(placeholder="/path/to/repo", id="proj-path")
            yield Label("Имя проекта:", classes="field-label")
            yield Input(placeholder="my-project", id="proj-name")
            yield Label("Путь к MASTER.md:", classes="field-label")
            yield Input(placeholder="MASTER.md", id="proj-master", value="MASTER.md")
            with Horizontal():
                yield Button("Добавить", id="btn-add", variant="primary")
                yield Button("Отмена", id="btn-cancel", variant="default")

    @on(Button.Pressed, "#btn-cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#btn-add")
    def add(self) -> None:
        path_str = self.query_one("#proj-path", Input).value.strip()
        name = self.query_one("#proj-name", Input).value.strip()
        master = self.query_one("#proj-master", Input).value.strip() or "MASTER.md"

        if not path_str or not name:
            self.notify("Заполните все поля", severity="error")
            return

        path = Path(path_str).expanduser()
        if not path.exists():
            self.notify(f"Директория не найдена: {path}", severity="error")
            return

        entry = ProjectEntry(name=name, path=str(path), master_md=master)
        self.config.add_project(entry)
        proj = Project(entry)
        proj.init()
        self.dismiss(entry)


class AddTaskDialog(Screen):
    """Диалог добавления задачи в проект."""

    BINDINGS = [Binding("escape", "dismiss", "Закрыть")]

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-body"):
            yield Static(f"➕ Новая задача: {self.project.entry.name}", id="dialog-title")
            yield Label("Название:", classes="field-label")
            yield Input(placeholder="Создать спецификацию Auth модуля", id="task-title")
            yield Label("Описание:", classes="field-label")
            yield Input(placeholder="Подробное описание (опционально)", id="task-desc")
            yield Label("Приоритет (1 = высший):", classes="field-label")
            yield Input(placeholder="5", id="task-priority", value="5")
            with Horizontal():
                yield Button("Создать", id="btn-create", variant="primary")
                yield Button("Отмена", id="btn-cancel")

    @on(Button.Pressed, "#btn-cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#btn-create")
    def create(self) -> None:
        title = self.query_one("#task-title", Input).value.strip()
        if not title:
            self.notify("Введите название задачи", severity="error")
            return
        desc = self.query_one("#task-desc", Input).value.strip()
        priority = int(self.query_one("#task-priority", Input).value or "5")
        task = Task(title=title, description=desc, priority=priority)
        self.project.add_task(task)
        self.dismiss(task)


class DashboardScreen(Screen):
    """Главный экран — список проектов."""

    BINDINGS = [
        Binding("a", "add_project", "Добавить проект", show=True),
        Binding("r", "refresh", "Обновить", show=True),
        Binding("s", "settings", "Настройки", show=True),
        Binding("q", "app.quit", "Выход", show=True),
    ]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-layout"):
            # Левая панель — список проектов
            with Vertical(id="projects-panel"):
                yield Static("📋 Проекты", classes="panel-title")
                yield ScrollableContainer(id="projects-list")
                with Horizontal(id="project-actions"):
                    yield Button("➕ Проект", id="btn-add-proj", variant="primary")
                    yield Button("🔄 Refresh", id="btn-refresh")
            # Правая панель — детали и задачи
            with Vertical(id="detail-panel"):
                yield Static("Выберите проект →", id="detail-header", classes="panel-title")
                yield DataTable(id="tasks-table")
                with Horizontal(id="detail-actions"):
                    yield Button("▶ Запустить агент", id="btn-run-agent", disabled=True, variant="success")
                    yield Button("➕ Задача", id="btn-add-task", disabled=True)
                    yield Button("🗑 Удалить проект", id="btn-remove-proj", disabled=True, variant="error")
        yield Footer()

        self._selected_project: Project | None = None

    def on_mount(self) -> None:
        log.debug("Dashboard mounted", extra={"event_type": "dashboard_mount"})
        table = self.query_one("#tasks-table", DataTable)
        table.add_columns("ID", "Приоритет", "Статус", "Название", "Обновлено")
        self._reload_projects()

    def _reload_projects(self) -> None:
        container = self.query_one("#projects-list", ScrollableContainer)
        container.remove_children()
        projects = self.config.list_projects()
        log.debug(
            "Reloading project cards",
            extra={"event_type": "dashboard_reload", "task_id": str(len(projects))},
        )
        for entry in projects:
            proj = Project(entry)
            card = ProjectCard(proj)
            card.can_focus = True
            container.mount(card)

    def _select_project(self, project: Project) -> None:
        self._selected_project = project
        log.debug(
            "Project selected",
            extra={"event_type": "dashboard_select", "project": project.entry.name},
        )
        self.query_one("#detail-header", Static).update(
            f"📁 {project.entry.name}  ·  {project.entry.path}"
        )
        # Обновить таблицу задач
        table = self.query_one("#tasks-table", DataTable)
        table.clear()
        for task in project.get_tasks():
            icons = {
                TaskStatus.PENDING: "🟡",
                TaskStatus.IN_PROGRESS: "🔵",
                TaskStatus.DONE: "🟢",
                TaskStatus.FAILED: "🔴",
                TaskStatus.BLOCKED: "⚠️",
            }
            table.add_row(
                task.id,
                str(task.priority),
                f"{icons.get(task.status, '⚪')} {task.status.value}",
                task.title[:60],
                (task.updated or "")[:16],
            )
        # Активировать кнопки
        for btn_id in ("#btn-run-agent", "#btn-add-task", "#btn-remove-proj"):
            self.query_one(btn_id, Button).disabled = False

    # ── Events ───────────────────────────────────────────────────────────────

    @on(Static.focus)
    def _on_card_focus(self, event) -> None:
        if isinstance(getattr(event, 'control', None), ProjectCard):
            self._select_project(event.control.project)

    @on(Button.Pressed, "#btn-add-proj")
    def _on_add_project(self) -> None:
        self.app.push_screen(AddProjectDialog(self.config), self._on_project_added)

    def _on_project_added(self, entry: ProjectEntry | None) -> None:
        if entry:
            self._reload_projects()
            self.notify(f"Проект '{entry.name}' добавлен")

    @on(Button.Pressed, "#btn-refresh")
    def action_refresh(self) -> None:
        self._reload_projects()
        self.notify("Обновлено")

    @on(Button.Pressed, "#btn-add-task")
    def _on_add_task(self) -> None:
        if not self._selected_project:
            return
        self.app.push_screen(AddTaskDialog(self._selected_project), self._on_task_added)

    def _on_task_added(self, task: Task | None) -> None:
        if task and self._selected_project:
            self._select_project(self._selected_project)
            self.notify(f"Задача '{task.title}' создана")

    @on(Button.Pressed, "#btn-run-agent")
    def _on_run_agent(self) -> None:
        if not self._selected_project:
            return
        self.app.push_screen(AgentRunScreen(self._selected_project, self.config))

    @on(Button.Pressed, "#btn-remove-proj")
    def _on_remove_project(self) -> None:
        if not self._selected_project:
            return
        name = self._selected_project.entry.name
        self.config.remove_project(name)
        self._selected_project = None
        self.query_one("#detail-header", Static).update("Выберите проект →")
        for btn_id in ("#btn-run-agent", "#btn-add-task", "#btn-remove-proj"):
            self.query_one(btn_id, Button).disabled = True
        self._reload_projects()
        self.notify(f"Проект '{name}' удалён из реестра (файлы не тронуты)")

    def action_add_project(self) -> None:
        self._on_add_project()

    def action_settings(self) -> None:
        self.app.push_screen(WizardScreen(self.config))


# Импорт после определения DashboardScreen чтобы избежать циклов
from cod_doc.tui.screens.wizard import WizardScreen  # noqa: E402
