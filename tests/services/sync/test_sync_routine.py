"""CLO-040: рутина ``sync`` — автосинк с сетью вне транзакции (RFC 29 §3.9).

Acceptance: «Проверка sync в CHECK_CATALOG делает pull, затем push через
фабрику сессий, не держа транзакцию рутины на время сети (RFC 29 §3.9); нет сети
— тихий пропуск без находки; несовпадение дайджеста после цикла — находка».

Две реплики одного проекта и ``file://``-remote (``_replicas``); реестр машины
знает remote проекта — его и читает проверка.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import create_engine, text, update

from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import transactional
from cod_doc.infra.models import TaskModel
from cod_doc.infra.remote.errors import RemoteUnavailableError
from cod_doc.services import routine_service
from cod_doc.services.sync_service import _local, pull
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

NAME = "sync_every_10m"


@pytest.fixture
def world(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    store = r.remote(tmp_path)
    _register(tmp_path, store)
    return a, b, store


def _register(tmp_path: Path, store: FileRemote, *, remote: bool = True) -> None:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    Config().add_project(ProjectEntry(name=r.SLUG, path=str(root)))
    if remote:
        Config.load().set_project_remote(
            r.SLUG, "origin", {"url": store.root.as_uri(), "kind": "file"}
        )
    Config.clear_cache()


def _run(rep: r.Replica, **check_args: Any) -> routine_service.RoutineRun:
    """Завести рутину (если её нет) и запустить, как движок: внутри своей транзакции."""
    with transactional(rep.sf) as s:
        if routine_service.get(s, rep.project_id, NAME) is None:
            routine_service.create(
                s,
                rep.project_id,
                name=NAME,
                check_name="sync",
                cron="*/10 * * * *",
                check_args={"author": r.AUTHOR, **check_args},
            )
        return routine_service.run_now(s, rep.project_id, NAME)


def _last_result(rep: r.Replica) -> dict[str, Any]:
    """Итог последнего прогона — из события ``routine.found_issue`` или пусто."""
    from sqlalchemy import select

    from cod_doc.infra.models import ActivityEventModel

    with transactional(rep.sf, commit=False) as s:
        ev = s.scalars(
            select(ActivityEventModel)
            .where(ActivityEventModel.kind == "routine.found_issue")
            .order_by(ActivityEventModel.row_id.desc())
        ).first()
        return dict(ev.payload["result"]) if ev is not None else {}


def test_sync_pulls_then_pushes_both_replicas_converge(world: tuple) -> None:
    a, b, _store = world
    r.make_task(a, "DEM-001")
    assert r.pending(a) > 0

    ran_a = _run(a)
    assert ran_a.status == "done"
    assert ran_a.findings_count == 0
    assert r.pending(a) == 0  # хвост уехал push'ем

    ran_b = _run(b)
    assert ran_b.status == "done"
    assert ran_b.findings_count == 0
    assert r.task_description(b, "DEM-001") is not None  # pull B принёс задачу A
    _run(a)  # рутина B (authored-определение) приехала и к A
    assert r.authored_state(a) == r.authored_state(b)


def test_network_runs_outside_the_routine_transaction(
    world: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Во время pull строка ``routine_run`` «running» уже зафиксирована и видна другим."""
    a, _b, _store = world
    r.make_task(a, "DEM-001")
    seen: list[str] = []
    original = pull.pull

    def spy(*args: Any, **kwargs: Any) -> pull.PullResult:
        other = create_engine(f"sqlite:///{a.db_path}")
        try:
            with other.connect() as conn:
                seen.extend(conn.execute(text("SELECT status FROM routine_run")).scalars().all())
        finally:
            other.dispose()
        return original(*args, **kwargs)

    monkeypatch.setattr(pull, "pull", spy)
    ran = _run(a)
    assert seen == ["running"]
    assert ran.status == "done"


def test_offline_is_a_quiet_skip_without_findings(
    world: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _b, _store = world

    def offline(*_a: object, **_k: object) -> object:
        msg = "remote недоступен"
        raise RemoteUnavailableError(msg)

    monkeypatch.setattr(_local, "open_store", offline)
    ran = _run(a)
    assert ran.status == "done"
    assert ran.findings_count == 0


def test_project_without_remote_is_skipped(tmp_path: Path) -> None:
    a = r.replica_a(tmp_path)
    _register(tmp_path, r.remote(tmp_path), remote=False)
    ran = _run(a)
    assert ran.status == "done"
    assert ran.findings_count == 0


def test_digest_mismatch_after_the_cycle_is_a_finding(world: tuple) -> None:
    a, b, _store = world
    r.make_task(a, "DEM-001")
    _run(a)
    _run(b)
    # Порча состояния B мимо журнала: голова та же, что у remote, а строки другие.
    with b.engine.begin() as conn:
        conn.execute(update(TaskModel).values(title="испорчено мимо журнала"))

    ran = _run(b)

    assert ran.status == "done"
    assert ran.findings_count == 1
    found = _last_result(b)["findings"]
    assert [f["kind"] for f in found] == ["digest_mismatch"]


def test_sync_errors_other_than_offline_become_a_finding(
    world: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cod_doc.services.sync_service.errors import EpochPassedError

    a, _b, _store = world

    def stale(*_a: object, **_k: object) -> object:
        msg = "remote перешёл в новую эпоху"
        raise EpochPassedError(msg)

    monkeypatch.setattr(pull, "pull", stale)
    ran = _run(a)
    assert ran.status == "done"
    assert ran.findings_count == 1
    assert _last_result(a)["findings"][0]["code"] == "SYNC-105"


def test_sync_is_in_the_catalog_and_owns_its_transactions() -> None:
    assert "sync" in routine_service.CHECK_CATALOG
    assert "sync" in routine_service.OWN_TRANSACTION_CHECKS
