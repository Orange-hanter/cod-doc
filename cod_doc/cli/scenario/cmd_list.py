"""`scenario list` — list scenarios, optionally narrowed to one group."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import STATUS_ICON, _make_session, _require_project_id, console
from ._group import scenario

if TYPE_CHECKING:
    from cod_doc.config import Config


@scenario.command("list")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--group", "group_key", default=None, help="Only this group")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.pass_context
def scenario_list(
    ctx: click.Context,
    project: str,
    group_key: str | None,
    as_json: bool,
) -> None:
    """List scenarios in the project."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import scenario_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        scenarios = (
            scenario_service.list_for_group(session, project_id, group_key)
            if group_key
            else scenario_service.list_for_project(session, project_id)
        )
        rows: list[dict[str, str | None]] = [
            {
                "scenario_id": s.scenario_id,
                "title": s.title,
                "kind": s.kind.value,
                "status": s.status.value,
                "group_key": s.group_key,
                "doc_key": s.doc_key,
            }
            for s in scenarios
        ]
        printable = [
            (s.scenario_id, s.group_key, s.kind.value, s.status.value, s.title) for s in scenarios
        ]

    if as_json:
        console.print_json(json.dumps(rows))
        return

    if not printable:
        console.print("[yellow]No scenarios yet.[/yellow]")
        return

    table = Table(title=f"Scenarios — {project}")
    table.add_column("ID", style="bold")
    table.add_column("Group")
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Title")
    for scenario_id, group, kind, status, title in printable:
        table.add_row(scenario_id, group, kind, f"{STATUS_ICON.get(status, '')} {status}", title)
    console.print(table)
