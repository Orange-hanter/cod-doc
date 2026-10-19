"""CLI синхронизации с remote (RFC 29 §10.8; CLO-015).

Верхние команды ``remote add/list/remove``, ``fetch``, ``pull``, ``push`` и
группа ``sync status/log/verify/conflicts/resolve`` (конфликты — CLO-026). ``-p/--project`` обязателен. ``--json``
печатается через ``click.echo`` (ADO-176), импорты ``infra``/``services`` —
в телах команд (ADO-179). Ошибки — ``click.ClickException`` (exit 1) с
сообщением, начинающимся с кода ``SYNC-NNN`` (§10.10).

``clone URL [DIR]`` (CLO-029) заводит проект с remote: снапшот + хвост,
регистрация в реестре. ``sync export-pending`` (CLO-032) выгружает
незапушенные операции в файл или stdout. Команды поздних секций ещё не
выставлены: ``remote login``, ``member``, ``token`` — их сервисов пока нет
(CLO-038).
"""

from __future__ import annotations

import dataclasses
import json
from contextlib import contextmanager
from pathlib import Path
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
# clone                                                                        #
# --------------------------------------------------------------------------- #


@click.command("clone")
@click.argument("url")
@click.argument("directory", required=False)
@click.option("--project", "slug", default=None, help="Project slug (по умолчанию — с remote)")
@_author_opt
@_json_opt
def clone(url: str, directory: str | None, slug: str | None, author: str, as_json: bool) -> None:
    """Завести проект с remote: последний снапшот + хвост, регистрация в реестре.

    URL — каталог проекта на remote: <URL remote>/<project_uid>
    (его печатает `cod-doc sync status`). DIR — корень проекта,
    по умолчанию ./<slug>.
    """
    from cod_doc.services.sync_service import clone as clone_mod

    root = Path(directory) if directory is not None else None
    with _sync_errors():
        result = clone_mod.clone(url, root=root, slug=slug, author=author)
    if as_json:
        _echo_json(_as_dict(result))
        return
    origin = "со снапшота " + _short(result.snapshot) if result.snapshot else "без снапшота"
    console.print(
        f"проект {result.project} заведён в {result.root} {origin}; "
        f"хвост: changeset'ов {len(result.applied)}, операций {result.ops}; "
        f"голова {_short(result.head)}",
        markup=False,
    )


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
    if result.epoch_opened:
        console.print(f"открыта эпоха {result.epoch_opened} (снапшот записан)", markup=False)


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
        f"эпоха:           {st.epoch_local} (remote: {st.epoch_remote or '—'})",
        f"restricted:      {st.restricted_local} документов не уезжают",
    ]
    if st.verify is not None:
        mark = "совпал" if st.verify.equal else "НЕ совпал"
        lines.append(f"дайджест:        {mark} ({_short(st.verify.local)})")
    if st.drift_hint:
        lines.append(f"подсказка:       {st.drift_hint}")
    if st.clone_url:
        lines.append(f"clone:           cod-doc clone {st.clone_url}")
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


@sync.command("export-pending")
@_project_opt
@click.option(
    "--out",
    "out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Файл выгрузки (новый, 0600); без опции — в stdout",
)
@click.pass_context
def sync_export_pending(ctx: click.Context, project: str, out: Path | None) -> None:
    """Незапушенные операции — в файл, для ручного переноса после смены эпохи (CLO-032)."""
    from cod_doc.services.sync_service import epochs

    sf = _factory(ctx, project)
    with _sync_errors():
        if out is None:
            data, _count = epochs.render_pending(sf, project)
            click.echo(data.decode("utf-8"), nl=False)
            return
        try:
            count = epochs.export_pending(sf, project, path=out)
        except FileExistsError as exc:
            raise click.ClickException(f"{out}: файл уже есть — выбери другой --out") from exc
    click.echo(f"{epochs.pending_phrase(count)} выгружено в {out}", err=True)


def _project_id(session: Session, project: str) -> int:
    from cod_doc.services.project_lookup import require_project_id

    try:
        return require_project_id(session, project)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _conflict_target(c: dict[str, object]) -> str:
    target = f"{c['entity_kind']} {c['entity_uid']}"
    return f"{target}.{c['field']}" if c["field"] else target


@sync.command("conflicts")
@_project_opt
@click.option(
    "--status",
    type=click.Choice(("open", "resolved", "all")),
    default="open",
    show_default=True,
)
@_json_opt
@click.pass_context
def sync_conflicts(ctx: click.Context, project: str, status: str, as_json: bool) -> None:
    """Конфликты слияния: в БД «наше», «их» лежит в конфликте (RFC 29 §3.7)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services.sync_service.conflicts import list_conflicts

    sf = _factory(ctx, project)
    with _sync_errors(), transactional(sf, commit=False) as session:
        items = list_conflicts(session, _project_id(session, project), status=status)
    if as_json:
        _echo_json(items)
        return
    if not items:
        console.print(f"конфликтов ({status}) нет", markup=False)
        return
    for c in items:
        console.print(
            printable(f"{c['uid']}  {c['status']}  {c['kind']}  {_conflict_target(c)}"),
            markup=False,
        )
    if status != "resolved":
        console.print(
            f"разрешить: cod-doc sync resolve UID -p {printable(project)} --take ours|theirs",
            markup=False,
        )


@sync.command("resolve")
@click.argument("conflict_uid")
@_project_opt
@click.option("--take", type=click.Choice(("ours", "theirs")), help="Чья версия остаётся")
@click.option(
    "--text-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Свой текст из файла (только конфликт text)",
)
@click.option("--reason", default=None, help="Причина в ревизии")
@_author_opt
@_json_opt
@click.pass_context
def sync_resolve(
    ctx: click.Context,
    conflict_uid: str,
    project: str,
    take: str | None,
    text_file: Path | None,
    reason: str | None,
    author: str,
    as_json: bool,
) -> None:
    """Разрешить конфликт: значение, операция, ревизия и событие — уедут push'ем."""
    from cod_doc.infra.db import transactional
    from cod_doc.services.sync_service.conflicts import resolve_conflict

    if (take is None) == (text_file is None):
        raise click.UsageError("нужно ровно одно из --take ours|theirs и --text-file F")
    chosen = take if text_file is None else text_file.read_text(encoding="utf-8")
    assert chosen is not None
    sf = _factory(ctx, project)
    with _sync_errors(), transactional(sf) as session:
        out = resolve_conflict(
            session,
            _project_id(session, project),
            conflict_uid,
            take=chosen,
            author=author,
            reason=reason,
        )
    if as_json:
        _echo_json(out)
        return
    label = take or "свой текст"
    console.print(
        printable(
            f"конфликт {conflict_uid} разрешён ({label}): {_conflict_target(out)}; "
            f"ревизия {out['revision_id'] or '—'}; уедет следующим push"
        ),
        markup=False,
    )
