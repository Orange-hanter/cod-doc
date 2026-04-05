"""Команды агента: agent run."""

from __future__ import annotations

import asyncio
import sys

import click
from rich.console import Console

from cod_doc.config import Config
from cod_doc.logging_config import get_logger

console = Console()
log = get_logger("cli")


@click.group()
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
        console.print("\n[yellow]⚠️  Агент ожидает ответа:[/yellow]")
        console.print(f"[bold]{question}[/bold]")
        if context:
            console.print(f"[dim]{context}[/dim]")
        return input("Ваш ответ: ")

    orch = Orchestrator(proj, cfg, on_ask_human=_on_ask)

    async def _run() -> None:
        if autonomous:
            gen = orch.run_autonomous()
        else:
            pending = proj.next_pending_task()
            if not pending:
                console.print("[yellow]Нет задач в очереди[/yellow]")
                return
            gen = orch.run_task(pending)
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
                data = f"{event.data.get('name')}({str(event.data.get('args', ''))[:80]})"
            console.print(f"{icon} {data[:300]}")

    asyncio.run(_run())
