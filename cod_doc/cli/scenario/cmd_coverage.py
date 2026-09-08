"""`scenario coverage` — how well each capability is *described*."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import _make_session, _require_project_id, console
from ._group import scenario

if TYPE_CHECKING:
    from cod_doc.config import Config


@scenario.command("coverage")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--group", "group_key", default=None, help="Only this group")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.pass_context
def scenario_coverage(
    ctx: click.Context,
    project: str,
    group_key: str | None,
    as_json: bool,
) -> None:
    """Authoring coverage: how much has been written down, per group.

    This is not test coverage. Whether a test proves a scenario is RFC 24 §9
    evidence produced elsewhere and is deliberately not reported here.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import scenario_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        report = (
            [scenario_service.group_coverage(session, project_id, group_key)]
            if group_key
            else scenario_service.project_coverage(session, project_id)
        )
        rows = [
            {
                "group_key": c.group_key,
                "total": c.total,
                "draft": c.draft,
                "confirmed": c.confirmed,
                "retired": c.retired,
                "by_kind": c.by_kind,
                "missing_kinds": c.missing_kinds,
            }
            for c in report
        ]
        printable = [
            (
                c.group_key,
                c.total,
                c.draft,
                c.confirmed,
                c.retired,
                ", ".join(c.missing_kinds) or "—",
            )
            for c in report
        ]

    if as_json:
        console.print_json(json.dumps(rows))
        return

    if not printable:
        console.print("[yellow]No scenarios yet.[/yellow]")
        return

    table = Table(title=f"Authoring coverage — {project}")
    table.add_column("Group", style="bold")
    table.add_column("Total", justify="right")
    table.add_column("Draft", justify="right")
    table.add_column("Confirmed", justify="right")
    table.add_column("Retired", justify="right")
    table.add_column("Missing kinds")
    for group, total, draft, confirmed, retired, missing in printable:
        table.add_row(group, str(total), str(draft), str(confirmed), str(retired), missing)
    console.print(table)
    console.print("[dim]Authoring coverage only — not test coverage.[/dim]")
