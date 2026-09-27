"""`question import` — move a legacy ``type: open-question`` document into questions."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import click

from ._common import console, echo_json, make_session, require_project_id, service_errors
from ._group import question

if TYPE_CHECKING:
    from cod_doc.config import Config


def _print_plan(payload: dict[str, Any], *, applied: bool) -> None:
    verb = "created" if applied else "would create"
    console.print(
        f"[bold]{payload['doc_key']}[/bold] — {payload['mode']}: "
        f"{verb} {len(payload['questions'])} question(s)"
    )
    for i, q in enumerate(payload["questions"]):
        qid = payload["created"][i] if applied else "·"
        console.print(
            f"  {qid} [{q['status']}] {q['title']}  "
            f"options: {len(q['options'])}, links: {len(q['links'])}"
        )
        for warning in q["warnings"]:
            console.print(f"    [yellow]⚠ {warning}[/yellow]")
    if payload["skipped_links"]:
        console.print(f"[yellow]links not resolved: {', '.join(payload['skipped_links'])}[/yellow]")
    if payload["incoming_links"]:
        console.print(
            f"[yellow]{payload['incoming_links']} link(s) from other documents point here "
            "and will break once the document is gone[/yellow]"
        )


@question.command("import")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--dry-run", is_flag=True, help="Show the plan, write nothing")
@click.option("--keep-doc", is_flag=True, help="Keep the document and its file (trial run)")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before deleting the document")
@click.option("--author", default="cli", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.pass_context
def question_import(
    ctx: click.Context,
    doc_key: str,
    project: str,
    dry_run: bool,
    keep_doc: bool,
    yes: bool,
    author: str,
    as_json: bool,
) -> None:
    """Turn an open-question document into questions, then delete the document.

    A registry document (many ``### OQ-NNN`` items) becomes one question per
    item. The markdown file is removed from disk too — commit that removal.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service
    from cod_doc.services.doc_service import DocumentNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    try:
        with service_errors(), transactional(sf) as session:
            project_id = require_project_id(session, project)
            if not (dry_run or keep_doc or yes or as_json):
                preview = question_service.import_document(
                    session, project_id=project_id, doc_key=doc_key, author=author, dry_run=True
                )
                _print_plan(question_service.import_result_to_dict(preview), applied=False)
                if not click.confirm("Import and delete the document and its file?"):
                    sys.exit(1)
            result = question_service.import_document(
                session,
                project_id=project_id,
                doc_key=doc_key,
                author=author,
                dry_run=dry_run,
                delete_document=not keep_doc,
            )
            payload = question_service.import_result_to_dict(result)
    except (DocumentNotFoundError, question_service.NotAQuestionDocumentError) as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    except question_service.AlreadyImportedError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    if as_json:
        echo_json(payload)
        return
    _print_plan(payload, applied=not dry_run)
    if payload["document_deleted"]:
        tail = " and its file" if payload["file_deleted"] else ""
        console.print(f"[green]document {doc_key}{tail} deleted[/green]")
