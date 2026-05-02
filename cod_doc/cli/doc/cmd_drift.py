"""`doc drift` — detect drift between DB / projection_hash / on-disk file."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _DRIFT_ICON, _get_root_path, _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("drift")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_drift(ctx: click.Context, doc_key: str, project: str, as_json: bool) -> None:
    """Detect drift between DB content, projection hash, and on-disk file."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service, projection_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    root = _get_root_path(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        d = doc_service.get(session, project_id, doc_key)
        if d is None or d.row_id is None:
            console.print(f"[red]Document '{doc_key}' not found.[/red]")
            sys.exit(1)
        report = projection_service.detect_drift(session, d.row_id, root_path=root)

    if as_json:
        console.print(
            _json.dumps(
                {
                    "doc_key": doc_key,
                    "status": report.status.value,
                    "projection_hash": report.projection_hash,
                    "db_content_hash": report.db_content_hash,
                    "file_hash": report.file_hash,
                },
                indent=2,
            )
        )
        return

    icon = _DRIFT_ICON.get(report.status.value, "⚪")
    console.rule(f"[bold]Drift — {doc_key}[/bold]")
    console.print(f"  Status: {icon} {report.status.value}")
    console.print(f"  DB hash:   {report.db_content_hash[:16]}…")
    console.print(
        f"  Proj hash: {(report.projection_hash or '—')[:16]}{'…' if report.projection_hash else ''}"
    )
    console.print(
        f"  File hash: {(report.file_hash or '(missing)')[:16]}{'…' if report.file_hash else ''}"
    )
