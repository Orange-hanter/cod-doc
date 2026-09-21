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
"""

from __future__ import annotations

from dataclasses import dataclass
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


#: Единственный источник истины по сервисам — бывший bash-массив SERVICES.
#: Порт веба намеренно не хардкодится: он приходит из ``api_port`` конфига,
#: иначе plist и конфиг разъезжаются молча.
SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(label="com.cod-doc.mcp", kind="mcp", port=8801, profile="standard"),
    ServiceSpec(label="com.cod-doc.mcp-agent", kind="mcp", port=8802, profile="agent"),
    ServiceSpec(label="com.cod-doc.web", kind="web", port=None, profile=None),
)

#: Прежний веб-лейбл из editable-venv. Снимается при install, plist
#: переименовывается в ``.plist.replaced``.
LEGACY_WEB_LABEL = "com.dakh.cod-doc"

#: Сколько ждать исчезновения лейбла после bootout. `bootout` асинхронен, и
#: немедленный `bootstrap` падает с `Bootstrap failed: 5: Input/output error` —
#: поймано на живой машине, под `set -e` install обрывался на первом сервисе.
BOOTOUT_BUDGET_S = 10.0

#: Таймаут пробы живости. Дольше ждать нечего: сервис либо отвечает, либо нет.
PROBE_TIMEOUT_S = 5.0


def is_supported() -> bool:
    """macOS с доступным ``launchctl``. На всём остальном — ``False``."""
    raise NotImplementedError


def render_plist(spec: ServiceSpec, *, runtime: Path, workdir: Path, logs: Path) -> str:
    """Собрать plist сервиса. Должен совпадать с выводом bash-``render()``.

    ``WorkingDirectory`` — нейтральный ``~/.cod-doc``: discovery проектов вверх
    по дереву у демона бессмысленен, рабочий каталог заморожен на момент
    старта. ``RunAtLoad``/``KeepAlive``/``ThrottleInterval=10`` — как было.
    """
    raise NotImplementedError


def bootout_and_wait(label: str, *, budget_s: float = BOOTOUT_BUDGET_S) -> bool:
    """Выгрузить лейбл и дождаться, пока ``launchctl print`` перестанет его знать.

    Возвращает ``False`` по исчерпании бюджета, но **не бросает**: невыгрузка
    одного сервиса не повод ронять весь прогон.
    """
    raise NotImplementedError


def install(
    *,
    runtime: Path,
    api_port: int,
    workdir: Path,
    logs: Path,
    agents: Path,
) -> list[ServiceStatus]:
    """Отрендерить plist'ы и загрузить все три сервиса (bootout → bootstrap)."""
    raise NotImplementedError


def uninstall(*, agents: Path) -> list[ServiceStatus]:
    """Выгрузить сервисы и убрать plist'ы."""
    raise NotImplementedError


def restart(*, api_port: int) -> list[ServiceStatus]:
    """``launchctl kickstart -k`` по всем лейблам — без пересборки plist'ов."""
    raise NotImplementedError


def status(*, api_port: int) -> list[ServiceStatus]:
    """Опросить сервисы: загружен ли лейбл и отвечает ли порт.

    MCP пробуется методом ``initialize`` по POST ``/mcp`` — самый дешёвый
    запрос, доказывающий живость. Веб — GET ``/api/health``.
    """
    raise NotImplementedError
