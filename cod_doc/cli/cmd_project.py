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
    from cod_doc.core.project import Project

    cfg: Config = ctx.obj["config"]
    p = Path(path).expanduser().resolve()
    if not p.exists():
        console.print(f"[red]Директория не найдена: {p}[/red]")
        sys.exit(1)

    entry = ProjectEntry(name=name, path=str(p), master_md=master)
    cfg.add_project(entry)
    proj = Project(entry)
    proj.init()
    console.print(f"[green]✅ Проект '{name}' добавлен и инициализирован.[/green]")


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
    """Переинициализировать .cod-doc/ в проекте."""
    from cod_doc.core.project import Project

    cfg: Config = ctx.obj["config"]
    entry = cfg.get_project(name)
    if not entry:
        console.print(f"[red]Проект '{name}' не найден.[/red]")
        sys.exit(1)
    Project(entry).init()
    console.print(f"[green]✅ Проект '{name}' инициализирован.[/green]")


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
