"""`adr list` — list ADRs in a project."""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import STATUS_ICON, console, make_session, require_project_id
from ._group import adr

if TYPE_CHECKING:
    from cod_doc.config import Config


@adr.command("list")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--status",
    type=click.Choice(["proposed", "accepted", "superseded", "deprecated", "rejected"]),
    default=None,
    help="Filter by status",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def adr_list(ctx: click.Context, project: str, status: str | None, as_json: bool) -> None:
    """List ADRs in a project."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)

    with transactional(sf) as session:
        project_id = require_project_id(session, project)
        rows = adr_service.list_for_project(session, project_id, status=status)

    if as_json:
        console.print(
            _json.dumps(
                [
                    {
                        "adr_id": r.adr_id, "title": r.title, "status": r.status,
                        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
                    }
                    for r in rows
                ],
                indent=2, ensure_ascii=False,
            )
        )
        return

    if not rows:
        console.print("[dim]No ADRs found.[/dim]")
        return

    filter_note = f" (status={status})" if status else ""
    table = Table(title=f"ADRs — {project}{filter_note}", show_header=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", width=14)
    table.add_column("Decided", width=12)
    table.add_column("Title")
    for r in rows:
        icon = STATUS_ICON.get(r.status, "•")
        table.add_row(
            r.adr_id,
            f"{icon} {r.status}",
            r.decided_at.isoformat() if r.decided_at else "—",
            r.title,
        )
    console.print(table)
