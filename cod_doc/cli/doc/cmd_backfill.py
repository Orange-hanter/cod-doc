"""`doc backfill-projection` — ADO-022 repair for pre-0025 databases."""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click
from rich.table import Table

from ._common import _get_root_path, _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config

_ACTION_ICON = {
    "filled": "✅",
    "file_missing": "❌",
    "skipped": "⚪",
}


@doc.command("backfill-projection")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Classify the rows without writing anything",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_backfill_projection(
    ctx: click.Context,
    project: str,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Recover projection-fidelity columns from the files on disk (ADO-022).

    Databases created before migration `0025_projection_fidelity` do not
    remember how each file was shaped (`frontmatter_raw` / `title_in_body` are
    NULL), so `doc export` would rewrite their frontmatter and invent headings —
    and refuses to run until this command has recovered the shape.

    Unlike `doc import`, only the two shape columns are touched: metadata
    changed in the DB but not yet exported survives.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import projection_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    root = _get_root_path(project, cfg)

    with transactional(sf, commit=not dry_run) as session:
        project_id = _require_project_id(session, project)
        report = projection_service.backfill_projection_fidelity(
            session, project_id, root_path=root, dry_run=dry_run
        )

    if as_json:
        console.print(
            _json.dumps(
                {
                    "project": project,
                    "dry_run": dry_run,
                    "scanned": report.scanned,
                    "filled": report.filled,
                    "file_missing": report.file_missing,
                    "skipped": report.skipped,
                    "items": [
                        {
                            "doc_key": item.doc_key,
                            "path": item.path,
                            "action": item.action.value,
                        }
                        for item in report.items
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console.rule(f"[bold]Backfill projection fidelity — {project}[/bold]")
    console.print(
        f"  scanned: {report.scanned}  ✅ filled: {report.filled}  "
        f"❌ file_missing: {report.file_missing}  ⚪ skipped: {report.skipped}"
    )
    if not report.items:
        console.print("[green]✅ Nothing to backfill — every row knows its file shape.[/green]")
        return

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("Action", width=16)
    table.add_column("Doc key", style="cyan")
    table.add_column("Path", style="dim")
    for item in report.items:
        table.add_row(
            f"{_ACTION_ICON.get(item.action.value, '⚪')} {item.action.value}",
            item.doc_key,
            item.path,
        )
    console.print(table)
    if dry_run:
        console.print("[dim]Dry run — nothing was written.[/dim]")
