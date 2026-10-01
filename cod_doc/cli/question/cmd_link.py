"""`question link|unlink|verify` — edges to documents, code, tasks, ADRs…"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import (
    LINK_KIND_CHOICES,
    RELATION_CHOICES,
    console,
    echo_json,
    make_session,
    require_project_id,
    service_errors,
)
from ._group import question

if TYPE_CHECKING:
    from cod_doc.config import Config

_REF_HELP = (
    "task id, ADR-NNN, doc_key, doc_key#anchor, path, path#symbol, path#L10-L20, URL, finding uid"
)


@question.command("link")
@click.argument("question_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--to-kind", required=True, type=click.Choice(LINK_KIND_CHOICES))
@click.option("--to-ref", required=True, help=_REF_HELP)
@click.option("--relation", type=click.Choice(RELATION_CHOICES), default="about")
@click.option("--note", default=None)
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def question_link(
    ctx: click.Context,
    question_id: str,
    project: str,
    to_kind: str,
    to_ref: str,
    relation: str,
    note: str | None,
    author: str,
) -> None:
    """Attach an edge; warns (does not fail) when the target does not exist yet."""
    from cod_doc.domain.entities import QuestionLinkKind, QuestionRelation
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        kind = QuestionLinkKind(to_kind)
        question_service.link(
            session,
            project_id=project_id,
            question_id=question_id,
            to_kind=kind,
            to_ref=to_ref,
            relation=QuestionRelation(relation),
            note=note,
            author=author,
        )
        resolved, reason = question_service.check_edge(session, project_id, kind, to_ref)

    console.print(f"[green]🔗 {question_id} {relation} → {to_kind}:{to_ref}[/green]")
    if resolved is False:
        console.print(f"[yellow]target does not resolve yet: {reason}[/yellow]")


@question.command("unlink")
@click.argument("question_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--to-kind", required=True, type=click.Choice(LINK_KIND_CHOICES))
@click.option("--to-ref", required=True)
@click.option("--relation", type=click.Choice(RELATION_CHOICES), default="about")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def question_unlink(
    ctx: click.Context,
    question_id: str,
    project: str,
    to_kind: str,
    to_ref: str,
    relation: str,
    author: str,
) -> None:
    """Detach an edge."""
    from cod_doc.domain.entities import QuestionLinkKind, QuestionRelation
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        removed = question_service.unlink(
            session,
            project_id=project_id,
            question_id=question_id,
            to_kind=QuestionLinkKind(to_kind),
            to_ref=to_ref,
            relation=QuestionRelation(relation),
            author=author,
        )
    if not removed:
        console.print("[yellow]No such edge; nothing removed.[/yellow]")
        return
    console.print(f"[green]⊘ {question_id} {relation} → {to_kind}:{to_ref} removed[/green]")


@question.command("verify")
@click.argument("question_id", required=False)
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.pass_context
def question_verify(
    ctx: click.Context,
    question_id: str | None,
    project: str,
    as_json: bool,
) -> None:
    """Re-check links of one question (or all); exit 1 if any link is broken."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        report = question_service.verify_links(
            session, project_id=project_id, question_id=question_id
        )

    if as_json:
        echo_json(
            {
                "checked": report.checked,
                "ok": report.ok,
                "broken": report.broken,
                "unchecked": report.unchecked,
                "broken_links": [
                    {
                        "question_id": b.question_id,
                        "to_kind": b.to_kind,
                        "to_ref": b.to_ref,
                        "relation": b.relation,
                        "broken_reason": b.broken_reason,
                    }
                    for b in report.broken_links
                ],
            }
        )
    else:
        console.print(
            f"checked {report.checked}: ok {report.ok}, broken {report.broken}, "
            f"unchecked {report.unchecked}"
        )
        for b in report.broken_links:
            console.print(
                f"[red]✗ {b.question_id} {b.to_kind}:{b.to_ref} — {b.broken_reason}[/red]"
            )
    if report.broken:
        sys.exit(1)
