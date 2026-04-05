"""
CLI точка входа: cod-doc [команды]

cod-doc tui              — запустить TUI (wizard + dashboard)
cod-doc wizard           — запустить только wizard настройки
cod-doc project add      — добавить проект
cod-doc project list     — список проектов
cod-doc project init     — инициализировать .cod-doc/ в проекте
cod-doc agent run        — запустить агент для проекта
cod-doc serve            — запустить REST API сервер
cod-doc hash calc        — вычислить хэш файла
cod-doc hash update      — обновить хэши в MASTER.md
"""

from __future__ import annotations

import asyncio
import sys

import click
from rich.console import Console
from rich.table import Table

from cod_doc.config import Config, ProjectEntry
from cod_doc.logging_config import setup_logging

console = Console()


@click.group()
@click.option("--log-level", default=None, envvar="LOG_LEVEL", help="DEBUG|INFO|WARNING|ERROR")
@click.option("--log-format", default=None, envvar="LOG_FORMAT", help="text|json")
@click.pass_context
def main(ctx: click.Context, log_level: str | None, log_format: str | None) -> None:
    """🧭 COD-DOC — Context Orchestrator for Documentation."""
    setup_logging(level=log_level, fmt=log_format)
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config.load()


# ── TUI ───────────────────────────────────────────────────────────────────────

@main.command()
@click.pass_context
def tui(ctx: click.Context) -> None:
    """Запустить полный TUI (dashboard + wizard)."""
    from cod_doc.tui.app import CodDocApp
    app = CodDocApp(ctx.obj["config"])
    app.run()


@main.command()
@click.pass_context
def wizard(ctx: click.Context) -> None:
    """Запустить мастер настройки."""
    from cod_doc.tui.app import CodDocApp
    from cod_doc.tui.screens.wizard import WizardScreen
    cfg = ctx.obj["config"]
    # Сбросить конфиг чтобы принудительно показать wizard
    cfg.api_key = ""
    app = CodDocApp(cfg)
    app.run()


# ── Project ───────────────────────────────────────────────────────────────────

@main.group()
def project() -> None:
    """Управление проектами."""


@project.command("list")
@click.pass_context
def project_list(ctx: click.Context) -> None:
    """Список всех зарегистрированных проектов."""
    cfg: Config = ctx.obj["config"]
    projects = cfg.list_projects()
    if not projects:
        console.print("[yellow]Проектов нет. Добавьте: cod-doc project add[/yellow]")
        return

    from cod_doc.core.project import Project
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
    import json as _json
    import re
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

    # Проверить сломанные ссылки в MASTER.md (строки с 📁 и статусом 🔴)
    master_content = proj.read_master() or ""
    broken_links = re.findall(r"[^\n]*📁[^\n]*🔴[^\n]*", master_content)
    stale_links  = re.findall(r"[^\n]*📁[^\n]*🔴 STALE[^\n]*", master_content)

    if as_json:
        data = {
            "project": name,
            "stats": stats,
            "tasks": [t.to_dict() for t in tasks],
            "next_actions": next_actions,
            "broken_links": broken_links,
            "stale_links": stale_links,
        }
        console.print(_json.dumps(data, ensure_ascii=False, indent=2))
        return

    # Human-readable
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
            table.add_row(t.id, f"{icons.get(t.status, '⚪')} {t.status.value}", str(t.priority), t.title)
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


# ── Agent ─────────────────────────────────────────────────────────────────────

@main.group()
def agent() -> None:
    """Управление агентом."""


@agent.command("run")
@click.argument("project_name")
@click.option("--task", "-t", default=None, help="Заголовок новой задачи для выполнения")
@click.option("--autonomous/--no-autonomous", default=True, help="Авто-генерация задач из MASTER.md")
@click.pass_context
def agent_run(ctx: click.Context, project_name: str, task: str | None, autonomous: bool) -> None:
    """Запустить агент для проекта."""
    from cod_doc.agent.orchestrator import Orchestrator
    from cod_doc.core.project import Project, Task as PTask

    cfg: Config = ctx.obj["config"]
    if not cfg.is_configured:
        console.print("[red]API-ключ не настроен. Запустите: cod-doc wizard[/red]")
        sys.exit(1)

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Проект не найден: {project_name}[/red]")
        sys.exit(1)

    proj = Project(entry)
    proj.init()

    if task:
        new_task = PTask(title=task)
        proj.add_task(new_task)
        console.print(f"[cyan]Задача создана: {new_task.id}[/cyan]")

    def _on_ask(question: str, context: str) -> str:
        console.print(f"\n[yellow]⚠️  Агент ожидает ответа:[/yellow]")
        console.print(f"[bold]{question}[/bold]")
        if context:
            console.print(f"[dim]{context}[/dim]")
        return input("Ваш ответ: ")

    orch = Orchestrator(proj, cfg, on_ask_human=_on_ask)

    async def _run() -> None:
        gen = orch.run_autonomous() if autonomous else (
            orch.run_task(proj.next_pending_task()) if proj.next_pending_task()
            else (e for e in [])
        )
        async for event in gen:
            icons = {
                "thinking": "[dim]💭[/dim]",
                "tool_call": "[cyan]🔧[/cyan]",
                "tool_result": "[green]📤[/green]",
                "message": "[white]🤖[/white]",
                "done": "[bold green]✅[/bold green]",
                "error": "[bold red]❌[/bold red]",
                "blocked": "[bold yellow]⚠️[/bold yellow]",
            }
            icon = icons.get(event.type, "•")
            data = str(event.data)
            if event.type == "tool_call" and isinstance(event.data, dict):
                data = f"{event.data.get('name')}({str(event.data.get('args',''))[:80]})"
            console.print(f"{icon} {data[:300]}")

    asyncio.run(_run())


# ── Hash ──────────────────────────────────────────────────────────────────────

@main.group()
def hash() -> None:
    """Утилиты хэширования."""


@hash.command("calc")
@click.argument("file_path")
def hash_calc(file_path: str) -> None:
    """Вычислить SHA-256 хэш файла."""
    from cod_doc.core.hash_calc import calc_hash as _calc
    try:
        h = _calc(file_path)
        console.print(f"sha:{h}  {file_path}")
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)


@hash.command("update")
@click.argument("master_path", default="MASTER.md")
def hash_update(master_path: str) -> None:
    """Обновить все хэши в MASTER.md."""
    from pathlib import Path
    from cod_doc.core.hash_calc import update_hashes
    n, warns = update_hashes(Path(master_path))
    for w in warns:
        console.print(f"[yellow]{w}[/yellow]")
    console.print(f"[green]✅ Обновлено хэшей: {n}[/green]")


# ── Serve ─────────────────────────────────────────────────────────────────────

@main.command()
@click.option("--host", default=None)
@click.option("--port", default=None, type=int)
@click.option("--reload", is_flag=True, default=False)
@click.pass_context
def serve(ctx: click.Context, host: str | None, port: int | None, reload: bool) -> None:
    """Запустить REST API сервер (production)."""
    import uvicorn
    cfg: Config = ctx.obj["config"]
    uvicorn.run(
        "cod_doc.api.server:app",
        host=host or cfg.api_host,
        port=port or cfg.api_port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
