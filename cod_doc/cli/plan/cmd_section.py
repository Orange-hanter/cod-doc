"""`plan section …` — секции плана из CLI (RFC 26 §5, ADO-204).

Зеркало MCP-тулов ``plan_section_create`` / ``plan_sections_list`` поверх
``plan_service``. Импорты ``infra``/``services`` — только в телах команд
(ADO-179); ``--json`` печатается через ``click.echo``, а не rich (ADO-176).
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click
from rich.table import Table
from rich.text import Text

from ._common import _fail, _guard, _make_session, _project_id, console
from ._group import plan

if TYPE_CHECKING:
    from cod_doc.config import Config


@plan.group("section")
def plan_section() -> None:
    """Секции плана: write-путь через plan_service.

    Каждая запись оставляет ревизию и activity event (ADO-040).
    """


@plan_section.command("create")
@click.argument("plan_scope")
@click.argument("letter")
@click.argument("title")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--slug", default=None, help="Явный слаг секции; без него генерируется")
@click.option("--position", type=int, default=None, help="Позиция; по умолчанию — в конец")
@click.option("--author", default="cli", show_default=True, help="Автор ревизии")
@click.option("--reason", default=None, help="Причина для ревизии")
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def section_create(
    ctx: click.Context,
    plan_scope: str,
    letter: str,
    title: str,
    project: str,
    slug: str | None,
    position: int | None,
    author: str,
    reason: str | None,
    as_json: bool,
) -> None:
    """Добавить секцию LETTER с заголовком TITLE в план PLAN_SCOPE.

    Пишет ревизию и событие plan.section_created. HTML-сущности в заголовке
    не заменяются — только предупреждение.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service, validation

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    # Фаза 1: проект (только чтение — sys.exit здесь безопасен).
    with transactional(sf) as session:
        pid = _project_id(session, project)

    # Фаза 2: запись; ошибка откатывает транзакцию, _guard печатает сообщение.
    with _guard(), transactional(sf) as session:
        created = plan_service.create_section(
            session,
            project_id=pid,
            plan_scope=plan_scope,
            letter=letter,
            title=title,
            author=author,
            slug=slug,
            position=position,
            reason=reason,
        )
        payload: dict[str, object] = {
            "section_id": created.row_id,
            "plan_scope": plan_scope,
            "letter": created.letter,
            "title": created.title,
            "slug": created.slug,
            "position": created.position,
        }

    warnings = [i.message for i in validation.audit_html_escaped_text(title, field="title")]

    if as_json:
        if warnings:
            payload["warnings"] = warnings
        click.echo(_json.dumps(payload, ensure_ascii=False))
        return

    console.print(
        f"✅ Секция {payload['letter']} ({payload['slug']}) добавлена в план {plan_scope}",
        style="green",
        markup=False,
    )
    for message in warnings:
        console.print(f"⚠ {message}", style="yellow", markup=False)


@plan_section.command("list")
@click.argument("plan_scope")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def section_list(ctx: click.Context, plan_scope: str, project: str, as_json: bool) -> None:
    """Секции плана PLAN_SCOPE в порядке хранения."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        pid = _project_id(session, project)
        found = plan_service.get_by_scope(session, plan_scope)
        # scope уникален на всю БД: без сверки project_id чужой план
        # читался бы из любого проекта. Текст тот же, что у сервиса.
        if found is None or found.row_id is None or found.project_id != pid:
            _fail(f"Plan '{plan_scope}' not found in project")
            return
        rows = [
            {
                "letter": s.letter,
                "title": s.title,
                "slug": s.slug,
                "position": s.position,
                "doc_id": s.doc_id,
            }
            for s in plan_service.list_sections(session, found.row_id)
        ]

    if as_json:
        click.echo(_json.dumps(rows, ensure_ascii=False))
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Letter", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Slug")
    table.add_column("Position", justify="right")
    table.add_column("Doc", justify="right")
    for r in rows:
        table.add_row(
            Text(str(r["letter"])),
            Text(str(r["title"])),
            Text(str(r["slug"])),
            str(r["position"]),
            "" if r["doc_id"] is None else str(r["doc_id"]),
        )
    console.print(table)


@plan_section.command("update")
@click.argument("plan_scope")
@click.argument("letter")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--title", default=None, help="Новый заголовок")
@click.option("--slug", default=None, help="Новый слаг")
@click.option("--doc-id", "doc_id", type=int, default=None, help="Привязать к документу проекта")
@click.option(
    "--adopt",
    is_flag=True,
    default=False,
    help="Взять в историю секцию без ревизий: ревизия пишется, даже если менять нечего",
)
@click.option("--author", default="cli", show_default=True, help="Автор ревизии")
@click.option("--reason", required=True, help="Причина для ревизии (RFC 26 §4)")
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def section_update(
    ctx: click.Context,
    plan_scope: str,
    letter: str,
    project: str,
    title: str | None,
    slug: str | None,
    doc_id: int | None,
    adopt: bool,
    author: str,
    reason: str,
    as_json: bool,
) -> None:
    """Поправить заголовок, слаг или документ секции LETTER плана PLAN_SCOPE.

    Не переданная опция поле не трогает. Вызов без изменений ничего не пишет
    (кроме --adopt). HTML-сущности в заголовке — только предупреждение.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service, validation

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        pid = _project_id(session, project)

    with _guard(), transactional(sf) as session:
        updated = plan_service.update_section(
            session,
            project_id=pid,
            plan_scope=plan_scope,
            letter=letter,
            author=author,
            reason=reason,
            title=title,
            slug=slug,
            doc_id=doc_id,
            adopt=adopt,
        )
        payload: dict[str, object] = {
            "section_id": updated.row_id,
            "plan_scope": plan_scope,
            "letter": updated.letter,
            "title": updated.title,
            "slug": updated.slug,
            "position": updated.position,
            "doc_id": updated.doc_id,
        }

    warnings = (
        [i.message for i in validation.audit_html_escaped_text(title, field="title")]
        if title is not None
        else []
    )

    if as_json:
        if warnings:
            payload["warnings"] = warnings
        click.echo(_json.dumps(payload, ensure_ascii=False))
        return

    console.print(
        f"✅ Секция {payload['letter']} ({payload['slug']}) плана {plan_scope} обновлена",
        style="green",
        markup=False,
    )
    for message in warnings:
        console.print(f"⚠ {message}", style="yellow", markup=False)


@plan_section.command("move")
@click.argument("plan_scope")
@click.argument("letter")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option("--before", default=None, help="Поставить перед секцией с этой буквой")
@click.option("--after", default=None, help="Поставить после секции с этой буквой")
@click.option("--position", type=int, default=None, help="Поставить на позицию (0 — первая)")
@click.option("--author", default="cli", show_default=True, help="Автор ревизии")
@click.option("--reason", required=True, help="Причина для ревизии (RFC 26 §4)")
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def section_move(
    ctx: click.Context,
    plan_scope: str,
    letter: str,
    project: str,
    before: str | None,
    after: str | None,
    position: int | None,
    author: str,
    reason: str,
    as_json: bool,
) -> None:
    """Переставить секцию LETTER плана PLAN_SCOPE.

    Нужна ровно одна из опций --before / --after / --position. После вызова
    позиции всех секций плана плотные: 0..n-1.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        pid = _project_id(session, project)

    with _guard(), transactional(sf) as session:
        ordered = plan_service.move_section(
            session,
            project_id=pid,
            plan_scope=plan_scope,
            letter=letter,
            author=author,
            reason=reason,
            before=before,
            after=after,
            position=position,
        )
        payload: dict[str, object] = {
            "plan_scope": plan_scope,
            "letter": letter.strip().upper(),
            "order": [{"letter": s.letter, "position": s.position} for s in ordered],
        }
        letters = [s.letter for s in ordered]

    if as_json:
        click.echo(_json.dumps(payload, ensure_ascii=False))
        return

    console.print(
        f"✅ Порядок секций плана {plan_scope}: {' '.join(letters)}",
        style="green",
        markup=False,
    )


@plan_section.command("rm")
@click.argument("plan_scope")
@click.argument("letter")
@click.option("--project", "-p", required=True, help="Слаг проекта")
@click.option(
    "--reassign-to",
    "reassign_to",
    default=None,
    help="Буква секции, куда перенести задачи удаляемой",
)
@click.option("--author", default="cli", show_default=True, help="Автор ревизии")
@click.option("--reason", required=True, help="Причина для ревизии (RFC 26 §4)")
@click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")
@click.pass_context
def section_rm(
    ctx: click.Context,
    plan_scope: str,
    letter: str,
    project: str,
    reassign_to: str | None,
    author: str,
    reason: str,
    as_json: bool,
) -> None:
    """Удалить секцию LETTER плана PLAN_SCOPE.

    Секция с задачами удаляется только с --reassign-to: задачи переезжают в
    указанную секцию.

    \f
    Флага ``--force`` нет намеренно: ``task.section_id`` — NOT NULL с
    ``ondelete=CASCADE``, и удаление непустой секции снесло бы её задачи, а их
    ревизии и события остались бы сиротами. ``\\f`` выше отрезает этот абзац
    от ``--help``: упоминание флага там читалось бы как его наличие.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import plan_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        pid = _project_id(session, project)

    with _guard(), transactional(sf) as session:
        moved = plan_service.delete_section(
            session,
            project_id=pid,
            plan_scope=plan_scope,
            letter=letter,
            author=author,
            reason=reason,
            reassign_to=reassign_to,
        )
        payload: dict[str, object] = {
            "plan_scope": plan_scope,
            "letter": letter.strip().upper(),
            "deleted": True,
            "reassign_to": reassign_to.strip().upper() if reassign_to is not None else None,
            "moved_tasks": moved,
        }

    if as_json:
        click.echo(_json.dumps(payload, ensure_ascii=False))
        return

    tail = f", задач перенесено в {payload['reassign_to']}: {moved}" if reassign_to else ""
    console.print(
        f"✅ Секция {payload['letter']} удалена из плана {plan_scope}{tail}",
        style="green",
        markup=False,
    )
