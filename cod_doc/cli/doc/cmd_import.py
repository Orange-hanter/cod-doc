"""`doc import` — import frontmatter changes from a projection file back into DB."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from ._common import _get_root_path, _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("import")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def doc_import(ctx: click.Context, file_path: str, project: str, author: str) -> None:
    """Import frontmatter changes from a projection file back into the DB."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import projection_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    root = _get_root_path(project, cfg)
    path = Path(file_path).expanduser().resolve()

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        result = projection_service.import_document(
            session, project_id, path, author=author, root_path=root
        )

    if result is None:
        console.print(f"[red]No document in this project matches path: {path}[/red]")
        sys.exit(1)
    console.print(f"[green]✅ Imported {result.doc_key} from {path.name}[/green]")
