"""`doc import` — import frontmatter changes from a projection file back into DB."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import click

from ._common import _get_root_path, _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config
    from cod_doc.services.import_service import ImportReport


@doc.command("import")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.option(
    "--replace",
    is_flag=True,
    default=False,
    help="Treat the file as the whole body: delete DB sections it no longer has "
    "and reorder the rest to match it",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Run the import and report it, then roll back — nothing is written",
)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt --replace raises before deleting sections",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Allow --replace to empty the document when the file parses to no sections",
)
@click.pass_context
def doc_import(
    ctx: click.Context,
    file_path: str,
    project: str,
    author: str,
    replace: bool,
    dry_run: bool,
    assume_yes: bool,
    force: bool,
) -> None:
    """Import frontmatter changes from a projection file back into the DB.

    By default the import is additive: sections in the file are patched or
    appended, and sections the file has since lost are kept and reported.
    That is deliberate — `doc import` is used as "pull my edits in", and a
    partial file must not silently cost a section.

    `--replace` makes the file the whole body: the orphans are deleted (each
    leaving a revision) and the DB order is brought to the file's order. It
    asks first, `--dry-run` shows what it would do, and it refuses outright
    when the file parses to no sections at all while the DB holds some —
    that is what a truncated write looks like (ADO-213).
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import projection_service
    from cod_doc.services.import_service import ReplaceWouldEmptyError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    root = _get_root_path(project, cfg)
    path = Path(file_path).expanduser().resolve()

    def _run(*, commit: bool) -> ImportReport | None:
        with transactional(sf, commit=commit) as session:
            project_id = _require_project_id(session, project)
            return projection_service.import_document(
                session,
                project_id,
                path,
                author=author,
                root_path=root,
                replace=replace,
                force=force,
            )

    # A preview pass costs one rolled-back transaction and buys the two things
    # `--replace` was missing: a look before the leap, and a prompt sized to
    # the damage. Skipped when nothing would ask (ADO-213).
    preview: ImportReport | None = None
    try:
        if dry_run or (replace and not assume_yes):
            preview = _run(commit=False)
            if preview is None:
                _no_match(path)
            if dry_run:
                _report(preview, path, dry_run=True)
                return
            if preview.deleted_sections and not click.confirm(
                f"Delete {len(preview.deleted_sections)} section(s) from "
                f"{preview.document.doc_key} — "
                f"{', '.join(preview.deleted_sections)}?"
            ):
                raise click.Abort
        result = _run(commit=True)
    except ReplaceWouldEmptyError as exc:
        console.print(f"[red]❌ {exc}[/red]")
        sys.exit(1)

    if result is None:
        _no_match(path)
    _report(result, path, dry_run=False)


def _no_match(path: Path) -> NoReturn:
    console.print(f"[red]No document in this project matches path: {path}[/red]")
    sys.exit(1)


def _report(result: ImportReport, path: Path, *, dry_run: bool) -> None:
    if dry_run:
        console.print(
            f"[cyan]🔍 Dry run — would import {result.document.doc_key} "
            f"from {path.name}; nothing written[/cyan]"
        )
    else:
        console.print(f"[green]✅ Imported {result.document.doc_key} from {path.name}[/green]")
    # ADO-015: frontmatter the enums could not store as written. Printed, not
    # swallowed — a coerced `type:` used to be invisible until export rewrote
    # the file with the fallback.
    for warning in result.warnings:
        console.print(f"[yellow]⚠️  {warning.describe()}[/yellow]")
    # ADO-213: sections the DB holds and the file does not. Named either way —
    # the whole reason they piled up is that nothing ever said a word.
    verb = "would remove" if dry_run else "removed"
    for anchor in result.deleted_sections:
        console.print(f"[yellow]🗑️  {verb} section '{anchor}' (not in the file)[/yellow]")
    for anchor in result.orphan_sections:
        if anchor in result.deleted_sections:
            continue
        console.print(
            f"[yellow]⚠️  section '{anchor}' is in the DB but not in the file — "
            f"kept. Re-run with --replace to delete it.[/yellow]"
        )
    if result.reordered:
        tail = "would be brought" if dry_run else "brought"
        console.print(f"[yellow]↕️  section order {tail} to the file's order[/yellow]")
