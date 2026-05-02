"""`plan forward` / `plan reverse` — graph chain queries."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _render_chain, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.command("forward")
@click.argument("task_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def plan_forward(ctx: click.Context, task_id: str, project: str, as_json: bool) -> None:
    """Forward chain: prerequisites of TASK_ID (must complete BEFORE it)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service
    from cod_doc.services.plan_service import TaskNotFoundInPlanError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            chain = plan_service.forward_chain(session, task_id)
    except TaskNotFoundInPlanError:
        console.print(f"[red]Task '{task_id}' not found.[/red]")
        sys.exit(1)

    _render_chain(chain, "Forward", task_id, as_json)


@plan.command("reverse")
@click.argument("task_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def plan_reverse(ctx: click.Context, task_id: str, project: str, as_json: bool) -> None:
    """Reverse chain: tasks unblocked when TASK_ID completes."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service
    from cod_doc.services.plan_service import TaskNotFoundInPlanError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            chain = plan_service.reverse_chain(session, task_id)
    except TaskNotFoundInPlanError:
        console.print(f"[red]Task '{task_id}' not found.[/red]")
        sys.exit(1)

    _render_chain(chain, "Reverse", task_id, as_json)
