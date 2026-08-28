"""`doc delete` — remove a document (or bulk by glob) from the DB only."""

from __future__ import annotations

import sys
from pathlib import PurePath
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


def _match_path(path: str, pattern: str) -> bool:
    return PurePath(path).match(pattern)


@doc.command("delete")
@click.argument("doc_key", required=False)
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--path-glob",
    default=None,
    help="Bulk: delete documents whose path matches this glob (supports **)",
)
@click.option("--type", "doc_type", default=None, help="Bulk: filter by document type")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show candidates and section counts; write nothing",
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Confirm bulk deletion (required without --dry-run)",
)
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def doc_delete(
    ctx: click.Context,
    doc_key: str | None,
    project: str,
    path_glob: str | None,
    doc_type: str | None,
    dry_run: bool,
    yes: bool,
    author: str,
    reason: str | None,
) -> None:
    """Delete a single document by key, or bulk-delete by path glob (+ type).

    The on-disk file is never touched; this removes only DB rows. Revisions and
    activity events are intentionally preserved.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service
    from cod_doc.services.doc_service import DocumentNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    if doc_key is None and path_glob is None:
        console.print("[red]Provide either <doc_key> or --path-glob.[/red]")
        sys.exit(2)
    if doc_key is not None and path_glob is not None:
        console.print("[red]Use either <doc_key> or --path-glob, not both.[/red]")
        sys.exit(2)
    if doc_type is not None and path_glob is None:
        console.print("[red]--type can only be used with --path-glob.[/red]")
        sys.exit(2)
    if yes and path_glob is None:
        console.print("[red]--yes only makes sense for bulk --path-glob deletions.[/red]")
        sys.exit(2)

    if doc_key is not None:
        try:
            with transactional(sf, commit=not dry_run) as session:
                project_id = _require_project_id(session, project)
                result = doc_service.delete(
                    session,
                    project_id=project_id,
                    doc_key=doc_key,
                    author=author,
                    reason=reason,
                )
        except DocumentNotFoundError:
            console.print(f"[red]Document '{doc_key}' not found.[/red]")
            sys.exit(1)

        if dry_run:
            console.print(
                f"[dim]Would delete {result.doc_key} ({result.title}) "
                f"— {result.section_count} section(s).[/dim]"
            )
        else:
            console.print(
                f"[green]✅ Deleted {result.doc_key} ({result.title}) "
                f"— {result.section_count} section(s).[/green]"
            )
        return

    # Bulk path.
    with transactional(sf, commit=not dry_run) as session:
        project_id = _require_project_id(session, project)
        candidates = doc_service.list_delete_candidates(
            session,
            project_id=project_id,
            path_glob=path_glob,
            doc_type=doc_type,
        )
        if not candidates:
            console.print("[dim]No matching documents.[/dim]")
            return

        if dry_run:
            console.print(f"[dim]Would delete {len(candidates)} document(s):[/dim]")
            for c in candidates:
                console.print(f"  - {c.doc_key}: {c.title} ({c.section_count} section(s))")
            return

        if not yes:
            console.print(
                f"[yellow]{len(candidates)} document(s) match. Use --yes to delete.[/yellow]"
            )
            for c in candidates:
                console.print(f"  - {c.doc_key}: {c.title} ({c.section_count} section(s))")
            sys.exit(2)

        deleted = [
            doc_service.delete(
                session,
                project_id=project_id,
                doc_key=c.doc_key,
                author=author,
                reason=reason,
            )
            for c in candidates
        ]

    console.print(f"[green]✅ Deleted {len(deleted)} document(s).[/green]")
