"""`scenario export` — project scenario groups into docs/system/scenarios/."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _project_root, _require_project_id, console
from ._group import scenario

if TYPE_CHECKING:
    from cod_doc.config import Config


@scenario.command("export")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--group", "group_key", default=None, help="One group (default: every group)")
@click.option("--force", is_flag=True, help="Re-render even when the projection hash matches")
@click.option("--dry-run", is_flag=True, help="Show what would change, write nothing")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def scenario_export(
    ctx: click.Context,
    project: str,
    group_key: str | None,
    force: bool,
    dry_run: bool,
    author: str,
) -> None:
    """Write docs/system/scenarios/<group>.md from the scenarios in the DB.

    Refuses to overwrite a file that was edited by hand: fix the scenarios and
    re-export, or run `cod-doc doc import` to take the edit back into the DB.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import scenario_service
    from cod_doc.services.projection_service import ExportGuardError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    root = _project_root(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            if group_key:
                results = [
                    scenario_service.export_group(
                        session,
                        project_id=project_id,
                        group_key=group_key,
                        root_path=root,
                        author=author,
                        force=force,
                        dry_run=dry_run,
                    )
                ]
            else:
                results = scenario_service.export_all(
                    session,
                    project_id=project_id,
                    root_path=root,
                    author=author,
                    force=force,
                    dry_run=dry_run,
                )
    except ExportGuardError as exc:
        console.print(f"[red]Export refused: {exc}[/red]")
        sys.exit(1)

    if not results:
        console.print("[yellow]No scenario groups to export.[/yellow]")
        return

    written = sum(1 for r in results if r.written)
    for result in results:
        mark = "📝" if result.written else "＝"
        console.print(f"  {mark} {result.path}")
    verb = "would write" if dry_run else "written"
    console.print(f"[green]{written}/{len(results)} file(s) {verb}[/green]")
