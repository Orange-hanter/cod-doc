"""`adr export` — project ADRs to ``docs/adr/ADR-NNN.md`` files.

Idempotent: only files whose rendering differs from disk get rewritten.
Useful for git visibility — the markdown projection lives in the repo,
so `git diff` shows architectural-decision changes alongside code.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from ._common import console, make_session, require_project_id
from ._group import adr

if TYPE_CHECKING:
    from cod_doc.config import Config


@adr.command("export")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, dir_okay=True, resolve_path=True),
    default=None,
    help="Override output directory (default: <project>/docs/adr).",
)
@click.pass_context
def adr_export(ctx: click.Context, project: str, out_dir: str | None) -> None:
    """Project every ADR of the project into ``ADR-NNN.md`` files."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    target = Path(out_dir) if out_dir else None

    with transactional(sf) as session:
        project_id = require_project_id(session, project)
        written = adr_service.export_to_disk(
            session, project_id=project_id, out_dir=target,
        )

    if not written:
        console.print("[dim]Nothing to export — all ADRs already on disk.[/dim]")
        return
    console.print(f"[green]Wrote {len(written)} ADR file(s):[/green]")
    for path in written:
        console.print(f"  • {path}")
