"""Сервер умного remote: ``/api/sync/v1`` поверх каталога (RFC 29 §3.5, §3.8; CLO-035/037).

Хранилище объектов сервера — тот же ``FileRemote``, что у ``file://``-remote, с
корнем ``Config.sync_remote_root`` (по умолчанию ``$COD_DOC_HOME/remotes``).
Поэтому CAS, потолки размеров и проверка имён у ``https://`` те же, что у
файлового remote, а маршруты (``api/sync``) только переводят HTTP в вызовы
``RemoteStore`` и обратно.

Доступ — владелец Bearer-токена из ``request_identity`` (его привязывает гейт
``api/auth.py``) и его роль в проекте по ``project_member`` (§3.8):

| Роль | Что |
|---|---|
| reader | чтение ref'ов, объектов, JSON, снапшотов |
| writer | + CAS ref'ов и JSON (аренды, ``meta.json`` эпохи), объекты, снапшоты |
| admin | + ``meta.schema_min``, участники проекта, токены |

**Проверка push (CLO-037).** Объект — changeset; сервер разбирает его тем же
``changeset.decode``, что pull, и отвергает (``SYNC-203``), если ``actor`` не
владелец токена или ``author`` хоть одной операции — не его handle и не handle
его агента, или changeset чужого проекта. Провенанс перестаёт быть свободной
строкой ровно там, где пишут несколько людей; локально ADR-012 как раньше.

Умный remote без хранилища доступа не работает вовсе: без владельца токена
роли не у кого спросить, а пустить анонима на запись — значит превратить
remote в глупый, не предупредив.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from cod_doc.infra.remote.errors import RemoteAuthError
from cod_doc.services import request_identity
from cod_doc.services.request_identity import ROLE_ADMIN, ROLE_READER, ROLE_WRITER
from cod_doc.services.sync_service.errors import AuthorMismatchError

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote
    from cod_doc.services.request_identity import Identity

__all__ = [
    "REMOTES_DIRNAME",
    "ROLE_ADMIN",
    "ROLE_READER",
    "ROLE_WRITER",
    "check_changeset",
    "remote_root",
    "require",
    "store",
]

REMOTES_DIRNAME: Final = "remotes"


def remote_root() -> Path:
    """Корень объектов сервера: ``sync_remote_root`` конфига или ``$COD_DOC_HOME/remotes``."""
    from pathlib import Path

    from cod_doc.config import Config, config_dir

    configured = Config.load().sync_remote_root
    return Path(configured) if configured else config_dir() / REMOTES_DIRNAME


def store() -> FileRemote:
    from cod_doc.infra.remote.file import FileRemote

    root = remote_root()
    root.mkdir(parents=True, exist_ok=True)
    return FileRemote(root)


def require(project_uid: str, minimum: str) -> Identity:
    """Владелец токена с ролью не ниже ``minimum`` в проекте; иначе ``RemoteAuthError``.

    Нет владельца — ``SYNC-201`` (401): гейт не привязал токен, значит хранилища
    доступа нет. Роль ниже — ``SYNC-202`` (403); не участник — тоже 403, без
    различения «проекта нет» и «вас в нём нет».
    """
    identity = request_identity.current()
    if identity is None:
        msg = "умный remote требует Bearer-токен (на сервере нет хранилища доступа: cod-doc access init)"
        raise RemoteAuthError(msg)
    role = identity.roles.get(project_uid)
    if role is None or not request_identity.role_at_least(role, minimum):
        have = role or "нет роли"
        msg = f"{identity.handle!r} в проекте {project_uid}: {have}, нужно {minimum}"
        raise RemoteAuthError(msg, code="SYNC-202")
    return identity


def check_changeset(identity: Identity, project_uid: str, sha: str, data: bytes) -> None:
    """Changeset принадлежит проекту и владельцу токена (CLO-037); иначе ``SYNC-203``.

    Непригодный объект (формат, sha) — ``ObjectMissingError`` (``SYNC-102``) из
    ``changeset.decode``: сервер не хранит то, что pull всё равно отверг бы.
    """
    from cod_doc.services.sync_service import changeset

    cs = changeset.decode(data, sha)
    if cs.project != project_uid:
        msg = f"changeset проекта {cs.project}, а записывается в {project_uid}"
        raise AuthorMismatchError(msg)
    _kind, actor = request_identity.split_author(cs.actor)
    if actor != identity.handle:
        msg = f"actor {cs.actor!r} — не владелец токена {identity.handle!r}"
        raise AuthorMismatchError(msg)
    foreign = sorted(
        {op.author for op in cs.ops if not request_identity.author_allowed(identity, op.author)}
    )
    if foreign:
        msg = (
            f"author операций {', '.join(map(repr, foreign))} не принадлежат токену "
            f"{identity.handle!r}; допустимо: {request_identity.allowed_authors(identity)}"
        )
        raise AuthorMismatchError(msg)
