"""Доступ к серверу: actor, членство, Bearer-токены (RFC 29 §3.8, CLO-034).

**Хранилище доступа** — отдельная БД сервера (``Config.access_db_url``, по
умолчанию ``$COD_DOC_HOME/access.db``) на общей схеме миграций. Отдельная,
потому что actor — сущность сервера: один токен открывает все проекты, где
его владелец участник, а проекты лежат каждый в своей БД. Членство ссылается
на проект по ``project.uid``.

Нет файла хранилища — нет и гейта: машина локальная, всё работает как до
CLO-036. ``init_store`` создаёт файл, и с этого момента удалённым вызывающим
нужен токен.

**След записи (ADO-040).** Каждая мутация пишет activity event в журнал самого
хранилища — строка проекта ``_access`` (:data:`STORE_SLUG`) держит FK журнала
и в реестр ``config.yaml`` не попадает. Ревизий нет намеренно: ``revision_revert``
по ним отменил бы отзыв токена, а откат решения о доступе — новое решение,
а не возврат текста.

**Токен** — ``cdt_`` + 32 байта ``secrets``; хранится ``sha256``, показывается
один раз. Поиск — по уникальному индексу хэша, сверка — ``hmac.compare_digest``.
Отзыв и отключение действуют на следующем запросе: ``authenticate`` ничего не
кэширует.

Агент — actor с владельцем, ``handle`` — ``<владелец>/<имя>``. Роль агента в
проекте — роль владельца, суженная собственным членством агента, если оно
есть; выше владельца агент не поднимается никогда.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from cod_doc.infra.models import ActorModel, ApiTokenModel, ProjectMemberModel, ProjectModel
from cod_doc.services import activity_service
from cod_doc.services.request_identity import ROLES, Identity, role_at_least

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config, ProjectEntry

__all__ = [
    "KIND_AGENT",
    "KIND_HUMAN",
    "STORE_FILENAME",
    "STORE_SLUG",
    "TOKEN_PREFIX",
    "AccessError",
    "IssuedToken",
    "MemberRow",
    "TokenRow",
    "add_agent",
    "add_member",
    "authenticate",
    "create_token",
    "disable_actor",
    "hash_token",
    "init_store",
    "list_members",
    "list_tokens",
    "open_store",
    "project_uid_for",
    "record_denied",
    "remove_member",
    "revoke_token",
    "store_url",
    "upgrade_store",
]

STORE_FILENAME: Final = "access.db"
#: Строка ``project`` хранилища: держит FK журнала, в реестр не попадает.
STORE_SLUG: Final = "_access"
TOKEN_PREFIX: Final = "cdt_"
KIND_HUMAN: Final = "human"
KIND_AGENT: Final = "agent"
_TOKEN_BYTES: Final = 32
#: ``last_used`` пишется не чаще раза в минуту на токен: запись на каждый
#: запрос держала бы пишущую транзакцию SQLite на горячем пути гейта.
_TOUCH_EVERY: Final = timedelta(minutes=1)
_HANDLE_RE: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")


class AccessError(ValueError):
    """Неверный ввод администрирования доступа (нет actor'а, роль, дубль)."""


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """Выданный токен: ``token`` — открытый текст, второй раз его не увидеть."""

    uid: str
    handle: str
    label: str
    token: str


@dataclass(frozen=True, slots=True)
class MemberRow:
    handle: str
    kind: str
    project_uid: str
    role: str


@dataclass(frozen=True, slots=True)
class TokenRow:
    uid: str
    handle: str
    label: str
    created: datetime
    last_used: datetime | None
    revoked_at: datetime | None


# --- хранилище ----------------------------------------------------------------


def store_url(cfg: Config | None = None) -> str:
    """URL хранилища доступа: ``access_db_url`` конфига или файл в ``COD_DOC_HOME``."""
    from cod_doc.config import Config, config_dir

    url = (cfg or Config.load()).access_db_url
    return url or f"sqlite:///{config_dir() / STORE_FILENAME}"


_ENGINES: dict[str, tuple[Engine, sessionmaker[Session]]] = {}
_ENGINES_LOCK = threading.Lock()


def _factory(url: str) -> sessionmaker[Session]:
    from cod_doc.infra.db import make_engine, make_session_factory

    with _ENGINES_LOCK:
        cached = _ENGINES.get(url)
        if cached is None:
            engine = make_engine(url)
            cached = (engine, make_session_factory(engine))
            _ENGINES[url] = cached
        return cached[1]


def open_store(url: str | None = None) -> sessionmaker[Session] | None:
    """Фабрика сессий хранилища; ``None`` — хранилища нет, гейт выключен.

    Файл проверяется на каждом вызове (один ``stat``): хранилище, созданное
    при запущенном сервере, включает гейт без рестарта. Нефайловая БД
    (postgres) считается существующей — её создают снаружи.
    """
    from cod_doc.infra.db import sqlite_file_path

    final = url or store_url()
    path = sqlite_file_path(final)
    if path is not None and not path.is_file():
        return None
    return _factory(final)


def init_store(url: str | None = None) -> str:
    """Создать или догнать хранилище до головы миграций; вернуть его URL. Идемпотентно."""
    from cod_doc.config import config_dir
    from cod_doc.infra.db import sqlite_file_path, transactional
    from cod_doc.infra.sync_capture import set_author
    from cod_doc.services.project_service import upgrade_url

    final = url or store_url()
    path = sqlite_file_path(final)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    upgrade_url(final)
    with transactional(_factory(final)) as session:
        if _store_project_id(session, create=False) is None:
            set_author(session, "system:access")
            now = datetime.now(UTC)
            session.add(
                ProjectModel(
                    slug=STORE_SLUG,
                    title="access store",
                    root_path=str(config_dir()),
                    config_json={},
                    created=now,
                    updated=now,
                )
            )
    if path is not None:
        path.chmod(0o600)
    return final


def upgrade_store(url: str | None = None) -> str | None:
    """Догнать схему существующего хранилища; нет его — ``None``, ничего не создаётся.

    Для bootstrap контейнера: ``init_store`` включил бы гейт там, где его не
    просили, а без апгрейда новый код упал бы на старой схеме хранилища.
    """
    final = url or store_url()
    if open_store(final) is None:
        return None
    return init_store(final)


def _store_project_id(session: Session, *, create: bool = True) -> int | None:
    row_id = session.scalar(select(ProjectModel.row_id).where(ProjectModel.slug == STORE_SLUG))
    if row_id is None and create:
        msg = "хранилище доступа не инициализировано: cod-doc access init"
        raise AccessError(msg)
    return row_id


def _emit(
    session: Session,
    kind: str,
    author: str,
    *,
    scope_kind: str,
    scope_id: str,
    payload: dict[str, Any],
) -> None:
    project_id = _store_project_id(session)
    assert project_id is not None  # create=True бросает раньше
    activity_service.emit_for_write(
        session,
        project_id,
        kind,
        author,
        scope_kind=scope_kind,
        scope_id=scope_id,
        payload=payload,
    )


def project_uid_for(entry: ProjectEntry) -> str:
    """``project.uid`` проекта реестра — адрес проекта в членстве."""
    from cod_doc.infra.db import db_for_entry

    factory, _engine = db_for_entry(entry)
    with factory() as session:
        uid = session.scalar(select(ProjectModel.uid).where(ProjectModel.slug == entry.name))
    if not uid:
        msg = f"у проекта {entry.name!r} нет uid — догоните схему: cod-doc project migrate"
        raise AccessError(msg)
    return uid


# --- actor и членство ---------------------------------------------------------


def _actor(session: Session, handle: str) -> ActorModel:
    actor = session.scalar(select(ActorModel).where(ActorModel.handle == handle))
    if actor is None:
        msg = f"actor {handle!r} не найден"
        raise AccessError(msg)
    return actor


def _check_role(role: str) -> None:
    if role not in ROLES:
        msg = f"роль {role!r} неизвестна; допустимо: {', '.join(ROLES)}"
        raise AccessError(msg)


def _ensure_human(session: Session, handle: str, author: str) -> ActorModel:
    actor = session.scalar(select(ActorModel).where(ActorModel.handle == handle))
    if actor is not None:
        return actor
    if not _HANDLE_RE.fullmatch(handle):
        msg = f"handle {handle!r}: строчные латиница, цифры, '.', '_', '-'; до 63 символов"
        raise AccessError(msg)
    actor = ActorModel(
        uid=str(uuid.uuid7()), kind=KIND_HUMAN, handle=handle, created=datetime.now(UTC)
    )
    session.add(actor)
    session.flush()
    _emit(
        session,
        "access.actor_created",
        author,
        scope_kind="actor",
        scope_id=handle,
        payload={"kind": KIND_HUMAN},
    )
    return actor


def add_agent(session: Session, *, owner: str, name: str, author: str) -> str:
    """Завести агента ``<owner>/<name>``; вернуть его handle."""
    owner_actor = _actor(session, owner)
    if owner_actor.kind != KIND_HUMAN:
        msg = f"владелец агента — человек, а {owner!r} — {owner_actor.kind}"
        raise AccessError(msg)
    if not _HANDLE_RE.fullmatch(name):
        msg = f"имя агента {name!r}: строчные латиница, цифры, '.', '_', '-'"
        raise AccessError(msg)
    handle = f"{owner}/{name}"
    if session.scalar(select(ActorModel.uid).where(ActorModel.handle == handle)):
        msg = f"агент {handle!r} уже есть"
        raise AccessError(msg)
    session.add(
        ActorModel(
            uid=str(uuid.uuid7()),
            kind=KIND_AGENT,
            handle=handle,
            owner_uid=owner_actor.uid,
            created=datetime.now(UTC),
        )
    )
    session.flush()
    _emit(
        session,
        "access.actor_created",
        author,
        scope_kind="actor",
        scope_id=handle,
        payload={"kind": KIND_AGENT, "owner": owner},
    )
    return handle


def add_member(
    session: Session, *, handle: str, project_uid: str, role: str, author: str
) -> MemberRow:
    """Выдать роль в проекте (upsert). Человек заводится, если его нет.

    Агента нужно завести раньше (``add_agent``): его членство только сужает
    роль владельца, поэтому роль выше владельца отвергается.
    """
    _check_role(role)
    existing = session.scalar(select(ActorModel).where(ActorModel.handle == handle))
    actor = existing if existing is not None else _ensure_human(session, handle, author)
    if actor.kind == KIND_AGENT:
        owner_role = _role_of(session, actor.owner_uid, project_uid)
        if owner_role is None or not role_at_least(owner_role, role):
            msg = f"агент {handle!r} не выше владельца: у владельца {owner_role or 'нет роли'!r}"
            raise AccessError(msg)
    row = session.get(ProjectMemberModel, (project_uid, actor.uid))
    previous = row.role if row is not None else None
    if row is None:
        session.add(ProjectMemberModel(project_uid=project_uid, actor_uid=actor.uid, role=role))
    else:
        row.role = role
    session.flush()
    _emit(
        session,
        "access.member_set",
        author,
        scope_kind="project_member",
        scope_id=f"{project_uid}:{handle}",
        payload={"project_uid": project_uid, "handle": handle, "role": role, "from": previous},
    )
    return MemberRow(handle=handle, kind=actor.kind, project_uid=project_uid, role=role)


def remove_member(session: Session, *, handle: str, project_uid: str, author: str) -> bool:
    """Снять членство; ``False`` — его не было."""
    actor = _actor(session, handle)
    row = session.get(ProjectMemberModel, (project_uid, actor.uid))
    if row is None:
        return False
    session.delete(row)
    session.flush()
    _emit(
        session,
        "access.member_removed",
        author,
        scope_kind="project_member",
        scope_id=f"{project_uid}:{handle}",
        payload={"project_uid": project_uid, "handle": handle, "role": row.role},
    )
    return True


def list_members(session: Session, project_uid: str | None = None) -> list[MemberRow]:
    stmt = select(ActorModel.handle, ActorModel.kind, ProjectMemberModel).join(
        ProjectMemberModel, ProjectMemberModel.actor_uid == ActorModel.uid
    )
    if project_uid is not None:
        stmt = stmt.where(ProjectMemberModel.project_uid == project_uid)
    rows = session.execute(stmt.order_by(ActorModel.handle)).all()
    return [MemberRow(handle=h, kind=k, project_uid=m.project_uid, role=m.role) for h, k, m in rows]


def disable_actor(session: Session, *, handle: str, author: str) -> None:
    """Отключить actor'а: его токены (и токены его агентов) перестают работать сразу."""
    actor = _actor(session, handle)
    if actor.disabled:
        return
    actor.disabled = True
    session.flush()
    _emit(
        session,
        "access.actor_disabled",
        author,
        scope_kind="actor",
        scope_id=handle,
        payload={"kind": actor.kind},
    )


def _role_of(session: Session, actor_uid: str | None, project_uid: str) -> str | None:
    if actor_uid is None:
        return None
    row = session.get(ProjectMemberModel, (project_uid, actor_uid))
    return row.role if row is not None else None


# --- токены -------------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(session: Session, *, handle: str, label: str, author: str) -> IssuedToken:
    """Выпустить токен actor'у; открытый текст — только в возвращённом значении."""
    actor = _actor(session, handle)
    if actor.disabled:
        msg = f"actor {handle!r} отключён"
        raise AccessError(msg)
    if not label.strip():
        msg = "label токена обязателен: 'macbook', 'ci', 'cursor-cloud'"
        raise AccessError(msg)
    token = TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    row = ApiTokenModel(
        uid=str(uuid.uuid7()),
        actor_uid=actor.uid,
        label=label.strip(),
        token_hash=hash_token(token),
        created=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    _emit(
        session,
        "access.token_created",
        author,
        scope_kind="api_token",
        scope_id=row.uid,
        payload={"handle": handle, "label": row.label},
    )
    return IssuedToken(uid=row.uid, handle=handle, label=row.label, token=token)


def revoke_token(session: Session, *, token_uid: str, author: str) -> bool:
    """Отозвать токен по uid (или его уникальному префиксу); ``False`` — уже отозван."""
    rows = session.scalars(
        select(ApiTokenModel).where(ApiTokenModel.uid.startswith(token_uid, autoescape=True))
    ).all()
    if len(rows) != 1:
        msg = f"токен {token_uid!r}: " + ("не найден" if not rows else "префикс неоднозначен")
        raise AccessError(msg)
    row = rows[0]
    if row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(UTC)
    session.flush()
    _emit(
        session,
        "access.token_revoked",
        author,
        scope_kind="api_token",
        scope_id=row.uid,
        payload={"label": row.label},
    )
    return True


def list_tokens(session: Session, handle: str | None = None) -> list[TokenRow]:
    stmt = select(ApiTokenModel, ActorModel.handle).join(
        ActorModel, ActorModel.uid == ApiTokenModel.actor_uid
    )
    if handle is not None:
        stmt = stmt.where(ActorModel.handle == handle)
    rows = session.execute(stmt.order_by(ApiTokenModel.created)).all()
    return [
        TokenRow(
            uid=t.uid,
            handle=h,
            label=t.label,
            created=t.created,
            last_used=t.last_used,
            revoked_at=t.revoked_at,
        )
        for t, h in rows
    ]


def authenticate(session: Session, token: str) -> Identity | None:
    """Токен → владелец с ролями и законными авторами; ``None`` — не пускать.

    Не пускает: незнакомый или отозванный токен, отключённый actor, агент
    отключённого владельца.
    """
    if not token.startswith(TOKEN_PREFIX):
        return None
    digest = hash_token(token)
    row = session.scalar(select(ApiTokenModel).where(ApiTokenModel.token_hash == digest))
    if row is None or not hmac.compare_digest(row.token_hash, digest):
        return None
    if row.revoked_at is not None:
        return None
    actor = session.get(ActorModel, row.actor_uid)
    if actor is None or actor.disabled:
        return None
    owner = session.get(ActorModel, actor.owner_uid) if actor.owner_uid else None
    if actor.kind == KIND_AGENT and (owner is None or owner.disabled):
        return None
    _touch(row)
    return Identity(
        actor_uid=actor.uid,
        handle=actor.handle,
        kind=actor.kind,
        token_label=row.label,
        authors=_authors(session, actor),
        roles=MappingProxyType(_roles(session, actor)),
    )


def _touch(row: ApiTokenModel) -> None:
    now = datetime.now(UTC)
    last = row.last_used
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if last is None or now - last >= _TOUCH_EVERY:
        row.last_used = now


def _authors(session: Session, actor: ActorModel) -> frozenset[str]:
    if actor.kind == KIND_AGENT:
        return frozenset({actor.handle})
    agents = session.scalars(
        select(ActorModel.handle).where(
            ActorModel.owner_uid == actor.uid, ActorModel.disabled.is_(False)
        )
    ).all()
    return frozenset({actor.handle, *agents})


def _member_roles(session: Session, actor_uid: str) -> dict[str, str]:
    rows = session.scalars(
        select(ProjectMemberModel).where(ProjectMemberModel.actor_uid == actor_uid)
    ).all()
    return {r.project_uid: r.role for r in rows}


def _roles(session: Session, actor: ActorModel) -> dict[str, str]:
    own = _member_roles(session, actor.uid)
    if actor.kind != KIND_AGENT or actor.owner_uid is None:
        return own
    # Агент: роль владельца, суженная своей, если она задана.
    roles: dict[str, str] = {}
    for project_uid, owner_role in _member_roles(session, actor.owner_uid).items():
        narrowed = own.get(project_uid, owner_role)
        roles[project_uid] = narrowed if role_at_least(owner_role, narrowed) else owner_role
    return roles


# --- отказы -------------------------------------------------------------------


def record_denied(
    session: Session,
    *,
    code: str,
    ip: str,
    path: str,
    token_label: str | None = None,
    project: str | None = None,
) -> None:
    """``auth.denied`` в журнал хранилища (RFC 29 §10.11): 401/403/429 гейта."""
    project_id = _store_project_id(session, create=False)
    if project_id is None:
        return
    activity_service.emit(
        session,
        project_id,
        "auth.denied",
        actor_kind="system",
        actor_id=None,
        scope_kind="project",
        scope_id=project,
        payload={"token_label": token_label, "ip": ip, "path": path, "code": code},
    )
