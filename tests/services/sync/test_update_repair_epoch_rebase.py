"""CLO-053: починка ``cod-doc update`` не блокирует перескок отставшей реплики на снапшот эпохи.

Acceptance: «После cod-doc update на схему новой эпохи и последующего pull
отставшая реплика перескакивает на снапшот эпохи без SYNC-105, если пользователь
сам ничего не правил; починка проекта выполняется (до или после перескока) и её
результат уезжает обычным push; тест на сценарий update → pull».

Мир и отставание — из ``test_epoch_rebase`` (CLO-050). Фаза D ``update`` —
``update_service._repair_in_session`` поверх БД реплики B; чинить ей есть что:
у B нет дерева документации (``tree_seed``) и есть дрейф markdown ``guide``.
"""

# Фикстура world импортирована из test_epoch_rebase — параметр её имени не переопределение.
# ruff: noqa: F811

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import repair_service, update_service
from cod_doc.services.sync_service import deferred_repair
from cod_doc.services.sync_service.errors import EpochPassedError
from tests.services.sync import _replicas as r
from tests.services.sync.test_epoch_rebase import (  # noqa: F401 — autouse-фикстура эпох
    NEW,
    _head,
    _head_opens_an_epoch,
    _lagging,
    _pull,
    _push,
    _set_schema,
    world,
)

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote


def _register(tmp_path: Path, store: FileRemote, *, remote: bool = True) -> ProjectEntry:
    """B в реестре машины: корень с дрейфом markdown ``guide`` и, по желанию, remote."""
    root = tmp_path / "b-root"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("# guide\n\n## Intro\n\nправка руками\n")
    (root / "MASTER.md").write_text("# demo\n")
    entry = ProjectEntry(name=r.SLUG, path=str(root))
    Config().add_project(entry)
    if remote:
        Config.load().set_project_remote(
            r.SLUG, "origin", {"url": store.root.as_uri(), "kind": "file"}
        )
    Config.clear_cache()
    found = Config.load().get_project(r.SLUG)
    assert found is not None
    return found


def _phase_d(b: r.Replica, entry: ProjectEntry, *, has_remote: bool) -> repair_service.RepairResult:
    return update_service._repair_in_session(
        b.sf, entry, slug=r.SLUG, ttl_minutes=60, has_remote=has_remote
    )


def _deferred(b: r.Replica) -> bool:
    with transactional(b.sf, commit=False) as s:
        return deferred_repair.is_deferred(s, b.project_id)


def test_repair_without_deferral_is_what_broke_the_rebase(world: tuple, tmp_path: Path) -> None:
    """Регрессия: починка сразу после update копит хвост — pull отказывает SYNC-105."""
    a, b, store = world
    _lagging(a, b, store)
    entry = _register(tmp_path, store)
    _set_schema(b, NEW)  # update: схема новой эпохи
    with transactional(b.sf) as s:
        repair_service.apply(
            s,
            project_id=b.project_id,
            root_path=entry.root,
            master_path=entry.master_path,
            slug=r.SLUG,
            author=update_service.REPAIR_AUTHOR,
        )
    assert r.pending(b) > 0
    with pytest.raises(EpochPassedError):
        _pull(b, store)


def test_scn_update_then_pull_rebases_and_repair_ships_by_push(
    world: tuple, tmp_path: Path
) -> None:
    a, b, store = world
    _, opened = _lagging(a, b, store)
    entry = _register(tmp_path, store)
    _set_schema(b, NEW)

    deferred = _phase_d(b, entry, has_remote=True)

    assert deferred.deferred is not None  # эпоха головы B — прошлая
    assert deferred.actions == []
    assert deferred.ok
    assert r.pending(b) == 0  # отметка — local-колонка, хвоста нет
    assert _deferred(b)

    result = _pull(b, store)

    assert result.rebased == opened  # перескок без SYNC-105
    assert result.repaired is not None
    assert result.repaired["applied"] > 0
    assert not _deferred(b)
    tail = r.pending(b)
    assert tail > 0  # записи починки — обычный хвост
    shipped = _push(b, store)
    assert shipped.head is not None
    assert r.pending(b) == 0
    assert _head(b) == shipped.head
    # A получает результат починки обычным pull
    _pull(a, store)
    assert r.authored_state(a) == r.authored_state(b)


def test_remote_still_in_the_old_epoch_repair_runs_after_plain_pull(
    world: tuple, tmp_path: Path
) -> None:
    """B обновилась первой: remote эпоху не открывал, перескока нет — чинит pull."""
    a, b, store = world
    r.make_task(a, "DEM-001")
    _push(a, store)
    _pull(b, store)
    entry = _register(tmp_path, store)
    _set_schema(b, NEW)

    assert _phase_d(b, entry, has_remote=True).deferred is not None
    result = _pull(b, store)

    assert result.rebased is None
    assert result.repaired is not None
    assert not _deferred(b)
    assert r.pending(b) > 0


def test_project_without_remote_is_repaired_immediately(world: tuple, tmp_path: Path) -> None:
    a, b, store = world
    _lagging(a, b, store)
    entry = _register(tmp_path, store, remote=False)
    _set_schema(b, NEW)

    result = _phase_d(b, entry, has_remote=False)

    assert result.deferred is None
    assert result.applied > 0
    assert not _deferred(b)


def test_dry_run_pull_does_not_repair(world: tuple, tmp_path: Path) -> None:
    a, b, store = world
    _lagging(a, b, store)
    entry = _register(tmp_path, store)
    _set_schema(b, NEW)
    _phase_d(b, entry, has_remote=True)

    result = _pull(b, store, dry_run=True)

    assert result.repaired is None
    assert _deferred(b)
    assert r.pending(b) == 0
    with transactional(b.sf, commit=False) as s:
        assert s.scalars(select(ProjectModel.root_path)).first() == "/tmp/b"
