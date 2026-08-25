"""Shared session/plan-id helpers + status icons + chain renderer."""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config
    from cod_doc.services.plan_service import ChainEntry

console = Console()
log = get_logger("cli.plan")

_STATUS_ICON = {
    "pending": "🟡",
    "in-progress": "🔵",
    "done": "🟢",
    "empty": "⬜",
}


def _make_session(project_name: str, cfg: Config) -> sessionmaker[Session]:
    from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    url = resolve_db_url(Path(entry.path))
    engine = make_engine(url)
    return make_session_factory(engine)


def _require_plan_id(session: Session, plan_scope: str) -> int:
    from cod_doc.infra.repositories import PlanRepository

    plan = PlanRepository(session).get_by_scope(plan_scope)
    if plan is None or plan.row_id is None:
        console.print(f"[red]Plan '{plan_scope}' not found.[/red]")
        sys.exit(1)
    return plan.row_id


def _render_chain(
    chain: list[ChainEntry],
    label: str,
    task_id: str,
    as_json: bool,
) -> None:
    if as_json:
        console.print(
            _json.dumps(
                [
                    {
                        "task_id": e.task_id,
                        "title": e.title,
                        "status": e.status.value,
                        "depth": e.depth,
                    }
                    for e in chain
                ],
                indent=2,
            )
        )
        return

    console.rule(f"[bold]{label} chain — {task_id}[/bold]")
    if not chain:
        console.print("[dim]No dependencies.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Depth", justify="right", width=6)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", width=14)
    table.add_column("Title")
    for e in chain:
        icon = _STATUS_ICON.get(e.status.value, "⚪")
        table.add_row(str(e.depth), e.task_id, f"{icon} {e.status.value}", e.title)
    console.print(table)
