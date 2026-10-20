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
    from collections.abc import Mapping
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote
    from cod_doc.services.request_identity import Identity

__all__ = [
    "REMOTES_DIRNAME",
    "ROLE_ADMIN",
    "ROLE_READER",
    "ROLE_WRITER",
    "check_changeset",
    "check_meta_forward",
    "check_snapshot",
    "remote_root",
    "require",
    "require_human",
    "store",
]

REMOTES_DIRNAME: Final = "remotes"


def remote_root() -> Path:
    """Корень объектов сервера: ``sync_remote_root`` конфига или ``$COD_DOC_HOME/remotes``."""
    from pathlib import Path

    from cod_doc.config import Config, config_dir

    configured = Config.load().sync_remote_root
    return Path(configured) if configured else config_dir() / REMOTES_DIRNAME


_CREATED_ROOTS: set[Path] = set()


def store() -> FileRemote:
    from cod_doc.infra.remote.file import FileRemote

    root = remote_root()
    if root not in _CREATED_ROOTS:
        root.mkdir(parents=True, exist_ok=True)
        _CREATED_ROOTS.add(root)
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


def require_human(project_uid: str, minimum: str) -> Identity:
    """``require`` для администрирования доступа: токен агента сюда не пускается.

    Агент admin'а иначе выпускал бы себе токены и раздавал роли — ровно та
    эскалация, ради которой доступ не выставлен в MCP (§3.9).
    """
    identity = require(project_uid, minimum)
    if identity.kind != "human":
        msg = f"{identity.handle!r}: участниками и токенами управляет человек, не агент"
        raise RemoteAuthError(msg, code="SYNC-202")
    return identity


def check_meta_forward(
    identity: Identity,
    project_uid: str,
    current: Mapping[str, object] | None,
    new: Mapping[str, object],
) -> None:
    """Не-admin двигает ``epoch``/``schema_min`` в ``meta.json`` только вперёд.

    Первый push новой эпохи поднимает их CAS'ом (CLO-032) — это право writer'а;
    опустить ``schema_min`` значило бы снова пустить старые реплики, это решение
    admin'а (``PUT meta``). Незнакомая серверу ревизия считается новее: её открыл
    релиз, которого у сервера ещё нет.
    """
    from cod_doc.infra import sync_epochs

    if current is None or identity.roles.get(project_uid) == ROLE_ADMIN:
        return
    for field in ("epoch", "schema_min"):
        old, value = current.get(field), new.get(field)
        if old == value or not isinstance(old, str):
            continue
        if not isinstance(value, str):
            msg = f"meta.json: {field} нельзя убрать"
            raise AuthorMismatchError(msg)
        try:
            older = sync_epochs.epoch_is_newer(old, value)
        except sync_epochs.UnknownRevisionError:
            older = False  # прежнее значение серверу незнакомо — сравнивать не с чем
        if older:
            msg = f"meta.json: {field} {value} старше {old} — опускает только admin (PUT meta)"
            raise AuthorMismatchError(msg)


def check_snapshot(project_uid: str, epoch: str, sha: str, data: bytes) -> None:
    """Заголовок снапшота совпадает с адресом записи; иначе ``SYNC-102``.

    Голова (``sha``) обязана уже лежать на remote changeset'ом — его проверил
    ``check_changeset``. Тело дальше заголовка клиент сверяет сам при загрузке
    (дайджест против головы, ``snapshot``/``clone``): сервер не пересобирает БД.
    """
    from cod_doc.services.sync_service import snapshot
    from cod_doc.services.sync_service.errors import ObjectMissingError

    header, _project_row = snapshot.peek(data)
    if (header.project, header.head, header.epoch) != (project_uid, sha, epoch):
        msg = (
            f"снапшот: заголовок ({header.project}, {header.head}, {header.epoch}) "
            f"не совпадает с адресом ({project_uid}, {sha}, {epoch})"
        )
        raise ObjectMissingError(msg)


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
    kind, actor = request_identity.split_author(cs.actor)
    # Вид в префиксе — как у author: ``agent:alice`` под токеном человека alice —
    # подмена роли в журнале чужой реплики.
    if actor != identity.handle or (kind or "human") != identity.kind:
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
