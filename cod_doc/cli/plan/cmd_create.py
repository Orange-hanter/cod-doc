"""`plan create` — завести план (RFC 26 §5, ADO-204).

Зеркало MCP-тула ``plan_create`` поверх ``plan_service.create_plan``.
Секции этой командой не создаются — для них группа ``plan section``.

``--json`` печатается через ``click.echo``, а не rich: перенос строки и
разметка молча портят значения (ADO-176).
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click

from ._common import _guard, _make_session, _project_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.command("create")
@click.argument("scope")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--principle", required=True, help="Принцип плана — одна строка о его сути")
@click.option("--author", default="cli", show_default=True, help="Автор ревизии")
@click.option("--reason", default=None, help="Причина для ревизии")
@click.option(
    "--id-prefix",
    default=None,
    help="Префикс ID новых задач плана (2-5 заглавных, напр. WEB) — ADO-243",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def plan_create(
    ctx: click.Context,
    scope: str,
    project: str,
    principle: str,
    author: str,
    reason: str | None,
    id_prefix: str | None,
    as_json: bool,
) -> None:
    """Создать план SCOPE в проекте. Scope уникален на всю БД.

    Пишет ревизию и событие plan.created. Секции не создаются —
    добавляй их через `cod-doc plan section create`.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    # Фаза 1: проект (только чтение — sys.exit здесь безопасен).
    with transactional(sf) as session:
        pid = _project_id(session, project)

    # Фаза 2: запись; ошибка откатывает транзакцию, _guard печатает сообщение.
    with _guard(), transactional(sf) as session:
        created = plan_service.create_plan(
            session,
            project_id=pid,
            scope=scope,
            principle=principle,
            author=author,
            reason=reason,
            id_prefix=id_prefix,
        )
        payload = {
            "plan_id": created.row_id,
            "scope": created.scope,
            "principle": created.principle,
            "id_prefix": created.id_prefix,
        }

    if as_json:
        click.echo(_json.dumps(payload, ensure_ascii=False))
        return

    console.print(
        f"✅ План {payload['scope']} создан. Секции: cod-doc plan section create",
        style="green",
        markup=False,
    )
