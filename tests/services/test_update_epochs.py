"""CLO-032 / SCN-095: ``cod-doc update`` перед миграцией, открывающей эпоху.

SCN-095: «Реплика с тремя неупакованными операциями; новый релиз содержит
миграцию, меняющую authored-колонку. Update не накатывает миграцию и
завершается сообщением „3 незапушенные операции — сначала cod-doc push“;
схема реплики остаётся прежней, операции на месте.»

Миграция настоящая: БД стоит на ревизии перед головой цепочки, а реестр
``sync_epochs`` подменён так, что голова открывает эпоху (или нет — для
обратного случая). Пары ревизий выводятся из цепочки, а не пишутся литералами:
новая миграция иначе роняла бы эти тесты, ничего не меняя в поведении. Фаза B идёт через
``run_post_swap`` без свапа (``--skip-install``) — тот же код, что исполняет
новый бинарь после relay.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from cod_doc.config import Config, ProjectEntry
from cod_doc.infra import sync_epochs
from cod_doc.infra.models import ProjectModel, SyncOpModel
from cod_doc.services import update_service
from cod_doc.services.update_service import SkipFlags
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

SLUG = "demo"
_ORDER = sync_epochs.revision_order()
NEW = sync_epochs.code_head()
OLD = next(rev for rev, i in _ORDER.items() if i == _ORDER[NEW] - 1)


def _registry(*, head_opens: bool) -> MappingProxyType[str, bool]:
    """Реестр, где голова открывает эпоху (или нет), а ревизия перед ней — нет."""
    after_baseline = sorted(
        (rev for rev, i in _ORDER.items() if i > _ORDER[sync_epochs.SYNC_BASELINE]),
        key=_ORDER.__getitem__,
    )
    registry = {rev: rev == "0050_sync_rank" for rev in after_baseline}
    registry[NEW] = head_opens
    return MappingProxyType(registry)


@pytest.fixture
def head_opens_an_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_epochs, "MIGRATIONS", _registry(head_opens=True))


@pytest.fixture
def head_opens_no_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_epochs, "MIGRATIONS", _registry(head_opens=False))


def _replica(tmp_path: Path, *, pending: int, remote: bool) -> tuple[Config, Path]:
    root = tmp_path / SLUG
    db = root / ".cod-doc" / "state.db"
    db.parent.mkdir(parents=True)
    run_alembic("upgrade", OLD, db_url=f"sqlite:///{db}")
    engine = create_engine(f"sqlite:///{db}")
    now = datetime.now(UTC)
    with Session(engine) as s:  # мимо захвата: операции заводим сами
        proj = ProjectModel(
            slug=SLUG, title="Demo", root_path=str(root), config_json={}, uid=str(uuid.uuid4())
        )
        proj.created = now
        proj.updated = now
        s.add(proj)
        s.flush()
        for i in range(pending):
            s.add(
                SyncOpModel(
                    op_id=f"01JOP{i:021d}",
                    project_id=proj.row_id,
                    replica_uid="r",
                    hlc=f"{1_760_000_000_000 + i:013d}.0000.r",
                    entity_kind="task",
                    entity_uid=str(uuid.uuid4()),
                    kind="upsert",
                    fields="{}",
                    base="{}",
                    author="human:dakh",
                    changeset=None,
                )
            )
        s.commit()
    engine.dispose()
    cfg = Config.load()
    cfg.add_project(ProjectEntry(name=SLUG, path=str(root)))
    if remote:
        cfg.set_project_remote(SLUG, "origin", {"url": f"file://{tmp_path}/remote", "kind": "file"})
    return Config.load(), db


def _schema_and_ops(db: Path) -> tuple[str, int]:
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        ops = conn.execute(
            text("SELECT count(*) FROM sync_op WHERE changeset IS NULL")
        ).scalar_one()
    engine.dispose()
    return str(head), int(ops)


def _update(cfg: Config) -> update_service.UpdateReport:
    return update_service.run_post_swap(
        cfg, resumed={}, projects=[SLUG], skip=SkipFlags(install=True, repair=True)
    )


@pytest.mark.usefixtures("head_opens_an_epoch")
def test_scn_095_update_with_unpushed_ops_before_an_epoch_stops_with_sync_106(
    tmp_path: Path,
) -> None:
    cfg, db = _replica(tmp_path, pending=3, remote=True)

    report = _update(cfg)

    [result] = report.migrate
    assert not result.ok
    assert result.error is not None
    assert result.error.startswith("SYNC-106: ")
    assert "3 незапушенные операции — сначала cod-doc push" in result.error
    assert "export-pending -p demo" in result.error
    assert report.exit_code == update_service.EXIT_FAILED
    assert _schema_and_ops(db) == (OLD, 3)


@pytest.mark.usefixtures("head_opens_an_epoch")
def test_update_without_a_remote_is_not_checked(tmp_path: Path) -> None:
    cfg, db = _replica(tmp_path, pending=3, remote=False)
    [result] = _update(cfg).migrate
    assert result.ok, result.error
    assert _schema_and_ops(db) == (NEW, 3)


@pytest.mark.usefixtures("head_opens_an_epoch")
def test_update_with_everything_pushed_migrates(tmp_path: Path) -> None:
    cfg, db = _replica(tmp_path, pending=0, remote=True)
    [result] = _update(cfg).migrate
    assert result.ok, result.error
    assert _schema_and_ops(db) == (NEW, 0)


@pytest.mark.usefixtures("head_opens_no_epoch")
def test_migration_that_opens_no_epoch_is_not_held(tmp_path: Path) -> None:
    """Голова эпоху не открывает — незапушенное миграции не мешает."""
    cfg, db = _replica(tmp_path, pending=3, remote=True)
    [result] = _update(cfg).migrate
    assert result.ok, result.error
    assert _schema_and_ops(db) == (NEW, 3)


@pytest.mark.usefixtures("head_opens_an_epoch")
def test_unreadable_db_stops_the_migration_without_leaking_the_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Проверка эпохи не прочитала БД — fail closed; пароль hub-URL не попадает ни в лог, ни в отчёт."""
    secret = "s3cr3t-pass"
    url = f"postgresql+psycopg://dakh:{secret}@127.0.0.1:1/cod_doc_epoch_test"
    cfg = Config.load()
    cfg.add_project(ProjectEntry(name=SLUG, path=str(tmp_path), db_url=url))
    cfg.set_project_remote(SLUG, "origin", {"url": f"file://{tmp_path}/remote", "kind": "file"})
    migrated: list[str] = []
    monkeypatch.setattr(
        update_service.project_service,
        "migrate_entry",
        lambda entry: migrated.append(entry.name),
    )

    with caplog.at_level(logging.WARNING, logger="cod_doc.services.update_service"):
        [result] = _update(Config.load()).migrate

    assert not result.ok
    assert result.error is not None
    assert result.error.startswith("SYNC-106: проверка эпохи не прочитала БД проектов demo")
    assert migrated == []
    assert secret not in result.error
    assert secret not in caplog.text
    assert "***" in caplog.text
