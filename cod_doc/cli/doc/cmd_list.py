"""`doc list` — list all documents for a project."""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import _STATUS_ICON, _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("list")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_list(ctx: click.Context, project: str, as_json: bool) -> None:
    """List all documents for a project."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        docs = doc_service.list_for_project(session, project_id)

    if as_json:
        console.print(
            _json.dumps(
                [
                    {
                        "doc_key": d.doc_key,
                        "title": d.title,
                        "type": d.type.value,
                        "status": d.status.value,
                        "sensitivity": d.sensitivity.value,
                        "owner": d.owner,
                        "path": d.path,
                    }
                    for d in docs
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if not docs:
        console.print("[dim]No documents found.[/dim]")
        return

    table = Table(title=f"Documents — {project}", show_header=True)
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Status", width=10)
    table.add_column("Type", width=16)
    table.add_column("Sensitivity", width=12)
    table.add_column("Title")
    for d in docs:
        icon = _STATUS_ICON.get(d.status.value, "⚪")
        table.add_row(
            d.doc_key,
            f"{icon} {d.status.value}",
            d.type.value,
            d.sensitivity.value,
            d.title,
        )
    console.print(table)
