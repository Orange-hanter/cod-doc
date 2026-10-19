"""Кто делает текущий запрос (RFC 29 §3.8, CLO-036).

Bearer-гейт (``api/auth.py``) резолвит токен в :class:`Identity` и привязывает
её к контексту запроса на время его обработки; сервисы читают её отсюда.
Слой services вверх не смотрит, поэтому контейнер живёт здесь, а не в ``api``.

Нет привязки — запрос локальный (CLI, stdio-MCP, loopback без хранилища
доступа): ни одна проверка не срабатывает, поведение — как до гейта.

Две проверки, обе на точках, через которые проходит каждый вызов:

- :func:`enter_project` — вход в проект (MCP ``session_factory``, маршруты
  ``/api/v1/projects/{slug}``): не участник — ``AccessDeniedError`` (SYNC-202).
- :func:`check_write` — запись (``activity_service.emit``, через него проходит
  каждый write-путь, ADO-040): роль ``reader`` или чужой ``author`` —
  ``AccessDeniedError``; исключение рушит транзакцию мутации целиком.

Модуль — stdlib: его импортирует ``activity_service``, а тот — почти всё.
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    "ROLES",
    "ROLE_ADMIN",
    "ROLE_READER",
    "ROLE_WRITER",
    "AccessDeniedError",
    "Identity",
    "author_handle",
    "bind",
    "check_write",
    "current",
    "enter_project",
    "role_at_least",
]

ROLE_READER: Final = "reader"
ROLE_WRITER: Final = "writer"
ROLE_ADMIN: Final = "admin"
#: По возрастанию прав: индекс — сила роли.
ROLES: Final = (ROLE_READER, ROLE_WRITER, ROLE_ADMIN)


class AccessDeniedError(PermissionError):
    """SYNC-202: роль не позволяет; ``code`` — как у ``RemoteAuthError``."""

    code = "SYNC-202"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


def role_at_least(role: str, minimum: str) -> bool:
    """``role`` не слабее ``minimum``; незнакомая роль — слабее любой."""
    if role not in ROLES:
        return False
    return ROLES.index(role) >= ROLES.index(minimum)


@dataclass(frozen=True, slots=True)
class Identity:
    """Владелец токена: роли по ``project.uid`` и законные строки ``author``."""

    actor_uid: str
    handle: str
    kind: str
    token_label: str
    #: ``handle`` самого actor'а и, для человека, его агентов.
    authors: frozenset[str]
    roles: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(slots=True)
class _Binding:
    identity: Identity
    #: Роль в проекте, в который вошёл запрос (``enter_project``); ``None`` — не входил.
    role: str | None = None
    project_uid: str | None = None


_BINDING: contextvars.ContextVar[_Binding | None] = contextvars.ContextVar(
    "cod_doc_request_identity", default=None
)


@contextlib.contextmanager
def bind(identity: Identity) -> Iterator[Identity]:
    """Привязать ``identity`` к текущему контексту на время блока."""
    token = _BINDING.set(_Binding(identity))
    try:
        yield identity
    finally:
        _BINDING.reset(token)


def current() -> Identity | None:
    """Владелец текущего запроса; ``None`` — запрос локальный."""
    binding = _BINDING.get()
    return binding.identity if binding is not None else None


def enter_project(project_uid: str | None, slug: str) -> str | None:
    """Войти в проект; вернуть роль (``None`` — привязки нет, проверять нечего).

    Не участник, или у проекта нет ``uid`` (схема до 0049) — отказ: проект,
    который нельзя назвать в членстве, удалённому вызывающему не открывается.
    """
    binding = _BINDING.get()
    if binding is None:
        return None
    role = binding.identity.roles.get(project_uid) if project_uid else None
    if role is None:
        msg = f"{binding.identity.handle!r} не участник проекта {slug!r}"
        raise AccessDeniedError(msg)
    binding.role = role
    binding.project_uid = project_uid
    return role


def author_handle(author: str | None) -> str:
    """``handle`` из строки-автора: ``human:dakh`` и ``dakh`` → ``dakh``.

    Канонический формат автора — ``<kind>:<id>`` (``domain.entities``); у
    handle агента свой ``/`` (``dakh/claude-code``), двоеточия в нём нет.
    """
    if not author:
        return ""
    _kind, sep, rest = author.partition(":")
    return rest if sep else author


def check_write(author: str | None) -> None:
    """Запись под привязанным владельцем: роль не ниже writer, ``author`` — его.

    Запись без входа в проект (тул, мимо ``session_factory``) тоже отказ:
    роль неизвестна, а пускать по умолчанию — значит пускать reader'а.
    """
    binding = _BINDING.get()
    if binding is None:
        return
    identity = binding.identity
    if binding.role is None or not role_at_least(binding.role, ROLE_WRITER):
        msg = f"{identity.handle!r}: роль {binding.role or 'нет'!r} не позволяет запись"
        raise AccessDeniedError(msg)
    if author_handle(author) not in identity.authors:
        allowed = ", ".join(sorted(identity.authors))
        msg = f"author {author!r} не принадлежит токену {identity.handle!r}; допустимо: {allowed}"
        raise AccessDeniedError(msg)
