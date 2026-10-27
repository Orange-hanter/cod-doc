"""`plan move` — переставить план в очереди проекта (RFC 36 §3.6, ROAD-002).

Зеркало MCP-тула ``plan_move`` поверх ``plan_service.move_plan``. Порядок
планов — ``plan.rank``; он же порядок ``plan list`` и дорожной карты.

``--json`` печатается через ``click.echo``, а не rich: перенос строки и
разметка молча портят значения (ADO-176).
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click

from ._common import _guard, _make_session, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.command("move")
@click.argument("scope")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--before", default=None, help="Поставить перед планом с этим scope")
@click.option("--after", default=None, help="Поставить после плана с этим scope")
@click.option("--author", default="cli", show_default=True, help="Автор ревизии")
@click.option("--reason", default=None, help="Причина для ревизии")
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def plan_move(
    ctx: click.Context,
    scope: str,
    project: str,
    before: str | None,
    after: str | None,
    author: str,
    reason: str | None,
    as_json: bool,
) -> None:
    """Переставить план SCOPE перед/после другого плана проекта.

    Нужна ровно одна из опций --before / --after. Новый ранг получает только
    SCOPE; пишет ревизию и событие plan.moved.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service
    from cod_doc.services.project_lookup import require_project_id

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with _guard(), transactional(sf) as session:
        ordered = plan_service.move_plan(
            session,
            project_id=require_project_id(session, project),
            plan_scope=scope,
            author=author,
            before=before,
            after=after,
            reason=reason,
        )
        order = [p.scope for p in ordered]

    if as_json:
        click.echo(_json.dumps({"scope": scope, "order": order}, ensure_ascii=False))
        return

    console.print(f"✅ Порядок планов: {' → '.join(order)}", style="green", markup=False)
