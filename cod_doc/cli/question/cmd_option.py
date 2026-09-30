"""`question option add|edit|rm` — answer options."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from ._common import console, make_session, require_project_id, service_errors
from ._group import question

if TYPE_CHECKING:
    from cod_doc.config import Config


@question.group("option")
def option() -> None:
    """Answer options of a question. Positions are stable: removal leaves a gap."""


@option.command("add")
@click.argument("question_id")
@click.argument("title")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--body", "-b", default=None, help="Pros/cons, markdown")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def option_add(
    ctx: click.Context,
    question_id: str,
    title: str,
    project: str,
    body: str | None,
    author: str,
) -> None:
    """Append an option."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        opt = question_service.add_option(
            session,
            project_id=project_id,
            question_id=question_id,
            title=title,
            body=body,
            author=author,
        )
        position = opt.position
    console.print(f"[green]➕ {question_id} option #{position} {title}[/green]")


@option.command("edit")
@click.argument("question_id")
@click.argument("position", type=int)
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--title", "-t", default=None)
@click.option("--body", "-b", default=None, help="empty string clears")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def option_edit(
    ctx: click.Context,
    question_id: str,
    position: int,
    project: str,
    title: str | None,
    body: str | None,
    author: str,
) -> None:
    """Patch an option's title/body."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        question_service.update_option(
            session,
            project_id=project_id,
            question_id=question_id,
            position=position,
            title=title,
            body=body,
            author=author,
        )
    console.print(f"[green]✏️ {question_id} option #{position} updated[/green]")


@option.command("rm")
@click.argument("question_id")
@click.argument("position", type=int)
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def option_rm(
    ctx: click.Context,
    question_id: str,
    position: int,
    project: str,
    author: str,
) -> None:
    """Remove an option."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        question_service.remove_option(
            session,
            project_id=project_id,
            question_id=question_id,
            position=position,
            author=author,
        )
    console.print(f"[green]➖ {question_id} option #{position} removed[/green]")
