"""`question resolve|drop|reopen` — status transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from ._common import console, make_session, require_project_id, service_errors
from ._group import question

if TYPE_CHECKING:
    from cod_doc.config import Config


@question.command("resolve")
@click.argument("question_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--by", "by_adr", default=None, help="ADR-NNN that answers the question")
@click.option("--option", "chosen_option", type=int, default=None, help="Chosen option position")
@click.option("--resolution", "-r", default=None, help="Answer text (markdown)")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def question_resolve(
    ctx: click.Context,
    question_id: str,
    project: str,
    by_adr: str | None,
    chosen_option: int | None,
    resolution: str | None,
    author: str,
    reason: str | None,
) -> None:
    """Close with an answer: --by ADR-NNN, --option N and/or --resolution text."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        question_service.resolve(
            session,
            project_id=project_id,
            question_id=question_id,
            resolution=resolution,
            by_adr=by_adr,
            chosen_option=chosen_option,
            author=author,
            reason=reason,
        )
    console.print(f"[green]✅ {question_id} resolved[/green]")


@question.command("drop")
@click.argument("question_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--why", "resolution", required=True, help="Why the question is dropped")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def question_drop(
    ctx: click.Context,
    question_id: str,
    project: str,
    resolution: str,
    author: str,
    reason: str | None,
) -> None:
    """Close without an answer."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        question_service.drop(
            session,
            project_id=project_id,
            question_id=question_id,
            resolution=resolution,
            author=author,
            reason=reason,
        )
    console.print(f"[green]🗄️ {question_id} dropped[/green]")


@question.command("reopen")
@click.argument("question_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def question_reopen(
    ctx: click.Context,
    question_id: str,
    project: str,
    author: str,
    reason: str | None,
) -> None:
    """Return a resolved/dropped question to open."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import question_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)
    with service_errors(), transactional(sf) as session:
        project_id = require_project_id(session, project)
        question_service.reopen(
            session,
            project_id=project_id,
            question_id=question_id,
            author=author,
            reason=reason,
        )
    console.print(f"[green]❓ {question_id} reopened[/green]")
