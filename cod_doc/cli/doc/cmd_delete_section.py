"""`doc delete-section` — remove one section from a document (mirror of MCP `doc_delete_section`)."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("delete-section")
@click.argument("doc_key")
@click.argument("anchor")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show the diff that would be recorded without deleting anything",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_delete_section(
    ctx: click.Context,
    doc_key: str,
    anchor: str,
    project: str,
    author: str,
    reason: str | None,
    assume_yes: bool,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Delete section ANCHOR from DOC_KEY.

    Closes the section cycle `doc add-section` → `doc patch` → this. Until it
    existed a heading that left the markdown file stayed in the DB forever:
    `doc import` could patch and append, never drop (ADO-213).

    The removed body survives in the DOCUMENT revision this writes (JSON
    ``op=delete_section``); `revision revert` refuses it explicitly — the row is
    gone. Read the body back out of `revision get` and re-create it with
    `doc add-section`.
    """
    from rich.markup import escape

    from cod_doc.domain.entities import EntityKind
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service
    from cod_doc.services import revision_service as revisions

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    # Чтение — в своей транзакции без записи: вопрос «Proceed?» ниже ждёт
    # человека сколько угодно, а держать на это время открытую транзакцию общей
    # SQLite, в которую пишут демоны, нельзя (ai-review #85).
    with transactional(sf, commit=False) as session:
        project_id = _require_project_id(session, project)
        d = doc_service.get(session, project_id, doc_key)
        if d is None or d.row_id is None:
            console.print(f"[red]Document '{escape(doc_key)}' not found.[/red]")
            sys.exit(1)
        document_id = d.row_id

        before = doc_service.get_sections(session, document_id)
        section = next((s for s in before if s.anchor == anchor), None)
        if section is None:
            console.print(
                f"[red]Section '{escape(anchor)}' not found in document '{escape(doc_key)}'.[/red]"
            )
            sys.exit(1)
        heading, position, body = section.heading, section.position, section.body
        # A dry run reports what the real call would leave behind, not what is
        # on the row right now.
        remaining = len(before) - 1
        diff = doc_service.section_delete_diff(body, doc_key=doc_key, anchor=anchor)

    revision_id: str | None = None
    if not dry_run:
        # ADO-213: `--json` picks the output format, not the consent.
        # Machine callers pass `--yes`; `--json` alone still asks.
        if not assume_yes:
            # Под `--json` вопрос уходит в stderr, чтобы не сломать разбор
            # ответа: формат вывода и согласие — разные вещи.
            prompt = f"Delete {doc_key}#{anchor} — {heading!r} ({len(body.splitlines())} line(s))?"
            if as_json:
                click.echo(prompt, err=True)
            else:
                console.print(f"[yellow]{escape(prompt)}[/yellow]")
            click.confirm("Proceed", abort=True, err=as_json)
        with transactional(sf) as session:
            try:
                doc_service.delete_section(
                    session,
                    document_id=document_id,
                    anchor=anchor,
                    author=author,
                    reason=reason,
                )
            except doc_service.SectionNotFoundError:
                # Между вопросом и записью секцию успели удалить.
                console.print(f"[red]Section '{escape(anchor)}' is already gone.[/red]")
                sys.exit(1)
            # Ревизия удаления висит на документе (ADO-213), а не на мёртвой строке.
            revision_id = revisions.head_for_entity(session, EntityKind.DOCUMENT, document_id)

    if as_json:
        payload: dict[str, object] = {
            "doc_key": doc_key,
            "anchor": anchor,
            "heading": heading,
            "position": position,
            "revision_id": revision_id,
            "deleted": not dry_run,
            "remaining_sections": remaining,
            "dry_run": dry_run,
        }
        if dry_run:
            payload["diff"] = diff
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if dry_run:
        console.print(f"[cyan]🔍 Dry run {doc_key}#{anchor}[/cyan]")
        # click.echo, not console.print: a diff is raw text and may contain
        # square brackets that rich would swallow as markup.
        click.echo(diff)
        return
    console.print(
        f"[green]🗑️  Deleted {doc_key}#{anchor}[/green]\n"
        f"[dim]{remaining} section(s) left, revision {revision_id}[/dim]"
    )
