"""``cod-doc clone``: проект с remote на чистой машине (RFC 29 §3.6, §10.6, §10.8; CLO-029).

**Адрес.** ``URL`` clone — каталог проекта на remote: URL remote плюс
``/<project_uid>`` (раскладка §3.5). Плоское хранилище не умеет перечислять
проекты (в ``RemoteStore`` нет листинга), поэтому uid называет сам URL;
готовую строку печатает ``cod-doc sync status`` (поле ``clone_url``). В
реестр пишется remote ``origin`` с URL без uid — как после ``remote add``.

**Ход** (сеть вне транзакций, как у pull):

1. ``refs/main`` remote; нет — ``SYNC-110`` (проекта на remote нет).
2. Указатель ``meta.json["snapshot"]``: тело снапшота и changeset его
   головы — по адресу, его дайджест — эталон загрузки. Слаг проекта —
   строка ``project`` снапшота; ``--project`` обязан с ним совпасть: слаг
   уезжает (``project.slug`` authored), и иной локальный слаг первый же pull
   переписал бы под реестром. Без снапшота (remote писали до CLO-029)
   ``--project`` обязателен и сверяется после проигрывания.
3. Отказ до записи: слаг уже в реестре или ``<dir>/.cod-doc/state.db`` есть.
4. БД — ``alembic upgrade head`` (``project_service.migrate_entry``), строка
   ``project`` с uid remote и своя реплика ``is_self`` — без дефолтных рутин и
   дерева разделов ``project add``: они приедут с remote, а две независимо
   заведённые строки с одним натуральным ключом — склейка (CLO-023), не
   clone (находка F2 аудита секции B).
5. Журнал и индекс changeset'ов собираются из объектов remote по адресам —
   обход ``parents`` от головы снапшота (``snapshot.load_history``): снапшот
   несёт только строки, их покрывает дайджест. Затем снапшот грузится под
   ``sync_capture.suspended`` одной транзакцией (``snapshot.load``: тот же
   гейт, что у pull, и сверка дайджеста до коммита), голова реплики — его
   changeset.
6. Хвост после снапшота — обычный ``pull``.
7. Дайджест реплики сверяется с дайджестом changeset'а её головы — ``sync
   verify`` после clone равен, пока remote не ушёл вперёд.
8. Регистрация в ``~/.cod-doc/config.yaml`` (``Config.add_project`` и
   ``remotes.add``), файлы ``.cod-doc/`` (``Project.init``, как у
   ``project add``), событие ``sync.cloned``.

Сбой любого шага до регистрации убирает созданное: файл БД, каталог
``.cod-doc/`` и каталог проекта, если их создал clone. В MCP clone нет
намеренно (§10.7): реестр машины — дело человека.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlparse, urlunparse

from sqlalchemy import select

from cod_doc.infra import sync_capture as cap
from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import self_replica
from cod_doc.infra.models import ChangesetModel, ProjectModel
from cod_doc.infra.remote.base import check_name, open_remote
from cod_doc.services import activity_service
from cod_doc.services.sync_service import _local, digest, snapshot
from cod_doc.services.sync_service import pull as pull_mod
from cod_doc.services.sync_service.errors import ObjectMissingError, RemoteConfigError

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import ProjectEntry
    from cod_doc.infra.remote.base import RemoteStore

__all__ = ["CloneResult", "clone", "split_url"]

#: Имя remote, под которым clone регистрирует источник (как у git).
ORIGIN: Final = "origin"
_SQLITE_SIDECARS: Final = ("-wal", "-shm", "-journal")
#: ``project.slug`` — ``String(64)``.
_MAX_SLUG: Final = 64


@dataclass(frozen=True, slots=True)
class CloneResult:
    project: str
    project_uid: str
    root: str
    remote: str
    url: str
    #: Голова снапшота, с которого стартовал clone; ``None`` — снапшота нет.
    snapshot: str | None
    head: str | None
    #: Changeset'ы хвоста после снапшота и их операции.
    applied: list[str] = field(default_factory=list)
    ops: int = 0
    digest: str = ""


def split_url(url: str) -> tuple[str, str]:
    """``(URL remote, project_uid)`` из URL каталога проекта на remote."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    parent, _, uid = path.rpartition("/")
    try:
        check_name(uid, "project_uid")
    except ValueError:
        msg = f"clone {url!r}: нужен URL каталога проекта — <URL remote>/<project_uid>"
        raise RemoteConfigError(msg) from None
    return urlunparse(parsed._replace(path=parent)), uid


@dataclass
class _Target:
    """Что clone создал на диске — чтобы убрать при сбое."""

    root: Path
    created_root: bool = False
    created_dir: bool = False
    engine: Engine | None = None
    db_url: str = ""

    @property
    def db_path(self) -> Path:
        from cod_doc.infra.db import DEFAULT_EMBEDDED_PATH

        return self.root / DEFAULT_EMBEDDED_PATH

    def cleanup(self) -> None:
        from cod_doc.infra.db import evict_cached_engine

        if self.engine is not None:
            self.engine.dispose()
        if self.db_url:
            evict_cached_engine(self.db_url)
        db = self.db_path
        for path in (db, *(db.with_name(db.name + s) for s in _SQLITE_SIDECARS)):
            path.unlink(missing_ok=True)
        if self.created_root:
            shutil.rmtree(self.root, ignore_errors=True)
        elif self.created_dir:
            shutil.rmtree(db.parent, ignore_errors=True)


def _prepare(root: Path) -> _Target:
    target = _Target(root=root)
    if target.db_path.exists():
        msg = f"clone: {target.db_path} уже есть — проект здесь уже заведён"
        raise RemoteConfigError(msg)
    target.created_root = not root.exists()
    target.created_dir = not target.db_path.parent.exists()
    return target


def _slug_ok(value: str) -> bool:
    """Слаг — имя каталога и ключ реестра: ``[A-Za-z0-9_][A-Za-z0-9._-]*``, ≤ 64."""
    try:
        check_name(value, "слаг")
    except ValueError:
        return False
    return len(value) <= _MAX_SLUG


def _resolve_slug(
    store: RemoteStore, uid: str, slug: str | None
) -> tuple[str, tuple[bytes, snapshot.Header] | None]:
    """Слаг клона и тело снапшота (если он есть)."""
    found = snapshot.pointer(store, uid)
    if found is None:
        if slug is None:
            msg = "clone: на remote нет снапшота — назовите проект: --project SLUG"
            raise RemoteConfigError(msg)
        return slug, None
    data = store.get_snapshot(uid, found[0])
    header, project_fields = snapshot.peek(data)
    if header.head != found[0].sha:
        msg = f"снапшот {found[0].key}: голова в заголовке {header.head}"
        raise ObjectMissingError(msg)
    remote_slug = project_fields.get("slug")
    # Слаг с remote — недоверенный ввод: из него собирается путь ./<slug>.
    if not isinstance(remote_slug, str) or not _slug_ok(remote_slug):
        msg = f"снапшот: недопустимый слаг проекта {remote_slug!r}"
        raise ObjectMissingError(msg)
    if slug is not None and slug != remote_slug:
        msg = f"clone: проект на remote называется {remote_slug!r}, а не {slug!r}"
        raise RemoteConfigError(msg)
    return remote_slug, (data, header)


def _create_project(sf: sessionmaker[Session], uid: str, slug: str, root: Path) -> None:
    """Строка ``project`` с uid remote и своя реплика — без захвата и бутстрапа."""
    with transactional(sf) as session, cap.suspended(session):
        row = ProjectModel(slug=slug, title=slug, root_path=str(root), config_json={})
        row.uid = uid
        session.add(row)
        session.flush()
        self_replica(session, row.row_id)


def _load_snapshot(
    sf: sessionmaker[Session], store: RemoteStore, slug: str, data: bytes, header: snapshot.Header
) -> None:
    with transactional(sf, commit=False) as session:
        ref = _local.project_ref(session, slug)
    # Журнал и индекс — из changeset'ов по адресам от головы: снапшот их не несёт.
    history = snapshot.load_history(sf, store, ref, header.head)
    with transactional(sf) as session:
        snapshot.load(session, ref, data, head=header.head, head_cs=history.head)


def _check_digest(sf: sessionmaker[Session], slug: str) -> tuple[str | None, str]:
    """Дайджест реплики = дайджесту changeset'а её головы; иначе SYNC-102."""
    with transactional(sf, commit=False) as session:
        ref = _local.project_ref(session, slug)
        head = _local.local_head(session, ref.row_id)
        local = digest.compute(session, ref.row_id)
        row = session.get(ChangesetModel, head) if head else None
        expected = None if row is None else row.digest
    if expected is not None and expected not in (local, _local.DIGEST_PENDING):
        msg = f"clone: дайджест реплики {local[:12]} ≠ дайджесту головы {expected[:12]}"
        raise ObjectMissingError(msg)
    return head, local


def _check_slug(sf: sessionmaker[Session], uid: str, name: str) -> None:
    """Слаг после проигрывания = слагу реестра: без снапшота его привёз первый changeset."""
    with transactional(sf, commit=False) as session:
        found = session.scalars(select(ProjectModel.slug).where(ProjectModel.uid == uid)).one()
    if found != name:
        msg = f"clone: проект на remote называется {found!r}, а не {name!r}"
        raise RemoteConfigError(msg)


def _register(entry: ProjectEntry, remote_url: str) -> None:
    from cod_doc.config import Config
    from cod_doc.core.project import Project
    from cod_doc.services.sync_service import remotes

    Config.load().add_project(entry)
    remotes.add(entry.name, ORIGIN, remote_url)
    Project(entry).init()


def _emit(sf: sessionmaker[Session], result: CloneResult, author: str) -> None:
    with transactional(sf) as session:
        ref = _local.project_ref(session, result.project)
        activity_service.emit_for_write(
            session,
            ref.row_id,
            "sync.cloned",
            author,
            scope_kind="project",
            scope_id=ref.slug,
            payload={
                "remote": ORIGIN,
                "url": result.url,
                "snapshot": result.snapshot,
                "head": result.head,
                "applied": result.applied,
                "ops": result.ops,
            },
            summary=f"clone {result.url}: голова {(result.head or '—')[:12]}",
        )


def clone(
    url: str,
    *,
    root: Path | None = None,
    slug: str | None = None,
    author: str,
    store: RemoteStore | None = None,
) -> CloneResult:
    """Завести проект с remote: снапшот + хвост, реестр, своя реплика (§10.6).

    ``root`` — корень проекта, по умолчанию ``./<slug>``; ``store`` —
    транспорт вместо открытого по URL (тесты).
    """
    from cod_doc.config import Config, ProjectEntry
    from cod_doc.infra.db import db_for_entry, db_url_for_entry
    from cod_doc.services import project_service

    remote_url, uid = split_url(url)
    store = store if store is not None else open_remote(remote_url)
    if store.get_ref(uid, _local.MAIN) is None:
        msg = f"clone: на remote нет проекта {uid} (refs/main пуст)"
        raise RemoteConfigError(msg)
    if slug is not None and not _slug_ok(slug):
        msg = f"clone: недопустимый слаг {slug!r}"
        raise RemoteConfigError(msg)
    name, snap = _resolve_slug(store, uid, slug)
    if Config.load().get_project(name) is not None:
        msg = f"clone: проект {name!r} уже есть в реестре"
        raise RemoteConfigError(msg)
    root = (root if root is not None else Path.cwd() / name).expanduser().resolve()
    target = _prepare(root)
    entry = ProjectEntry(name=name, path=str(root))
    try:
        migrated = project_service.migrate_entry(entry)
        if not migrated.ok:
            msg = f"clone: схема БД не накатилась: {migrated.error}"
            raise RemoteConfigError(msg)
        target.db_url = db_url_for_entry(entry)
        sf, target.engine = db_for_entry(entry)
        _create_project(sf, uid, name, root)
        if snap is not None:
            _load_snapshot(sf, store, name, *snap)
        pulled = pull_mod.pull(sf, name, remote=ORIGIN, author=author, store=store)
        _check_slug(sf, uid, name)
        head, local_digest = _check_digest(sf, name)
    except BaseException:
        target.cleanup()
        raise
    result = CloneResult(
        project=name,
        project_uid=uid,
        root=str(root),
        remote=ORIGIN,
        url=remote_url,
        snapshot=None if snap is None else snap[1].head,
        head=head,
        applied=pulled.applied,
        ops=pulled.ops,
        digest=local_digest,
    )
    _register(entry, remote_url)
    _emit(sf, result, author)
    return result
