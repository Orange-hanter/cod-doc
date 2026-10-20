"""CLI доступа к серверу (RFC 29 §3.8, §10.8; CLO-038).

``access init|status`` — хранилище доступа; ``member add/list/remove`` — роли в
проектах; ``token create/list/revoke`` — Bearer-токены; ``actor agent|disable``
— агенты и отключение. Команды работают **на сервере** против его хранилища
(``Config.access_db_url`` или ``$COD_DOC_HOME/access.db``): в контейнере —
``docker compose exec cod-doc cod-doc token create …``. Удалённое
администрирование через https-remote — после ``/api/sync/v1`` (CLO-035).

В MCP ничего из этого не выставлено намеренно (§3.9): выдача токена агентом,
которому этот токен и нужен, — эскалация привилегий.

``--json`` печатается через ``click.echo`` (ADO-176), импорты
``infra``/``services`` — в телах команд (ADO-179).
"""

from __future__ import annotations

import dataclasses
import getpass
import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

import click
from rich.console import Console

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

    from cod_doc.config import Config

console = Console()

_ROLES = ("reader", "writer", "admin")


def _default_author() -> str:
    return f"human:{getpass.getuser()}"


_author_opt = click.option(
    "--author", default=_default_author, show_default="human:<os-user>", help="Кто выдаёт доступ"
)
_json_opt = click.option("--json", "as_json", is_flag=True, help="JSON на stdout")


def _echo_json(payload: object) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


@contextmanager
def _store_session() -> Iterator[Session]:
    """Транзакция в хранилище; нет хранилища или ошибка ввода — ``ClickException``."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import access_service

    factory = access_service.open_store()
    if factory is None:
        msg = f"хранилища доступа нет ({access_service.store_url()}): cod-doc access init"
        raise click.ClickException(msg)
    try:
        with transactional(factory) as session:
            yield session
    except access_service.AccessError as exc:
        raise click.ClickException(str(exc)) from exc


def _project_uid(ctx: click.Context, project: str) -> str:
    from cod_doc.services import access_service

    cfg: Config = ctx.obj["config"]
    entry = cfg.get_project(project)
    if entry is None:
        raise click.ClickException(f"Project not found: {project}")
    try:
        return access_service.project_uid_for(entry)
    except access_service.AccessError as exc:
        raise click.ClickException(str(exc)) from exc


def _slug_by_uid(ctx: click.Context) -> dict[str, str]:
    """``project.uid`` → слаг для вывода; недоступный проект пропускается."""
    from cod_doc.services import access_service

    cfg: Config = ctx.obj["config"]
    out: dict[str, str] = {}
    for entry in cfg.list_projects():
        try:
            out[access_service.project_uid_for(entry)] = entry.name
        except access_service.AccessError:
            continue  # проект без uid — в списке остаётся uid, как и записан
    return out


# --- access -------------------------------------------------------------------


@click.group()
def access() -> None:
    """Хранилище доступа сервера: без него Bearer-гейт выключен."""


@access.command("init")
def access_init() -> None:
    """Создать хранилище (или догнать его схему). С этого момента нужен токен."""
    from cod_doc.services import access_service

    url = access_service.init_store()
    console.print(f"[green]✓[/green] хранилище доступа: {url}")
    console.print(
        "Bearer-гейт включён: /api/v1 и streamable-http MCP пускают только с токеном.\n"
        "Дальше: cod-doc member add <handle> -p <project> --role writer; "
        "cod-doc token create <handle> --label <устройство>"
    )


@access.command("migrate")
def access_migrate() -> None:
    """Догнать схему хранилища, если оно есть; нет — ничего не создавать (bootstrap)."""
    from cod_doc.services import access_service

    url = access_service.upgrade_store()
    console.print(f"[green]✓[/green] хранилище доступа: {url}" if url else "хранилища доступа нет")


@access.command("status")
@_json_opt
def access_status(as_json: bool) -> None:
    """Есть ли хранилище и сколько в нём участников и живых токенов."""
    from cod_doc.services import access_service

    url = access_service.store_url()
    factory = access_service.open_store()
    payload: dict[str, object] = {"store": url, "gate": factory is not None}
    if factory is not None:
        with factory() as session:
            tokens = access_service.list_tokens(session)
            payload["members"] = len(access_service.list_members(session))
            payload["active_tokens"] = sum(1 for t in tokens if t.revoked_at is None)
    if as_json:
        _echo_json(payload)
        return
    state = "[green]включён[/green]" if factory is not None else "[yellow]выключен[/yellow]"
    console.print(f"хранилище: {url}\nгейт: {state}")
    if factory is not None:
        console.print(
            f"участников: {payload['members']}, живых токенов: {payload['active_tokens']}"
        )


# --- member -------------------------------------------------------------------


@click.group()
def member() -> None:
    """Участники проектов и их роли (reader / writer / admin)."""


@member.command("add")
@click.argument("handle")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--role", type=click.Choice(_ROLES), required=True)
@_author_opt
@_json_opt
@click.pass_context
def member_add(
    ctx: click.Context, handle: str, project: str, role: str, author: str, as_json: bool
) -> None:
    """Выдать HANDLE роль в проекте; человек заводится, если его ещё нет."""
    from cod_doc.services import access_service

    uid = _project_uid(ctx, project)
    with _store_session() as session:
        row = access_service.add_member(
            session, handle=handle, project_uid=uid, role=role, author=author
        )
    if as_json:
        _echo_json({**dataclasses.asdict(row), "project": project})
        return
    console.print(f"[green]✓[/green] {handle} → {project}: {role}")


@member.command("remove")
@click.argument("handle")
@click.option("--project", "-p", required=True, help="Project slug")
@_author_opt
@click.pass_context
def member_remove(ctx: click.Context, handle: str, project: str, author: str) -> None:
    """Снять членство HANDLE в проекте."""
    from cod_doc.services import access_service

    uid = _project_uid(ctx, project)
    with _store_session() as session:
        removed = access_service.remove_member(
            session, handle=handle, project_uid=uid, author=author
        )
    console.print(
        f"[green]✓[/green] снято: {handle} ← {project}" if removed else "членства не было"
    )


@member.command("list")
@click.option("--project", "-p", default=None, help="Только этот проект")
@_json_opt
@click.pass_context
def member_list(ctx: click.Context, project: str | None, as_json: bool) -> None:
    """Участники: все или одного проекта."""
    from cod_doc.services import access_service

    uid = _project_uid(ctx, project) if project else None
    slugs = _slug_by_uid(ctx)
    with _store_session() as session:
        rows = access_service.list_members(session, uid)
    items = [{**dataclasses.asdict(r), "project": slugs.get(r.project_uid)} for r in rows]
    if as_json:
        _echo_json(items)
        return
    for item in items:
        where = item["project"] or item["project_uid"]
        console.print(f"{item['handle']:<32} {item['kind']:<6} {where:<24} {item['role']}")


# --- token --------------------------------------------------------------------


@click.group()
def token() -> None:
    """Bearer-токены: открытый текст показывается один раз."""


@token.command("create")
@click.argument("handle")
@click.option("--label", required=True, help="Устройство или агент: macbook, ci, cursor-cloud")
@_author_opt
@_json_opt
def token_create(handle: str, label: str, author: str, as_json: bool) -> None:
    """Выпустить токен HANDLE. Сохраните его сразу — второй раз его не показать."""
    from cod_doc.services import access_service

    with _store_session() as session:
        issued = access_service.create_token(session, handle=handle, label=label, author=author)
    if as_json:
        _echo_json(dataclasses.asdict(issued))
        return
    # Токен — отдельной строкой без разметки rich: его копируют целиком.
    click.echo(issued.token)
    click.echo(
        f"uid {issued.uid} · {issued.handle} · {issued.label} — больше не будет показан",
        err=True,
    )


@token.command("list")
@click.option("--handle", default=None, help="Только токены этого actor'а")
@_json_opt
def token_list(handle: str | None, as_json: bool) -> None:
    """Токены без открытого текста: uid, владелец, метка, последнее использование."""
    from cod_doc.services import access_service

    with _store_session() as session:
        rows = access_service.list_tokens(session, handle)
    if as_json:
        _echo_json([dataclasses.asdict(r) for r in rows])
        return
    for r in rows:
        state = "отозван" if r.revoked_at else "активен"
        used = r.last_used.isoformat(timespec="minutes") if r.last_used else "—"
        console.print(f"{r.uid}  {r.handle:<28} {r.label:<16} {state:<8} {used}")


@token.command("revoke")
@click.argument("token_uid")
@_author_opt
def token_revoke(token_uid: str, author: str) -> None:
    """Отозвать токен по uid (или уникальному префиксу uid). Действует сразу."""
    from cod_doc.services import access_service

    with _store_session() as session:
        revoked = access_service.revoke_token(session, token_uid=token_uid, author=author)
    console.print("[green]✓[/green] отозван" if revoked else "уже отозван")


# --- actor --------------------------------------------------------------------


@click.group()
def actor() -> None:
    """Агенты людей и отключение actor'ов."""


@actor.command("agent")
@click.argument("owner")
@click.argument("name")
@_author_opt
def actor_agent(owner: str, name: str, author: str) -> None:
    """Завести агента OWNER/NAME: роль — не выше владельца, author — только свой."""
    from cod_doc.services import access_service

    with _store_session() as session:
        handle = access_service.add_agent(session, owner=owner, name=name, author=author)
    console.print(
        f"[green]✓[/green] агент {handle}; токен: cod-doc token create {handle} --label …"
    )


@actor.command("disable")
@click.argument("handle")
@_author_opt
def actor_disable(handle: str, author: str) -> None:
    """Отключить HANDLE: его токены и токены его агентов перестают работать сразу."""
    from cod_doc.services import access_service

    with _store_session() as session:
        access_service.disable_actor(session, handle=handle, author=author)
    console.print(f"[green]✓[/green] отключён: {handle}")
