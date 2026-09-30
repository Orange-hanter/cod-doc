"""`question new|list|show|edit` — create, list, read and patch questions."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import click
from rich.markdown import Markdown
from rich.table import Table

from ._common import (
    LINK_KIND_CHOICES,
    PRIORITY_CHOICES,
    STATUS_CHOICES,
    STATUS_ICON,
    console,
    echo_json,
    make_session,
    require_project_id,
    service_errors,
)
from ._group import question

if TYPE_CHECKING:
    from cod_doc.config import Config


def _read_text(value: str | None) -> str | None:
    """``@path`` reads the value from a file (``@-`` — stdin), like curl."""
    if value is None or not value.startswith("@"):
        return value
    source = value[1:]
    if source == "-":
        return sys.stdin.read()
    with open(source, encoding="utf-8") as fh:  # noqa: PTH123 — путь от пользователя как есть
        return fh.read()


@question.command("new")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--title", "-t", required=True, help="Short title")
@click.option("--question", "-q", "question_text", required=True, help="The question (@file ok)")
@click.option("--context", "-c", default=None, help="Background, markdown (@file / @- ok)")
@click.option("--priority", type=click.Choice(PRIORITY_CHOICES), default="medium")
@click.option("--owner", default=None, help="Who is responsible for the answer")
@click.option("--option", "options", multiple=True, help="Answer option title (repeatable)")
@click.option(
    "--link",
    "links",
    multiple=True,
    help="kind:ref[:relation], e.g. task:ADO-042:addressed_by or code:app.py#L10-L20",
)
@click.option("--author", default="cli", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.pass_context
def question_new(
    ctx: click.Context,
    project: str,
    title: str,
    question_text: str,
    context: str | None,
    priority: str,
    owner: str | None,
    options: tuple[str, ...],
    links: tuple[str, ...],
    author: str,
    as_json: bool,
) -> None:
    """Open a question. It lives in the DB only and is never exported to markdown."""
    from cod_doc.domain.entities import Priority, QuestionLinkKind, QuestionRelation
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    parsed_links = [_parse_link(raw) for raw in links]
    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        created = question_service.create(
            session,
            project_id=project_id,
            title=title,
            question=_read_text(question_text) or "",
            context=_read_text(context),
            priority=Priority(priority),
            owner=owner,
            options=[(o, None) for o in options],
            author=author,
        )
        for kind, ref, relation in parsed_links:
            question_service.link(
                session,
                project_id=project_id,
                question_id=created.question_id,
                to_kind=QuestionLinkKind(kind),
                to_ref=ref,
                relation=QuestionRelation(relation),
                author=author,
            )
        fresh = question_service.get(session, project_id, created.question_id)
        assert fresh is not None
        payload = question_service.question_to_dict(session, fresh)

    if as_json:
        echo_json(payload)
        return
    console.print(f"[green]❓ {payload['question_id']} opened — {title}[/green]")


def _parse_link(raw: str) -> tuple[str, str, str]:
    """``kind:ref[:relation]``; ref may itself contain ``:`` only for ``url``."""
    kind, sep, rest = raw.partition(":")
    if not sep or kind not in LINK_KIND_CHOICES:
        raise click.BadParameter(f"{raw!r}: expected kind:ref[:relation]", param_hint="--link")
    if kind == "url":
        return kind, rest, "about"
    ref, _, relation = rest.partition(":")
    return kind, ref, relation or "about"


@question.command("list")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--status",
    type=click.Choice([*STATUS_CHOICES, "all"]),
    default="open",
    show_default=True,
)
@click.option("--owner", default=None)
@click.option("--priority", type=click.Choice(PRIORITY_CHOICES), default=None)
@click.option("--linked-to", default=None, help="kind:ref — only questions linked to it")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.pass_context
def question_list(
    ctx: click.Context,
    project: str,
    status: str,
    owner: str | None,
    priority: str | None,
    linked_to: str | None,
    as_json: bool,
) -> None:
    """List questions: open first, then by priority."""
    from cod_doc.domain.entities import Priority, QuestionLinkKind, QuestionStatus
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    linked = None
    if linked_to is not None:
        kind, ref, _ = _parse_link(linked_to)
        linked = (QuestionLinkKind(kind), ref)
    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        found = question_service.list_for_project(
            session,
            project_id,
            status=None if status == "all" else QuestionStatus(status),
            owner=owner,
            priority=Priority(priority) if priority else None,
            linked_to=linked,
        )
        rows = [question_service.question_summary(q) for q in found]

    if as_json:
        echo_json(rows)
        return
    if not rows:
        console.print("[yellow]No questions.[/yellow]")
        return
    table = Table(title=f"Questions — {project}")
    for col in ("ID", "Status", "Priority", "Owner", "Title"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r["question_id"],
            f"{STATUS_ICON.get(r['status'], '')} {r['status']}",
            r["priority"],
            r["owner"] or "—",
            r["title"],
        )
    console.print(table)


def _print_card(q: dict[str, Any]) -> None:
    console.print(f"[bold]{q['question_id']} — {q['title']}[/bold]")
    console.print(
        f"{STATUS_ICON.get(q['status'], '')} {q['status']}   priority: {q['priority']}"
        f"   owner: {q['owner'] or '—'}"
    )
    console.print(Markdown(q["question"]))
    if q["context"]:
        console.rule("Context")
        console.print(Markdown(q["context"]))
    if q["options"]:
        console.rule("Options")
        for o in q["options"]:
            mark = "✔" if o["chosen"] else " "
            console.print(f"[{mark}] #{o['position']} [bold]{o['title']}[/bold]")
            if o["body"]:
                console.print(Markdown(o["body"]))
    if q["links"]:
        console.rule("Links")
        for e in q["links"]:
            state = {True: "✓", False: "✗", None: "·"}[e["resolved"]]
            tail = f"  [red]{e['broken_reason']}[/red]" if e["broken_reason"] else ""
            console.print(f"{state} {e['relation']} → {e['to_kind']}:{e['to_ref']}{tail}")
    if q["status"] != "open":
        console.rule("Resolution")
        if q["resolved_by_adr"]:
            console.print(f"by {q['resolved_by_adr']}")
        if q["resolution"]:
            console.print(Markdown(q["resolution"]))


@question.command("show")
@click.argument("question_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.pass_context
def question_show(ctx: click.Context, question_id: str, project: str, as_json: bool) -> None:
    """Show one question with context, options and links."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service
    from cod_doc.services.question_service import QuestionNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        found = question_service.get(session, project_id, question_id)
        if found is None:
            raise QuestionNotFoundError(question_id)
        payload = question_service.question_to_dict(session, found)

    if as_json:
        echo_json(payload)
        return
    _print_card(payload)


@question.command("edit")
@click.argument("question_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--title", "-t", default=None)
@click.option("--question", "-q", "question_text", default=None, help="@file ok")
@click.option("--context", "-c", default=None, help="markdown; empty string clears; @file ok")
@click.option("--priority", type=click.Choice(PRIORITY_CHOICES), default=None)
@click.option("--owner", default=None, help="empty string clears")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def question_edit(
    ctx: click.Context,
    question_id: str,
    project: str,
    title: str | None,
    question_text: str | None,
    context: str | None,
    priority: str | None,
    owner: str | None,
    author: str,
    reason: str | None,
) -> None:
    """Patch fields of a question."""
    from cod_doc.domain.entities import Priority
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        question_service.update(
            session,
            project_id=project_id,
            question_id=question_id,
            title=title,
            question=_read_text(question_text),
            context=_read_text(context),
            priority=Priority(priority) if priority else None,
            owner=owner,
            author=author,
            reason=reason,
        )
    console.print(f"[green]✏️ {question_id} updated[/green]")
