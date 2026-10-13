"""`plan export` — export markdown projections for a plan."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_plan_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.command("export")
@click.argument("plan_scope")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--section",
    type=click.Choice(["progress_overview", "next_batch", "dependency_graph", "all"]),
    default="all",
    show_default=True,
)
@click.pass_context
def plan_export(ctx: click.Context, plan_scope: str, project: str, section: str) -> None:
    """Export markdown projections for a plan."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        pid, plan_id = _require_plan_id(session, project, plan_scope)
        projections = plan_service.export(session, plan_id, project_id=pid)

    keys = list(projections.keys()) if section == "all" else [section]
    for key in keys:
        console.print(projections[key])
