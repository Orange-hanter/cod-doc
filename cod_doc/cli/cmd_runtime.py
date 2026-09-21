"""ADO-192: ``cod-doc runtime`` — пиннованная сборка сервисов и три демона launchd.

Замена подкомандам ``deploy/launchd/cod-doc-services.sh``. Скрипт не входит в
колесо (``[tool.setuptools.packages.find] include = ["cod_doc*"]``), поэтому из
установленного рантайма управление сервисами было недоступно вовсе — оно
существовало только внутри чекаута репозитория. Семантика подкоманд сохранена
один в один; вся логика живёт в ``runtime_service`` / ``launchd_service``, здесь
только разбор флагов и печать.

Импорты ``cod_doc.services.*`` — **только в телах команд** (ADO-179). Оба
сервиса stdlib-only, но гейт ``tests/cli/test_cli_startup_is_light.py`` смотрит
на корень пакета, а не на его вес, и запрещает такие импорты на уровне модуля
без исключения в allowlist.

Печать plist'а идёт через ``click.echo``, а не ``console.print``: rich
подсвечивает разметку и переносит длинные строки по ширине терминала, а plist
человек копирует как есть.
"""

from __future__ import annotations

import json as _json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from cod_doc.config import config_dir

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cod_doc.config import Config
    from cod_doc.services.launchd_service import ServiceStatus
    from cod_doc.services.runtime_service import InstallVersion

console = Console()

#: Переменные окружения, которые читает ``runtime_service``. Продублированы
#: здесь потому, что справочным командам нужен НЕклонирующий резолв чекаута
#: (:func:`_existing_repo`), а ``runtime_service`` экспортирует только
#: ``resolve_repo``, который на пустом месте делает ``git clone``.
REPO_ENV = "COD_DOC_REPO"
PYTHON_ENV = "COD_DOC_PYTHON"

#: Куда установлен рантайм сервисов. Тот же дефолт, что у bash-скрипта.
RUNTIME_ENV = "COD_DOC_RUNTIME"
RUNTIME_DIRNAME = "runtime"

#: Столько символов sha показываем человеку — как ``runtime_service.SHA_DISPLAY_LEN``,
#: который на уровне модуля импортировать нельзя (ADO-179).
SHA_SHOWN = 12

#: Единственное, чего откат рантайма не делает. Человек обязан это прочитать:
#: код вернулся, схема — нет.
DB_NOT_ROLLED_BACK = (
    "Откат вернул прежний код рантайма. БД он НЕ откатывает: накатанные миграции "
    "остаются на месте, и прежний код может не знать новой схемы."
)

#: Ответ на «какая версия», когда установки нет вовсе.
_NO_VERSION = "—"

#: Форма, в которой click печатает ``--version``: ``<prog>, version <X>``.
#: ``runtime_service`` отдаёт сырой вывод бинаря и правильно делает — он
#: докладывает, что сказали. Но в колонке «Версия» слово «version» это
#: тавтология, и стоит она семнадцать символов узкой колонки: ровно то
#: значение, ради которого команду и зовут, уезжает переносом на три строки.
_REPORTED_VERSION = re.compile(r"^\s*[\w.-]+,\s*version\s+(?P<version>\S+)\s*$")


def runtime_dir() -> Path:
    """Каталог рантайма: ``$COD_DOC_RUNTIME`` → ``<config_dir>/runtime``."""
    explicit = os.environ.get(RUNTIME_ENV, "").strip()
    return Path(explicit).expanduser() if explicit else config_dir() / RUNTIME_DIRNAME


def agents_dir() -> Path:
    """Каталог plist'ов пользовательских агентов launchd."""
    return Path.home() / "Library" / "LaunchAgents"


def logs_dir() -> Path:
    """Куда launchd пишет stdout/stderr сервисов."""
    return Path.home() / "Library" / "Logs"


def workdir() -> Path:
    """``WorkingDirectory`` демонов — нейтральный ``~/.cod-doc``."""
    return config_dir()


def _existing_repo(repo: Path | None) -> Path | None:
    """Локальный чекаут, если он уже на диске. Никогда не клонирует.

    ``runtime_service.resolve_repo`` на пустом месте уходит в ``git clone`` —
    для справочных команд (``version``, ``status``) это сеть и минуты ожидания
    в ответ на вопрос «какая у меня версия».
    """
    from cod_doc.services import runtime_service

    candidates: list[Path] = []
    if repo is not None:
        candidates.append(Path(repo).expanduser())
    else:
        env_repo = os.environ.get(REPO_ENV, "").strip()
        if env_repo:
            candidates.append(Path(env_repo).expanduser())
        candidates.append(Path.home() / "Git" / "_my" / "cod-doc")
        candidates.append(config_dir() / runtime_service.SRC_CACHE_DIRNAME)
    return next((path for path in candidates if (path / ".git").exists()), None)


def installed_versions(repo: Path | None) -> list[InstallVersion]:
    """Версии всех установок cod-doc на машине."""
    from cod_doc.services import runtime_service

    return runtime_service.installed_versions(runtime=runtime_dir(), repo=_existing_repo(repo))


# ── печать ──────────────────────────────────────────────────────────────────


def short_version(reported: str) -> str:
    """Срезать префикс ``cod-doc, version `` — но только если он там и есть.

    Строка приходит из ``runtime_service`` как сырой вывод бинаря, и бинарь
    бывает любой: старая сборка без ``--version``, чужой console-script,
    ``версия не определяется``. Всё, что не подошло под формат, показываем как
    есть: соврать «1.4.1» там, где сказано другое, хуже тавтологии.
    """
    match = _REPORTED_VERSION.match(reported)
    return match.group("version") if match else reported


def _service_state(status: ServiceStatus) -> str:
    """Одна колонка «Состояние» — в терминах bash-``cmd_status``."""
    if status.skipped_reason:
        return status.skipped_reason
    if status.error:
        return f"ошибка: {status.error}"
    if status.kicked:
        return "перезапущен"
    if not status.loaded:
        return "не загружен"
    return "OK" if status.answering else "загружен, но не отвечает"


def render_services(statuses: Sequence[ServiceStatus], out: Console) -> None:
    """Таблица сервисов: лейбл, порт, профиль, состояние."""
    if not statuses:
        out.print("[dim]Сервисов нет.[/dim]")
        return
    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("Лейбл", style="cyan", no_wrap=True)
    table.add_column("Порт")
    table.add_column("Профиль")
    table.add_column("Состояние")
    for status in statuses:
        table.add_row(
            status.label,
            str(status.port) if status.port is not None else _NO_VERSION,
            status.profile or _NO_VERSION,
            _service_state(status),
        )
    out.print(table)


def render_versions(versions: Sequence[InstallVersion], out: Console) -> None:
    """Таблица установок cod-doc: у вопроса «какая версия» нет одного ответа."""
    if not versions:
        return
    table = Table(title="установки cod-doc на машине", show_header=True, box=None, padding=(0, 1))
    table.add_column("Установка", style="cyan", no_wrap=True)
    table.add_column("Версия", overflow="fold")
    # `fold`, а не дефолтное многоточие: обрезанный путь к бинарю бесполезен
    # ровно тогда, когда его и спрашивают — «а какая установка это была».
    table.add_column("Бинарь", style="dim", overflow="fold")
    for entry in versions:
        shown = short_version(entry.version) if entry.version else (entry.error or _NO_VERSION)
        table.add_row(entry.name, shown, entry.binary)
    out.print(table)


def _services_payload(statuses: Sequence[ServiceStatus]) -> list[dict[str, object]]:
    return [asdict(status) for status in statuses]


def _versions_payload(versions: Sequence[InstallVersion]) -> list[dict[str, object]]:
    return [asdict(entry) for entry in versions]


def _echo_json(payload: dict[str, object]) -> None:
    """Машинный вывод — только через ``click.echo`` (ADO-176)."""
    click.echo(_json.dumps(payload, ensure_ascii=False, indent=2))


def _fail_if_broken(statuses: Sequence[ServiceStatus]) -> None:
    """Ненулевой код, если хоть один сервис не загрузился."""
    if any(status.error for status in statuses):
        sys.exit(1)


# ── команды ─────────────────────────────────────────────────────────────────


@click.group()
def runtime() -> None:
    """Рантайм сервисов и демоны launchd (замена cod-doc-services.sh)."""


_REPO_OPTION = click.option(
    "--repo",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Чекаут репозитория (по умолчанию $COD_DOC_REPO → ~/Git/_my/cod-doc)",
)
_JSON_OPTION = click.option("--json", "as_json", is_flag=True, default=False, help="Вывод в JSON")


@runtime.command("status")
@_REPO_OPTION
@_JSON_OPTION
@click.pass_context
def runtime_status(ctx: click.Context, repo: Path | None, as_json: bool) -> None:
    """Что загружено, отвечает ли порт и какая версия у каждой установки."""
    from cod_doc.services import launchd_service

    cfg: Config = ctx.obj["config"]
    statuses = launchd_service.status(api_port=cfg.api_port)
    versions = installed_versions(repo)
    if as_json:
        _echo_json(
            {
                "services": _services_payload(statuses),
                "versions": _versions_payload(versions),
            }
        )
        return
    render_services(statuses, console)
    render_versions(versions, console)


@runtime.command("version")
@_REPO_OPTION
@_JSON_OPTION
def runtime_version(repo: Path | None, as_json: bool) -> None:
    """Версии трёх установок: рантайм сервисов, PATH (uv tool), чекаут."""
    versions = installed_versions(repo)
    if as_json:
        _echo_json({"versions": _versions_payload(versions)})
        return
    render_versions(versions, console)


@runtime.command("restart")
@_JSON_OPTION
@click.pass_context
def runtime_restart(ctx: click.Context, as_json: bool) -> None:
    """Перезапустить демоны (kickstart -k) без пересборки plist'ов."""
    from cod_doc.services import launchd_service

    cfg: Config = ctx.obj["config"]
    statuses = launchd_service.restart(api_port=cfg.api_port)
    if as_json:
        _echo_json({"services": _services_payload(statuses)})
        return
    render_services(statuses, console)


@runtime.command("rollback")
@_JSON_OPTION
@click.pass_context
def runtime_rollback(ctx: click.Context, as_json: bool) -> None:
    """Вернуть предыдущий рантайм. БД это НЕ откатывает."""
    from cod_doc.services import launchd_service, runtime_service

    cfg: Config = ctx.obj["config"]
    swapped = runtime_service.rollback(runtime_dir())
    statuses: list[ServiceStatus] = []
    if swapped.ok:
        click.echo("→ kickstart сервисов", err=True)
        statuses = launchd_service.restart(api_port=cfg.api_port)

    if as_json:
        _echo_json(
            {
                "swap": asdict(swapped),
                "services": _services_payload(statuses),
                "warning": DB_NOT_ROLLED_BACK if swapped.ok else None,
            }
        )
    elif not swapped.ok:
        console.print(f"[red]{swapped.error}[/red]")
    else:
        console.print(f"[green]Рантайм откачен: {swapped.runtime}[/green]")
        console.print("[dim]Повторный rollback вернёт обратно.[/dim]")
        console.print(f"[yellow]{DB_NOT_ROLLED_BACK}[/yellow]")
        render_services(statuses, console)
    if not swapped.ok:
        sys.exit(1)


@runtime.command("build")
@click.option("--ref", default=None, help="Ревизия или ветка; по умолчанию — ветка remote'а")
@_REPO_OPTION
@click.option("--python", "python_version", default=None, help=f"Версия Python (${PYTHON_ENV})")
@_JSON_OPTION
def runtime_build(
    ref: str | None,
    repo: Path | None,
    python_version: str | None,
    as_json: bool,
) -> None:
    """Собрать ревизию в <runtime>.staged и проверить, ничего не подменяя."""
    from cod_doc.services import runtime_service

    try:
        resolved = runtime_service.resolve_repo(
            repo=repo, src_cache=config_dir() / runtime_service.SRC_CACHE_DIRNAME
        )
        chosen = ref or runtime_service.default_branch(resolved)
        click.echo(f"→ git fetch origin ({resolved})", err=True)
        sha = runtime_service.resolve_ref(resolved, chosen)
        click.echo(f"→ сборка {chosen} ({sha[:SHA_SHOWN]})", err=True)
        report = runtime_service.build_staged(
            resolved,
            sha,
            runtime=runtime_dir(),
            python_version=python_version or runtime_service.DEFAULT_PYTHON_VERSION,
        )
    except runtime_service.RuntimeError_ as exc:
        raise click.ClickException(str(exc)) from exc
    report.ref = chosen
    runtime_service.drop_build_src(Path(report.src))

    if as_json:
        _echo_json({"build": asdict(report)})
    elif report.ok:
        console.print(f"[green]Собрано {report.version} ({report.sha[:SHA_SHOWN]})[/green]")
        console.print(f"[dim]{report.staged} — сервисы не тронуты, свап не делался.[/dim]")
        console.print(f"Поставить это: cod-doc update --ref {chosen}")
    else:
        console.print(f"[red]{report.error}[/red]")
    if not report.ok:
        sys.exit(1)


@runtime.command("install")
@_JSON_OPTION
@click.pass_context
def runtime_install(ctx: click.Context, as_json: bool) -> None:
    """Отрендерить plist'ы и загрузить все три сервиса (bootout → bootstrap)."""
    from cod_doc.services import launchd_service

    cfg: Config = ctx.obj["config"]
    statuses = launchd_service.install(
        runtime=runtime_dir(),
        api_port=cfg.api_port,
        workdir=workdir(),
        logs=logs_dir(),
        agents=agents_dir(),
    )
    if as_json:
        _echo_json({"services": _services_payload(statuses)})
    else:
        render_services(statuses, console)
    _fail_if_broken(statuses)


@runtime.command("uninstall")
@_JSON_OPTION
def runtime_uninstall(as_json: bool) -> None:
    """Выгрузить сервисы и удалить их plist'ы."""
    from cod_doc.services import launchd_service

    statuses = launchd_service.uninstall(agents=agents_dir())
    if as_json:
        _echo_json({"services": _services_payload(statuses)})
        return
    render_services(statuses, console)


@runtime.command("render")
@_JSON_OPTION
def runtime_render(as_json: bool) -> None:
    """Показать plist'ы, ничего не записывая на диск."""
    from cod_doc.services import launchd_service

    rendered = [
        {
            "label": spec.label,
            "path": str(agents_dir() / f"{spec.label}.plist"),
            "plist": launchd_service.render_plist(
                spec, runtime=runtime_dir(), workdir=workdir(), logs=logs_dir()
            ),
        }
        for spec in launchd_service.SERVICES
    ]
    if as_json:
        _echo_json({"plists": rendered})
        return
    for item in rendered:
        click.echo(f"=== {item['path']}")
        click.echo(item["plist"])
