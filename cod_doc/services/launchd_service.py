"""ADO-192: управление тремя launchd-сервисами cod-doc (порт из bash-скрипта).

Три процесса под launchd смотрят на одну пиннованную сборку в
``~/.cod-doc/runtime``: MCP на 8801 (профиль ``standard``), MCP на 8802
(профиль ``agent``) и веб ``cod-doc serve``. Раньше их рендерил и грузил
``deploy/launchd/cod-doc-services.sh``; :data:`SERVICES` — тот же bash-массив,
ставший единственным источником истины.

**Модуль обязан импортировать только stdlib** — пробы живости идут через
``urllib.request``, а не ``httpx``. Причина та же, что в
:mod:`cod_doc.services.runtime_service`: функции отсюда исполняются уже после
подмены рантайма, а ``httpx`` лежит в его ``site-packages``. Стережёт
``tests/services/test_swap_boundary_imports.py``.

Не-macOS: :func:`is_supported` отдаёт ``False``, и каждая функция возвращает
``ServiceStatus(skipped_reason=...)`` вместо исключения. Фаза сборки рантайма
на Linux осмысленна и без launchd — перезапускать просто нечего.

Единственная точка запуска внешнего бинаря — :func:`_run_launchctl`, как
``_run_gh`` в :mod:`cod_doc.services.gh_service`: тесты подменяют одну функцию,
а не восемь вызовов ``subprocess``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "LEGACY_WEB_LABEL",
    "SERVICES",
    "ServiceSpec",
    "ServiceStatus",
    "bootout_and_wait",
    "install",
    "is_supported",
    "render_plist",
    "restart",
    "status",
    "uninstall",
]


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    """Описание одного launchd-сервиса: лейбл, род, порт, профиль MCP."""

    label: str
    kind: str
    port: int | None
    profile: str | None


@dataclass(slots=True)
class ServiceStatus:
    """Состояние одного сервиса после операции или опроса."""

    label: str
    port: int | None
    profile: str | None
    loaded: bool
    answering: bool
    kicked: bool = False
    skipped_reason: str | None = None
    error: str | None = None


#: ``kind`` сервиса: MCP-демон (порт задаёт профиль) и REST API + web UI.
KIND_MCP = "mcp"
KIND_WEB = "web"

#: Единственный источник истины по сервисам — бывший bash-массив SERVICES.
#: Порт веба намеренно не хардкодится: он приходит из ``api_port`` конфига,
#: иначе plist и конфиг разъезжаются молча.
SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(label="com.cod-doc.mcp", kind=KIND_MCP, port=8801, profile="standard"),
    ServiceSpec(label="com.cod-doc.mcp-agent", kind=KIND_MCP, port=8802, profile="agent"),
    ServiceSpec(label="com.cod-doc.web", kind=KIND_WEB, port=None, profile=None),
)

#: Прежний веб-лейбл из editable-venv. Снимается при install, plist
#: переименовывается в ``.plist.replaced``.
LEGACY_WEB_LABEL = "com.dakh.cod-doc"

#: Сколько ждать исчезновения лейбла после bootout. `bootout` асинхронен, и
#: немедленный `bootstrap` падает с `Bootstrap failed: 5: Input/output error` —
#: поймано на живой машине, под `set -e` install обрывался на первом сервисе.
BOOTOUT_BUDGET_S = 10.0

#: Шаг опроса `launchctl print` внутри бюджета bootout (bash: `sleep 0.2` × 50).
POLL_INTERVAL_S = 0.2

#: Таймаут пробы живости. Дольше ждать нечего: сервис либо отвечает, либо нет.
PROBE_TIMEOUT_S = 5.0

#: Потолок на сам `launchctl`: команда локальная, висеть ей не с чего.
LAUNCHCTL_TIMEOUT_S = 30.0

#: Интерфейс, на котором висят сервисы и по которому их же и пробуют.
LOOPBACK = "127.0.0.1"

#: `PATH` демона: launchd стартует процесс с пустым окружением, и без этого
#: `git`/`uv` из Homebrew не находятся.
LAUNCHD_PATH = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

#: `ThrottleInterval` в plist: launchd не поднимает упавший сервис чаще.
THROTTLE_INTERVAL_S = 10

#: Почему все операции — no-op вне macOS.
UNSUPPORTED_REASON = "launchd есть только на macOS — перезапускать нечего"

#: `initialize` — самый дешёвый запрос, доказывающий живость MCP.
_MCP_INITIALIZE_BODY = (
    b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
    b'{"protocolVersion":"2025-06-18","capabilities":{},'
    b'"clientInfo":{"name":"healthcheck","version":"1"}}}'
)
_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

#: Что считаем ответом сервиса. `curl -fsS` роняет код на 4xx/5xx — здесь то же.
_HTTP_OK_MIN = 200
_HTTP_OK_LIMIT = 300

#: Код возврата, когда `launchctl` вовсе не запустился (нет бинаря, таймаут).
_LAUNCHCTL_UNAVAILABLE_RC = 127


def is_supported() -> bool:
    """macOS с доступным ``launchctl``. На всём остальном — ``False``."""
    return sys.platform == "darwin" and shutil.which("launchctl") is not None


# ── запуск launchctl ────────────────────────────────────────────────────────
def _run_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Единственная точка запуска ``launchctl``; её и подменяют тесты.

    Ненулевой код — это данные, а не исключение: `bootout` несуществующего
    лейбла и `kickstart` невыгруженного сервиса в bash-скрипте были штатными
    ветками (``|| true``), и переводить их в исключения значило бы городить
    обратно try/except вокруг каждого вызова.
    """
    cmd = ["launchctl", *args]
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=LAUNCHCTL_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, _LAUNCHCTL_UNAVAILABLE_RC, "", str(exc))


def _domain() -> str:
    """Домен пользовательских агентов: ``gui/<uid>``."""
    return f"gui/{os.getuid()}"


def _target(label: str) -> str:
    """Полный service target ``gui/<uid>/<label>``."""
    return f"{_domain()}/{label}"


def _is_loaded(label: str) -> bool:
    """Знает ли launchd такой лейбл (``launchctl print`` вернул 0)."""
    return _run_launchctl(["print", _target(label)]).returncode == 0


# ── пробы живости ───────────────────────────────────────────────────────────
def _http_ok(request: urllib.request.Request) -> bool:
    """Выполнить запрос и сказать, ответил ли сервис 2xx. Никогда не бросает."""
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_S) as response:
            return _HTTP_OK_MIN <= int(response.status) < _HTTP_OK_LIMIT
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _probe_mcp(port: int) -> bool:
    """POST ``/mcp`` с ``initialize`` — порт MCP отвечает."""
    request = urllib.request.Request(
        f"http://{LOOPBACK}:{port}/mcp",
        data=_MCP_INITIALIZE_BODY,
        headers=dict(_MCP_HEADERS),
        method="POST",
    )
    return _http_ok(request)


def _probe_web(port: int) -> bool:
    """GET ``/api/health`` — веб отвечает."""
    request = urllib.request.Request(
        f"http://{LOOPBACK}:{port}/api/health",
        method="GET",
    )
    return _http_ok(request)


def _probe(spec: ServiceSpec, port: int) -> bool:
    """Проба по роду сервиса."""
    return _probe_mcp(port) if spec.kind == KIND_MCP else _probe_web(port)


# ── рендер plist ────────────────────────────────────────────────────────────
def _program_arguments(spec: ServiceSpec, *, runtime: Path) -> list[str]:
    """``ProgramArguments`` сервиса.

    Веб запускается **без** ``--port``: порт живёт в ``~/.cod-doc/config.yaml``,
    и продублировать его флагом значит развести plist с конфигом молча.
    """
    if spec.kind == KIND_MCP:
        return [
            str(runtime / "bin" / "cod-doc-mcp"),
            "--transport",
            "streamable-http",
            "--host",
            LOOPBACK,
            "--port",
            str(spec.port),
            "--profile",
            str(spec.profile),
        ]
    return [str(runtime / "bin" / "cod-doc"), "serve"]


def render_plist(spec: ServiceSpec, *, runtime: Path, workdir: Path, logs: Path) -> str:
    """Собрать plist сервиса. Должен совпадать с выводом bash-``render()``.

    ``WorkingDirectory`` — нейтральный ``~/.cod-doc``: discovery проектов вверх
    по дереву у демона бессмысленен, рабочий каталог заморожен на момент
    старта. ``RunAtLoad``/``KeepAlive``/``ThrottleInterval=10`` — как было.
    """
    args = "\n".join(
        f"    <string>{escape(arg)}</string>" for arg in _program_arguments(spec, runtime=runtime)
    )
    log = escape(str(logs / f"{spec.label}.log"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{escape(spec.label)}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(str(workdir))}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{LAUNCHD_PATH}</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>{THROTTLE_INTERVAL_S}</integer>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


# ── выгрузка ────────────────────────────────────────────────────────────────
def bootout_and_wait(label: str, *, budget_s: float = BOOTOUT_BUDGET_S) -> bool:
    """Выгрузить лейбл и дождаться, пока ``launchctl print`` перестанет его знать.

    Возвращает ``False`` по исчерпании бюджета, но **не бросает**: невыгрузка
    одного сервиса не повод ронять весь прогон.
    """
    if not is_supported():
        return False
    _run_launchctl(["bootout", _target(label)])
    attempts = max(1, int(budget_s / POLL_INTERVAL_S))
    for _ in range(attempts):
        if not _is_loaded(label):
            return True
        time.sleep(POLL_INTERVAL_S)
    return False


def _drop_legacy_web(*, agents: Path) -> None:
    """Снять прежний веб-лейбл ``com.dakh.cod-doc`` и отложить его plist.

    Веб жил под личным лейблом и из editable-venv репозитория. Если его не
    выгрузить, два процесса дерутся за один порт, и кто победил — лотерея.
    """
    if _is_loaded(LEGACY_WEB_LABEL):
        bootout_and_wait(LEGACY_WEB_LABEL)
    legacy_plist = agents / f"{LEGACY_WEB_LABEL}.plist"
    if legacy_plist.is_file():
        legacy_plist.rename(legacy_plist.with_name(f"{legacy_plist.name}.replaced"))


# ── операции ────────────────────────────────────────────────────────────────
def _port_of(spec: ServiceSpec, api_port: int | None) -> int | None:
    """Порт сервиса: у MCP свой, у веба — переданный ``api_port``."""
    return spec.port if spec.port is not None else api_port


def _all_dead(
    api_port: int | None,
    *,
    skipped_reason: str | None = None,
    error: str | None = None,
) -> list[ServiceStatus]:
    """Три записи «ничего не сделано» с общей причиной."""
    return [
        ServiceStatus(
            label=spec.label,
            port=_port_of(spec, api_port),
            profile=spec.profile,
            loaded=False,
            answering=False,
            skipped_reason=skipped_reason,
            error=error,
        )
        for spec in SERVICES
    ]


def _skipped(api_port: int | None) -> list[ServiceStatus]:
    """Ответ всех операций там, где launchd нет."""
    return _all_dead(api_port, skipped_reason=UNSUPPORTED_REASON)


def install(
    *,
    runtime: Path,
    api_port: int,
    workdir: Path,
    logs: Path,
    agents: Path,
) -> list[ServiceStatus]:
    """Отрендерить plist'ы и загрузить все три сервиса (bootout → bootstrap)."""
    if not is_supported():
        return _skipped(api_port)

    mcp_bin = runtime / "bin" / "cod-doc-mcp"
    if not mcp_bin.is_file():
        # Тот же предохранитель, что `[ -x ... ] || die` в bash: загрузить plist,
        # указывающий в пустоту, — это три демона в цикле перезапуска.
        return _all_dead(api_port, error=f"рантайм не собран: нет {mcp_bin} — сначала фаза сборки")

    for directory in (agents, logs, workdir):
        directory.mkdir(parents=True, exist_ok=True)
    _drop_legacy_web(agents=agents)

    statuses: list[ServiceStatus] = []
    halted: str | None = None
    for spec in SERVICES:
        if halted is not None:
            # Как в bash: после первой неудачи остальные сервисы не трогаем —
            # полурабочий набор демонов хуже, чем явно незавершённый install.
            statuses.append(_halted_status(spec, api_port, halted))
            continue
        status_entry, halted = _install_one(spec, api_port, runtime, workdir, logs, agents)
        statuses.append(status_entry)
    return statuses


def _halted_status(spec: ServiceSpec, api_port: int, after: str) -> ServiceStatus:
    """Сервис, до которого install не дошёл из-за сбоя на предыдущем."""
    return ServiceStatus(
        label=spec.label,
        port=_port_of(spec, api_port),
        profile=spec.profile,
        loaded=False,
        answering=False,
        skipped_reason=f"не тронут: {after} не загрузился",
    )


def _install_one(
    spec: ServiceSpec,
    api_port: int,
    runtime: Path,
    workdir: Path,
    logs: Path,
    agents: Path,
) -> tuple[ServiceStatus, str | None]:
    """Записать plist и загрузить один сервис. Второй элемент — лейбл сбоя."""
    port = _port_of(spec, api_port)
    plist = agents / f"{spec.label}.plist"
    plist.write_text(
        render_plist(spec, runtime=runtime, workdir=workdir, logs=logs), encoding="utf-8"
    )
    bootout_and_wait(spec.label)
    proc = _run_launchctl(["bootstrap", _domain(), str(plist)])
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or f"bootstrap вернул {proc.returncode}"
        entry = ServiceStatus(
            label=spec.label,
            port=port,
            profile=spec.profile,
            loaded=False,
            answering=False,
            error=f"{detail}; см. {logs / f'{spec.label}.log'}",
        )
        return entry, spec.label
    entry = ServiceStatus(
        label=spec.label,
        port=port,
        profile=spec.profile,
        loaded=True,
        answering=port is not None and _probe(spec, port),
    )
    return entry, None


def uninstall(*, agents: Path) -> list[ServiceStatus]:
    """Выгрузить сервисы и убрать plist'ы."""
    if not is_supported():
        return _skipped(None)
    statuses: list[ServiceStatus] = []
    for spec in SERVICES:
        _run_launchctl(["bootout", _target(spec.label)])
        (agents / f"{spec.label}.plist").unlink(missing_ok=True)
        statuses.append(
            ServiceStatus(
                label=spec.label,
                port=spec.port,
                profile=spec.profile,
                loaded=False,
                answering=False,
            )
        )
    return statuses


def restart(*, api_port: int) -> list[ServiceStatus]:
    """``launchctl kickstart -k`` по всем лейблам — без пересборки plist'ов.

    Пробы здесь нет: после `kickstart` процесс ещё поднимается, и ``answering``
    в этот момент означал бы «не успел», а не «сломан». Живость спрашивает
    :func:`status` отдельным вызовом.
    """
    if not is_supported():
        return _skipped(api_port)
    statuses: list[ServiceStatus] = []
    for spec in SERVICES:
        proc = _run_launchctl(["kickstart", "-k", _target(spec.label)])
        kicked = proc.returncode == 0
        statuses.append(
            ServiceStatus(
                label=spec.label,
                port=_port_of(spec, api_port),
                profile=spec.profile,
                loaded=kicked,
                answering=False,
                kicked=kicked,
                error=None if kicked else f"не загружен: {spec.label}",
            )
        )
    return statuses


def status(*, api_port: int) -> list[ServiceStatus]:
    """Опросить сервисы: загружен ли лейбл и отвечает ли порт.

    MCP пробуется методом ``initialize`` по POST ``/mcp`` — самый дешёвый
    запрос, доказывающий живость. Веб — GET ``/api/health``.
    """
    if not is_supported():
        return _skipped(api_port)
    statuses: list[ServiceStatus] = []
    for spec in SERVICES:
        port = _port_of(spec, api_port)
        loaded = _is_loaded(spec.label)
        statuses.append(
            ServiceStatus(
                label=spec.label,
                port=port,
                profile=spec.profile,
                loaded=loaded,
                answering=loaded and port is not None and _probe(spec, port),
            )
        )
    return statuses
