"""CLI одобрений: ``approval list|show|approve|deny|cancel`` (ACU-010, RFC 28 §3.6).

Зеркало MCP ``approval_*``. Главный потребитель — человек, который разбирает
предложения фонового куратора (``doc_patch``): ``show`` печатает diff и
обоснование, ``approve`` исполняет правку в той же транзакции (ACU-009) или
сообщает, что она устарела.

Запросить одобрение (``request``) из CLI нельзя намеренно: это действие
агента, который ждёт решения, а не человека, который его принимает.
"""

from __future__ import annotations

import getpass
import json as _json
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config

console = Console()

#: Кто решает по умолчанию: одобряет человек за терминалом.
_DEFAULT_BY = f"human:{getpass.getuser()}"


def _open(cfg: Config, project: str) -> tuple[sessionmaker[Session], Engine]:
    from cod_doc.infra.db import db_for_entry

    entry = cfg.get_project(project)
    if entry is None:
        raise click.ClickException(f"Проект не найден: {project}")
    return db_for_entry(entry)


def _project_id(session: Session, project: str) -> int:
    from cod_doc.infra.repositories import ProjectRepository

    row = ProjectRepository(session).get_by_slug(project)
    if row is None or row.row_id is None:
        raise click.ClickException(f"Проект '{project}' не заведён в БД")
    return row.row_id


def _emit(payload: dict[str, Any]) -> None:
    click.echo(_json.dumps(payload, ensure_ascii=False, indent=2, default=str))


@click.group()
def approval() -> None:
    """Одобрения: предложения куратора и прочие решения человека."""


@approval.command("list")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option(
    "--status",
    type=click.Choice(["pending", "approved", "denied", "cancelled", "expired", "all"]),
    default="pending",
    show_default=True,
)
@click.option("--type", "approval_type", default=None, help="Тип, например doc_patch")
@click.option("--limit", default=50, show_default=True, type=int)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def approval_list(
    ctx: click.Context,
    project: str,
    status: str,
    approval_type: str | None,
    limit: int,
    as_json: bool,
) -> None:
    """Список одобрений (по умолчанию — ждущие решения)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import approval_service

    factory, engine = _open(ctx.obj["config"], project)
    try:
        with transactional(factory, commit=False) as session:
            page = approval_service.list_approvals(
                session,
                _project_id(session, project),
                status=None if status == "all" else status,
                approval_type=approval_type,
                limit=limit,
            )
    finally:
        engine.dispose()

    if as_json:
        _emit(page)
        return
    if not page["items"]:
        console.print("[green]Ничего не ждёт решения.[/green]")
        return
    table = Table(show_header=True, box=None, padding=(0, 1))
    for column in ("ID", "Тип", "Статус", "Кто просит", "Что"):
        table.add_column(column)
    for item in page["items"]:
        payload = item.get("payload") or {}
        what = payload.get("rationale") or payload.get("op") or ""
        table.add_row(
            item["approval_id"][:8],
            item["approval_type"],
            item["status"],
            item["requested_by"],
            str(what)[:60],
        )
    console.print(table)
    console.print(f"[dim]Всего: {page['total']}[/dim]")


@approval.command("show")
@click.argument("approval_id")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def approval_show(ctx: click.Context, approval_id: str, project: str, as_json: bool) -> None:
    """Одобрение целиком; у doc_patch — операция, обоснование и diff."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import approval_service

    factory, engine = _open(ctx.obj["config"], project)
    try:
        with transactional(factory, commit=False) as session:
            found = approval_service.get(
                session, _project_id(session, project), _full_id(session, project, approval_id)
            )
            data = approval_service.to_dict(found) if found else None
    finally:
        engine.dispose()
    if data is None:
        raise click.ClickException(f"Одобрение {approval_id} не найдено")
    if as_json:
        _emit(data)
        return
    payload = data.get("payload") or {}
    console.print(
        f"[bold]{data['approval_id']}[/bold]  {data['approval_type']} · {data['status']} · "
        f"просит {data['requested_by']}"
    )
    if payload.get("op"):
        console.print(
            f"Операция: {payload['op']}  {_json.dumps(payload.get('args'), ensure_ascii=False)}"
        )
    if payload.get("rationale"):
        console.print(f"Почему: {payload['rationale']}")
    if payload.get("diff"):
        console.print(payload["diff"], markup=False, highlight=False)


def _full_id(session: Session, project: str, prefix: str) -> str:
    """Полный id по префиксу из ``list`` (там печатаются первые 8 символов)."""
    from cod_doc.services import approval_service

    if len(prefix) >= 36:  # noqa: PLR2004 — длина UUID
        return prefix
    page = approval_service.list_approvals(session, _project_id(session, project), limit=500)
    matches = [i["approval_id"] for i in page["items"] if i["approval_id"].startswith(prefix)]
    if len(matches) != 1:
        raise click.ClickException(
            f"по префиксу {prefix!r} найдено {len(matches)} одобрений — уточни id"
        )
    return str(matches[0])


def _resolve(
    ctx: click.Context,
    *,
    project: str,
    approval_id: str,
    decision: str,
    by: str,
    comment: str | None,
) -> dict[str, Any]:
    from cod_doc.infra.db import transactional
    from cod_doc.services import approval_service, curator_ops

    factory, engine = _open(ctx.obj["config"], project)
    try:
        with transactional(factory) as session:
            result: dict[str, Any] = approval_service.resolve(
                session,
                _project_id(session, project),
                _full_id(session, project, approval_id),
                decision=decision,
                resolved_by=by,
                comment=comment,
            )
    except (LookupError, ValueError, curator_ops.CuratorOpError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        engine.dispose()
    return result


@approval.command("approve")
@click.argument("approval_id")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--by", default=_DEFAULT_BY, show_default=True, help="Кто одобряет")
@click.option("--comment", default=None)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def approval_approve(
    ctx: click.Context,
    approval_id: str,
    project: str,
    by: str,
    comment: str | None,
    as_json: bool,
) -> None:
    """Одобрить. У doc_patch правка применяется сразу — или approval истекает."""
    result = _resolve(
        ctx, project=project, approval_id=approval_id, decision="approve", by=by, comment=comment
    )
    if as_json:
        _emit(result)
        return
    status = result["approval"]["status"]
    if status == "expired":
        console.print(
            "[yellow]Правка устарела — документ изменили после предложения; "
            f"ничего не записано. {result['approval'].get('decision_comment') or ''}[/yellow]"
        )
        return
    applied = result.get("applied")
    console.print(f"[green]Одобрено.[/green] {'Применено: ' + str(applied) if applied else ''}")


@approval.command("deny")
@click.argument("approval_id")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--by", default=_DEFAULT_BY, show_default=True, help="Кто отклоняет")
@click.option("--reason", default=None, help="Почему — запомнится для куратора")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def approval_deny(
    ctx: click.Context,
    approval_id: str,
    project: str,
    by: str,
    reason: str | None,
    as_json: bool,
) -> None:
    """Отклонить. Тот же doc_patch куратор не предложит 30 дней."""
    result = _resolve(
        ctx, project=project, approval_id=approval_id, decision="deny", by=by, comment=reason
    )
    if as_json:
        _emit(result)
        return
    console.print("[green]Отклонено.[/green]")


@approval.command("cancel")
@click.argument("approval_id")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--reason", required=True, help="Почему отменяется")
@click.option("--by", default=_DEFAULT_BY, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def approval_cancel(
    ctx: click.Context,
    approval_id: str,
    project: str,
    reason: str,
    by: str,
    as_json: bool,
) -> None:
    """Отменить ждущее одобрение (вопрос снят, решать больше нечего)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import approval_service

    factory, engine = _open(ctx.obj["config"], project)
    try:
        with transactional(factory) as session:
            cancelled = approval_service.cancel(
                session,
                _project_id(session, project),
                _full_id(session, project, approval_id),
                reason=reason,
                cancelled_by=by,
            )
            data = approval_service.to_dict(cancelled)
    except (LookupError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        engine.dispose()
    if as_json:
        _emit(data)
        return
    console.print("[green]Отменено.[/green]")
