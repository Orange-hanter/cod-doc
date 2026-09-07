"""Команды управления проектами: project add/list/remove/init/status."""

from __future__ import annotations

import json as _json
import re
import sys

import click
from rich.console import Console
from rich.table import Table

from cod_doc.config import Config, ProjectEntry
from cod_doc.logging_config import get_logger

console = Console()
log = get_logger("cli")


def _init_and_report(entry: ProjectEntry, *, verb: str) -> None:
    """SYM-001: полный бутстрап (файлы + alembic + строка project) с отчётом.

    До этого CLI создавал только файлы (`Project.init()`), а БД — нет:
    `project_service.init_project` вызывался единственно из web-роута
    `POST /p/{slug}/init`, и задокументированная последовательность
    `project add → project init → import docs` падала на пустой SQLite.
    """
    from cod_doc.services import project_service

    result = project_service.init_project(entry)
    db_part = "БД мигрирована (уже существовала)" if result.db_existed else "БД создана"
    row_part = "запись проекта на месте" if result.db_row_existed else "запись проекта создана"
    files_part = "файлы .cod-doc/ созданы" if result.files_created else "файлы .cod-doc/ на месте"
    console.print(
        f"[green]✅ Проект '{entry.name}' {verb}: {db_part}, {row_part}, {files_part}.[/green]"
    )


@click.group()
def project() -> None:
    """Управление проектами."""


@project.command("list")
@click.pass_context
def project_list(ctx: click.Context) -> None:
    """Список всех зарегистрированных проектов."""
    from cod_doc.core.project import Project

    cfg: Config = ctx.obj["config"]
    projects = cfg.list_projects()
    if not projects:
        console.print("[yellow]Проектов нет. Добавьте: cod-doc project add[/yellow]")
        return

    table = Table(title="Проекты COD-DOC", show_header=True)
    table.add_column("Имя", style="cyan", no_wrap=True)
    table.add_column("Путь", style="dim")
    table.add_column("MASTER.md", style="green")
    table.add_column("Статус")
    table.add_column("Задачи")

    for entry in projects:
        proj = Project(entry)
        stats = proj.stats()
        master_exists = "✅" if entry.master_path.exists() else "❌"
        status_icon = {"idle": "🟢", "running": "🔵"}.get(stats["status"], "⚪")
        table.add_row(
            entry.name,
            entry.path,
            master_exists,
            f"{status_icon} {stats['status']}",
            f"🟡{stats['pending']} 🟢{stats['done']} 🔴{stats['failed']}",
        )
    console.print(table)


@project.command("add")
@click.argument("path")
@click.option("--name", "-n", required=True, help="Имя проекта")
@click.option("--master", "-m", default="MASTER.md", help="Путь к MASTER.md")
@click.pass_context
def project_add(ctx: click.Context, path: str, name: str, master: str) -> None:
    """Добавить проект в реестр COD-DOC."""
    from pathlib import Path

    cfg: Config = ctx.obj["config"]
    p = Path(path).expanduser().resolve()
    if not p.exists():
        console.print(f"[red]Директория не найдена: {p}[/red]")
        sys.exit(1)

    entry = ProjectEntry(name=name, path=str(p), master_md=master)
    cfg.add_project(entry)
    _init_and_report(entry, verb="добавлен")


@project.command("remove")
@click.argument("name")
@click.pass_context
def project_remove(ctx: click.Context, name: str) -> None:
    """Удалить проект из реестра (файлы не удаляются)."""
    cfg: Config = ctx.obj["config"]
    if cfg.remove_project(name):
        console.print(f"[green]Проект '{name}' удалён из реестра.[/green]")
    else:
        console.print(f"[red]Проект '{name}' не найден.[/red]")
        sys.exit(1)


@project.command("init")
@click.argument("name")
@click.pass_context
def project_init(ctx: click.Context, name: str) -> None:
    """(Пере)инициализировать проект: .cod-doc/, state.db со схемой, запись project."""
    cfg: Config = ctx.obj["config"]
    entry = cfg.get_project(name)
    if not entry:
        console.print(f"[red]Проект '{name}' не найден.[/red]")
        sys.exit(1)
    _init_and_report(entry, verb="инициализирован")


@project.command("migrate")
@click.argument("name", required=False)
@click.option("--all", "all_projects", is_flag=True, default=False, help="Все проекты реестра")
@click.pass_context
def project_migrate(ctx: click.Context, name: str | None, all_projects: bool) -> None:
    """Накатить голову миграций на БД проекта (hub из db_url или embedded).

    STO-009: то же, что делает контейнерный bootstrap. Резолв БД — общий
    (`db_url_for_entry`), поэтому hub-проект мигрируется по своему `db_url`,
    а не по `<root>/.cod-doc/state.db`.
    """
    from cod_doc.services import project_service

    cfg: Config = ctx.obj["config"]
    if all_projects == bool(name):
        console.print("[red]Укажи имя проекта либо --all (но не оба сразу).[/red]")
        sys.exit(2)

    if all_projects:
        results = project_service.migrate_registered_projects(cfg)
    else:
        entry = cfg.get_project(name or "")
        if not entry:
            console.print(f"[red]Проект '{name}' не найден.[/red]")
            sys.exit(1)
        results = [project_service.migrate_entry(entry)]

    if not results:
        console.print("[yellow]В реестре нет проектов — мигрировать нечего.[/yellow]")
        return

    for result in results:
        if result.ok:
            console.print(f"[green]✅ {result.name} → {result.db_url}[/green]")
        else:
            console.print(f"[red]❌ {result.name} → {result.db_url}: {result.error}[/red]")

    if any(not result.ok for result in results):
        sys.exit(1)


@project.command("status")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def project_status(ctx: click.Context, name: str, as_json: bool) -> None:
    """Подробный статус проекта: задачи, ссылки, последний запуск."""
    from cod_doc.core.project import Project, TaskStatus

    cfg: Config = ctx.obj["config"]
    entry = cfg.get_project(name)
    if not entry:
        console.print(f"[red]Проект '{name}' не найден.[/red]")
        sys.exit(1)

    proj = Project(entry)
    stats = proj.stats()
    tasks = proj.get_tasks()
    next_actions = proj.extract_next_actions()

    master_content = proj.read_master() or ""
    broken_links = re.findall(r"[^\n]*📁[^\n]*🔴[^\n]*", master_content)

    if as_json:
        data = {
            "project": name,
            "stats": stats,
            "tasks": [t.to_dict() for t in tasks],
            "next_actions": next_actions,
            "broken_links": broken_links,
        }
        console.print(_json.dumps(data, ensure_ascii=False, indent=2))
        return

    status_icon = {"idle": "🟢", "running": "🔵", "error": "🔴"}.get(stats["status"], "⚪")
    console.rule(f"[bold cyan]📁 {name}[/bold cyan]")
    console.print(f"  Путь:        [dim]{entry.path}[/dim]")
    console.print(f"  MASTER.md:   {'✅' if entry.master_path.exists() else '❌'}")
    console.print(f"  Статус:      {status_icon} {stats['status']}")
    if stats["last_run"]:
        console.print(f"  Последний запуск: {stats['last_run'][:19]}")

    console.print()
    console.print("[bold]📋 Задачи:[/bold]")
    if not tasks:
        console.print("  [dim]Нет задач[/dim]")
    else:
        icons = {
            TaskStatus.PENDING: "🟡",
            TaskStatus.IN_PROGRESS: "🔵",
            TaskStatus.DONE: "🟢",
            TaskStatus.FAILED: "🔴",
            TaskStatus.BLOCKED: "⚠️",
        }
        table = Table(show_header=True, box=None, padding=(0, 2))
        table.add_column("ID", style="dim", width=10)
        table.add_column("Статус", width=14)
        table.add_column("Приор.", width=6)
        table.add_column("Название")
        for t in tasks:
            table.add_row(
                t.id,
                f"{icons.get(t.status, '⚪')} {t.status.value}",
                str(t.priority),
                t.title,
            )
        console.print(table)

    if broken_links:
        console.print()
        console.print("[bold red]🔴 Сломанные/устаревшие ссылки в MASTER.md:[/bold red]")
        for link in broken_links[:5]:
            console.print(f"  {link[:100]}")

    if next_actions:
        console.print()
        console.print("[bold]⚡ Next actions:[/bold]")
        console.print(f"  {next_actions.get('next_step', '—')}")
        if next_actions.get("blocked_by"):
            console.print(f"  [yellow]Blocked by: {next_actions['blocked_by']}[/yellow]")
