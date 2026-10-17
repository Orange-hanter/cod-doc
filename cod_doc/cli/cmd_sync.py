"""CLI синхронизации с remote (RFC 29 §10.8; CLO-015).

Верхние команды ``remote add/list/remove``, ``fetch``, ``pull``, ``push`` и
группа ``sync status/log/verify``. ``-p/--project`` обязателен. ``--json``
печатается через ``click.echo`` (ADO-176), импорты ``infra``/``services`` —
в телах команд (ADO-179). Ошибки — ``click.ClickException`` (exit 1) с
сообщением, начинающимся с кода ``SYNC-NNN`` (§10.10).

Команды поздних секций ещё не выставлены: ``remote login``, ``clone``,
``sync conflicts/resolve``, ``sync export-pending``, ``member``, ``token`` —
их сервисов пока нет (CLO-026, CLO-029, CLO-032, CLO-038).
"""

from __future__ import annotations

import dataclasses
import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

import click
from rich.console import Console

from cod_doc.cli._common import printable

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config

console = Console()

_DEFAULT_AUTHOR = "cli"
_SHORT_SHA = 12
_KIND_CHOICES = ("auto", "file", "s3", "https")


def _echo_json(payload: object) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _as_dict(obj: object) -> object:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def _short(sha: str | None) -> str:
    return sha[:_SHORT_SHA] if sha else "—"


@contextmanager
def _sync_errors() -> Iterator[None]:
    """Ошибки синхронизации → ``ClickException`` (exit 1), текст начинается с кода."""
    from cod_doc.infra.sync_capture import SyncCaptureError
    from cod_doc.services.sync_service.errors import MergeRequiredError, SyncError

    try:
        yield
    except (SyncError, SyncCaptureError, MergeRequiredError) as exc:
        raise click.ClickException(str(exc)) from exc


def _factory(ctx: click.Context, project: str) -> sessionmaker[Session]:
    from cod_doc.infra.db import db_for_entry

    cfg: Config = ctx.obj["config"]
    entry = cfg.get_project(project)
    if entry is None:
        raise click.ClickException(f"Project not found: {project}")
    factory, _engine = db_for_entry(entry)
    return factory


_project_opt = click.option("--project", "-p", required=True, help="Project slug")
_remote_opt = click.option(
    "--remote", "remote_name", default="origin", show_default=True, help="Remote name"
)
_json_opt = click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output")
_author_opt = click.option("--author", default=_DEFAULT_AUTHOR, show_default=True)


# --------------------------------------------------------------------------- #
# remote add / list / remove                                                   #
# --------------------------------------------------------------------------- #


@click.group()
def remote() -> None:
    """Remotes проекта в ~/.cod-doc/config.yaml (RFC 29 §10.12)."""


@remote.command("add")
@click.argument("name")
@click.argument("url")
@_project_opt
@click.option(
    "--kind",
    type=click.Choice(_KIND_CHOICES),
    default="auto",
    show_default=True,
    help="Вид remote; auto — по схеме URL",
)
@_json_opt
def remote_add(name: str, url: str, project: str, kind: str, as_json: bool) -> None:
    """Добавить (или заменить) remote NAME → URL."""
    from cod_doc.services.sync_service import remotes

    with _sync_errors():
        added = remotes.add(project, name, url, kind=kind)
    for warning in added.warnings:
        click.echo(f"warning: {warning}", err=True)
    if as_json:
        _echo_json(_as_dict(added))
        return
    verb = "заменён" if added.replaced else "добавлен"
    console.print(
        f"remote {printable(name)} {verb}: {printable(added.remote.url)} ({added.remote.kind})",
        markup=False,
    )


@remote.command("list")
@_project_opt
@_json_opt
def remote_list(project: str, as_json: bool) -> None:
    """Перечислить remotes проекта."""
    from cod_doc.services.sync_service import remotes

    with _sync_errors():
        items = remotes.list_for(project)
    if as_json:
        _echo_json([_as_dict(item) for item in items])
        return
    if not items:
        console.print("remotes не настроены — cod-doc remote add NAME URL -p …", markup=False)
        return
    for item in items:
        console.print(f"{printable(item.name)}\t{printable(item.url)}\t{item.kind}", markup=False)


@remote.command("remove")
@click.argument("name")
@_project_opt
@_json_opt
def remote_remove(name: str, project: str, as_json: bool) -> None:
    """Удалить remote NAME из конфига проекта (состояние БД не трогается)."""
    from cod_doc.services.sync_service import remotes

    with _sync_errors():
        remotes.remove(project, name)
    if as_json:
        _echo_json({"removed": name})
        return
    console.print(f"remote {printable(name)} удалён", markup=False)


# --------------------------------------------------------------------------- #
# fetch / pull / push                                                          #
# --------------------------------------------------------------------------- #


@click.command("fetch")
@_project_opt
@_remote_opt
@_json_opt
@click.pass_context
def fetch(ctx: click.Context, project: str, remote_name: str, as_json: bool) -> None:
    """Скачать и проверить новые changeset'ы, не применяя."""
    from cod_doc.services.sync_service import pull as pull_mod

    sf = _factory(ctx, project)
    with _sync_errors():
        new = pull_mod.fetch(sf, project, remote=remote_name)
    if as_json:
        _echo_json({"remote": remote_name, "new": new})
        return
    if not new:
        console.print(f"{remote_name}: новых changeset'ов нет", markup=False)
        return
    console.print(f"{remote_name}: новых changeset'ов {len(new)} — cod-doc pull", markup=False)
    for sha in new:
        console.print(f"  {_short(sha)}", markup=False)


@click.command("pull")
@_project_opt
@_remote_opt
@_author_opt
@click.option("--dry-run", is_flag=True, help="Показать, что применится, ничего не записывая")
@_json_opt
@click.pass_context
def pull(
    ctx: click.Context, project: str, remote_name: str, author: str, dry_run: bool, as_json: bool
) -> None:
    """Fetch + fast-forward: применить чужие changeset'ы."""
    from cod_doc.services.sync_service import pull as pull_mod

    sf = _factory(ctx, project)
    with _sync_errors():
        result = pull_mod.pull(sf, project, remote=remote_name, author=author, dry_run=dry_run)
    if as_json:
        _echo_json(_as_dict(result))
        return
    prefix = "dry-run: применилось бы" if dry_run else "применено"
    console.print(
        f"{prefix} changeset'ов {len(result.applied)}, операций {result.ops}; "
        f"голова {_short(result.head)}",
        markup=False,
    )


@click.command("push")
@_project_opt
@_remote_opt
@_author_opt
@click.option("--no-retry", is_flag=True, help="Не повторять через pull при отказе CAS")
@_json_opt
@click.pass_context
def push(
    ctx: click.Context, project: str, remote_name: str, author: str, no_retry: bool, as_json: bool
) -> None:
    """Упаковать неотправленные операции и сдвинуть refs/main remote."""
    from cod_doc.services.sync_service import push as push_mod

    sf = _factory(ctx, project)
    retries = 0 if no_retry else push_mod.DEFAULT_MAX_RETRIES
    with _sync_errors():
        result = push_mod.push(sf, project, remote=remote_name, author=author, max_retries=retries)
    if as_json:
        _echo_json(_as_dict(result))
        return
    if not result.changesets:
        console.print(f"нечего отправлять; голова {_short(result.head)}", markup=False)
        return
    console.print(
        f"отправлено операций {result.ops} в {_short(result.head)} (повторов: {result.retries})",
        markup=False,
    )


# --------------------------------------------------------------------------- #
# sync status / log / verify                                                   #
# --------------------------------------------------------------------------- #


@click.group()
def sync() -> None:
    """Состояние синхронизации с remote (RFC 29 §3.9)."""


@sync.command("status")
@_project_opt
@_remote_opt
@click.option("--verify", "with_verify", is_flag=True, help="Сверить дайджест с remote")
@_json_opt
@click.pass_context
def sync_status(
    ctx: click.Context, project: str, remote_name: str, with_verify: bool, as_json: bool
) -> None:
    """Голова, ahead/behind, неотправленные операции, restricted-документы."""
    from cod_doc.services.sync_service import status as status_mod

    sf = _factory(ctx, project)
    with _sync_errors():
        st = status_mod.status(sf, project, remote=remote_name, verify=with_verify)
    if as_json:
        _echo_json(_as_dict(st))
        return
    lines = [
        f"remote:          {st.remote or '— (не настроен)'}",
        f"голова реплики:  {_short(st.head_local)}",
        f"голова remote:   {_short(st.head_remote)}",
        f"ahead / behind:  {st.ahead} / {st.behind}",
        f"не отправлено:   {st.pending_ops} операций",
        f"конфликты:       {st.open_conflicts}",
        f"эпоха:           {st.epoch_local}",
        f"restricted:      {st.restricted_local} документов не уезжают",
    ]
    if st.verify is not None:
        mark = "совпал" if st.verify.equal else "НЕ совпал"
        lines.append(f"дайджест:        {mark} ({_short(st.verify.local)})")
    if st.drift_hint:
        lines.append(f"подсказка:       {st.drift_hint}")
    for line in lines:
        console.print(printable(line), markup=False)


@sync.command("log")
@_project_opt
@click.option("-n", "limit", type=click.IntRange(min=1), default=20, show_default=True)
@_json_opt
@click.pass_context
def sync_log(ctx: click.Context, project: str, limit: int, as_json: bool) -> None:
    """Changeset'ы истории реплики: автор, реплика, число операций."""
    from cod_doc.services.sync_service import status as status_mod

    sf = _factory(ctx, project)
    with _sync_errors():
        entries = status_mod.log(sf, project, limit=limit)
    if as_json:
        _echo_json([_as_dict(e) for e in entries])
        return
    if not entries:
        console.print("история пуста — push или pull ещё не было", markup=False)
        return
    for e in entries:
        console.print(
            printable(f"{_short(e.sha)}  {e.actor}  {_short(e.replica)}  ops={e.ops}"),
            markup=False,
        )


@sync.command("verify")
@_project_opt
@_remote_opt
@_json_opt
@click.pass_context
def sync_verify(ctx: click.Context, project: str, remote_name: str, as_json: bool) -> None:
    """Сверить дайджест реплики с дайджестом головы remote; расхождение — exit 1."""
    from cod_doc.services.sync_service import digest

    sf = _factory(ctx, project)
    with _sync_errors():
        result = digest.verify(sf, project, remote=remote_name)
    if as_json:
        _echo_json(_as_dict(result))
    elif result.equal:
        console.print(f"дайджест совпал: {_short(result.local)}", markup=False)
    else:
        console.print(
            f"дайджест НЕ совпал: реплика {_short(result.local)}, remote {_short(result.remote)}",
            markup=False,
        )
    if not result.equal:
        ctx.exit(1)
