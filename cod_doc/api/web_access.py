"""Авторизация веба под OIDC-сессией: кто куда входит и что пишет (F1 аудита 0056).

SessionGate аутентифицирует, этот модуль решает доступ — по пути и методу, до
маршрута. Решение чистое: роли берутся из :class:`Identity`, ``project.uid`` —
через переданные функции, поэтому правило проверяется без ASGI.

- **Страница проекта** (``/p/<slug>…``, ``/ws/projects/<slug>…``, legacy
  ``/projects/<slug>…``) — только участнику проекта. Чтение (``GET``/``HEAD``/
  ``OPTIONS``, websocket ленты проекта) — любой роли. Запись — роли не ниже
  ``writer``; ``responder`` пишет только ответ на вопрос (``resolve`` и
  добавление варианта), как в MCP (``access_policy``). ``reader`` в вебе не
  пишет: адресованный ему ответ проверяется по ``owner`` вопроса в сервисе,
  а веб-запись идёт мимо этой проверки.
- **Страница узла** (``/``, ``/settings``, ``/palette``, ``/standards``,
  legacy JSON вне ``/api``) — только оператору: ``admin`` во всех проектах
  реестра; пустой реестр или проект с непрочитанным uid — оператора нет. Настройки узла правят общий конфиг, индекс перечисляет все проекты.
  Не оператору ``/`` показывает только его проекты (``Allow.visible``),
  остальное — 403.

Неизвестный проект, проект без ``uid`` и сбой чтения реестра — отказ: состояние
доступа, которое нельзя проверить, веб не открывает.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qsl

from cod_doc.services.request_identity import (
    ROLE_ADMIN,
    ROLE_RESPONDER,
    ROLE_WRITER,
    role_at_least,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from cod_doc.services.request_identity import Identity

__all__ = ["Allow", "Deny", "Verdict", "decide", "project_of"]

_PROJECT_PATHS: Final = (
    re.compile(r"^/p/(?P<slug>[^/]+)(?:/|$)"),
    re.compile(r"^/ws/projects/(?P<slug>[^/]+)(?:/|$)"),
    re.compile(r"^/projects/(?P<slug>[^/]+)(?:/|$)"),
)
#: Лента проекта по websocket — чтение; прочие websocket проекта (``…/run``) — запись.
_WS_FEED: Final = re.compile(r"^/ws/projects/[^/]+/?$")
#: Запись, доступная ``responder``: ответ на вопрос и добавление варианта ответа.
_ANSWER_WRITE: Final = re.compile(r"^/p/[^/]+/questions/[^/]+/(?:resolve|options)/?$")
_PALETTE: Final = "/palette"
_READ_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True, slots=True)
class Allow:
    """Пропустить; ``project`` — слаг, если путь проектный.

    ``visible`` — слаги, которые странице узла можно показать (индекс не
    оператору); ``None`` — без ограничения.
    """

    project: str | None = None
    visible: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class Deny:
    reason: str
    project: str | None = None


Verdict = Allow | Deny


def project_of(path: str, query: str = "") -> str | None:
    """Слаг проекта, которому принадлежит запрос; ``None`` — страница узла.

    Палитра (``/palette?project=<slug>``) ищет по одному проекту — она
    проектная по параметру; без него — страница узла.
    """
    if path == _PALETTE:
        values = _palette_projects(query)
        return values[0].strip() or None if values else None
    for pattern in _PROJECT_PATHS:
        match = pattern.match(path)
        if match is not None:
            return match.group("slug")
    return None


def _palette_projects(query: str) -> list[str]:
    """Все значения ``project`` палитры, пустые тоже: сверка с тем, что прочтёт маршрут."""
    return [v for k, v in parse_qsl(query, keep_blank_values=True) if k == "project"]


def _is_write(method: str, path: str, *, websocket: bool) -> bool:
    if websocket:
        return _WS_FEED.match(path) is None
    return method.upper() not in _READ_METHODS


def decide(
    identity: Identity,
    *,
    method: str,
    path: str,
    websocket: bool,
    uid_of: Callable[[str], str | None],
    registry: Callable[[], Mapping[str, str | None]],
    query: str = "",
) -> Verdict:
    """Решение по запросу владельца сессии; ``uid_of``/``registry`` — из ``access_service``."""
    if path == _PALETTE and len(_palette_projects(query)) > 1:
        # Гейт и маршрут разбирают повтор параметра по-разному (первое против
        # последнего значения): проверили бы один проект, а искали в другом.
        return Deny("неоднозначный параметр project")
    slug = project_of(path, query)
    if slug is not None:
        return _decide_project(
            identity, slug, method=method, path=path, websocket=websocket, uid_of=uid_of
        )
    projects = registry()
    # Оператор — admin в каждом проекте реестра. Пустой реестр и проект с
    # непрочитанным uid — не оператор: сбой чтения не должен открывать
    # настройки узла всем привязанным (fail-closed).
    if projects and all(
        uid is not None and identity.roles.get(uid) == ROLE_ADMIN for uid in projects.values()
    ):
        return Allow()
    if path == "/":
        mine = frozenset(name for name, uid in projects.items() if uid and uid in identity.roles)
        if mine:
            return Allow(visible=mine)
        return Deny(f"{identity.handle!r} не участник ни одного проекта")
    return Deny(f"{identity.handle!r}: страница узла доступна только администратору")


def _decide_project(
    identity: Identity,
    slug: str,
    *,
    method: str,
    path: str,
    websocket: bool,
    uid_of: Callable[[str], str | None],
) -> Verdict:
    uid = uid_of(slug)
    role = identity.roles.get(uid) if uid else None
    if role is None:
        return Deny(f"{identity.handle!r} не участник проекта {slug!r}", project=slug)
    if not _is_write(method, path, websocket=websocket) or role_at_least(role, ROLE_WRITER):
        return Allow(project=slug)
    if role == ROLE_RESPONDER and not websocket and _ANSWER_WRITE.match(path):
        return Allow(project=slug)
    return Deny(f"{identity.handle!r}: роль {role!r} не позволяет запись", project=slug)
