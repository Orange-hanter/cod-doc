"""CLO-031: аренда замка checkout через remote (RFC 29 §3.6, Q-011, §10.10).

Acceptance: «При настроенном remote checkout_service.checkout сначала берёт
leases/checkout/<task_uid> через CAS, потом пишет БД (при ошибке записи аренда
снимается); занято → SYNC-108, нет сети → SYNC-109, --offline / offline=True —
провизорный замок; release, complete и release_stale снимают аренду; без
remote — ADO-039 как сегодня; тест на SCN-094.»

Плюс разрешение конфликта ``double_checkout`` (Q-011): держатель остаётся
один, замок проигравшего снимается, аренда переписывается на победителя.

«Настроенный remote» — запись ``remotes`` проекта ``demo`` в ``config.yaml``
(фикстура ``lab``), транспорт — file-remote в tmp; обе реплики видят один
конфиг, как две машины с одинаковым ``cod-doc remote add``.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import self_replica
from cod_doc.infra.models import ActivityEventModel, ProjectModel, SyncRefModel, TaskModel
from cod_doc.services import checkout_service, task_service
from cod_doc.services.sync_service import checkout_lease, conflicts, pull, push
from cod_doc.services.sync_service.errors import (
    CheckoutLeaseHeldError,
    CheckoutLeaseUnavailableError,
    RemoteUnavailableError,
)
from cod_doc.services.task_errors import CheckoutConflictError
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

TASK = "DEM-001"
AGENT_A = "agent:laptop"
AGENT_B = "agent:desktop"


# --- обвязка ---------------------------------------------------------------------------


def _register(tmp_path: Path, a: r.Replica, store: FileRemote) -> None:
    cfg = Config.load()
    cfg.add_project(ProjectEntry(name=r.SLUG, path=str(tmp_path), db_url=f"sqlite:///{a.db_path}"))
    Config.load().set_project_remote(r.SLUG, "origin", {"url": store.root.as_uri(), "kind": "file"})


def _sync(rep: r.Replica, store: FileRemote) -> None:
    push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)
    pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


@pytest.fixture
def lab(tmp_path: Path, isolated_cod_doc_home: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    """Две реплики с общей задачей ``DEM-001`` (todo) и настроенным file-remote."""
    del isolated_cod_doc_home
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    store = r.remote(tmp_path)
    _register(tmp_path, a, store)
    r.make_task(a, TASK)
    _sync(a, store)
    _sync(b, store)
    return a, b, store


@pytest.fixture
def bare(tmp_path: Path, isolated_cod_doc_home: Path) -> tuple[r.Replica, FileRemote]:
    """Реплика без записи remote в конфиге — ADO-039 как до CLO-031."""
    del isolated_cod_doc_home
    a = r.replica_a(tmp_path)
    r.make_task(a, TASK)
    return a, r.remote(tmp_path)


def _uids(rep: r.Replica) -> tuple[str, str]:
    """(uid проекта, uid задачи)."""
    with transactional(rep.sf, commit=False) as s:
        proj = s.get_one(ProjectModel, rep.project_id)
        task = s.scalars(select(TaskModel).where(TaskModel.task_id == TASK)).one()
        return str(proj.uid), str(task.uid)


def _lease(rep: r.Replica, store: FileRemote) -> checkout_lease.Holder | None:
    project_uid, task_uid = _uids(rep)
    got = checkout_lease.read(store, project_uid, task_uid)
    return None if got is None else got[0]


def _replica(rep: r.Replica) -> str:
    with transactional(rep.sf) as s:
        return self_replica(s, rep.project_id)


def _task(rep: r.Replica) -> TaskModel:
    with transactional(rep.sf, commit=False) as s:
        row = s.scalars(select(TaskModel).where(TaskModel.task_id == TASK)).one()
        s.expunge(row)
        return row


def _checkout(
    rep: r.Replica, agent: str, *, offline: bool = False
) -> checkout_service.CheckoutResult:
    extra = {"offline": True} if offline else {}  # без флага — вызов, как до CLO-031
    with transactional(rep.sf) as s:
        return checkout_service.checkout(s, TASK, project_id=rep.project_id, agent=agent, **extra)


def _release(rep: r.Replica, agent: str, *, force: bool = False) -> None:
    with transactional(rep.sf) as s:
        checkout_service.release(s, TASK, project_id=rep.project_id, agent=agent, force=force)


def _markers(rep: r.Replica) -> list[str]:
    with transactional(rep.sf, commit=False) as s:
        return list(
            s.scalars(
                select(SyncRefModel.name).where(SyncRefModel.remote == checkout_lease.MARKER_REMOTE)
            )
        )


def _events(rep: r.Replica, kind: str) -> list[dict[str, Any]]:
    with transactional(rep.sf, commit=False) as s:
        rows = s.scalars(select(ActivityEventModel).where(ActivityEventModel.kind == kind))
        return [dict(row.payload or {}) for row in rows]


class _Offline:
    """Транспорт без сети: любой вызов — SYNC-101."""

    def __getattr__(self, name: str) -> Any:
        def fail(*_args: object, **_kwargs: object) -> None:
            raise RemoteUnavailableError("сеть недоступна")

        return fail


# --- SCN-094 ---------------------------------------------------------------------------


def test_scn_094_second_checkout_is_refused_before_any_work(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    """SCN-094: A берёт задачу и аренду; B получает SYNC-108 «кто и когда», статус на B прежний."""
    a, b, store = lab
    taken = _checkout(a, AGENT_A)
    with pytest.raises(CheckoutLeaseHeldError) as refused:
        _checkout(b, AGENT_B)
    on_b = _task(b)
    assert (on_b.status, on_b.checked_out_by) == ("todo", None)

    assert (taken.new_status, taken.lease) == ("in_progress", "leased")
    holder = _lease(a, store)
    assert holder is not None
    assert (holder.task_id, holder.agent, holder.replica) == (TASK, AGENT_A, _replica(a))
    assert holder.ttl_minutes == checkout_lease.DEFAULT_TTL_MINUTES
    assert taken.checked_out_at is not None
    assert abs(holder.acquired_at - taken.checked_out_at) < timedelta(seconds=1)
    assert refused.value.code == "SYNC-108"
    assert isinstance(refused.value, CheckoutConflictError)  # поверхности ловят как 409
    message = str(refused.value)
    assert message.startswith("SYNC-108: задача DEM-001 взята agent:laptop")
    assert holder.acquired_at.strftime("%H:%M") in message
    assert _lease(a, store) == holder  # отказ аренду не тронул


def test_same_agent_on_another_replica_is_also_refused(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    """Держатель — пара (агент, реплика): тот же агент со второй машины — SYNC-108."""
    a, b, _store = lab
    _checkout(a, AGENT_A)
    with pytest.raises(CheckoutLeaseHeldError):
        _checkout(b, AGENT_A)


def test_same_agent_rechecks_out_idempotently(lab: tuple[r.Replica, r.Replica, FileRemote]) -> None:
    a, _b, store = lab
    _checkout(a, AGENT_A)
    project_uid, task_uid = _uids(a)
    before = checkout_lease.read(store, project_uid, task_uid)
    again = _checkout(a, AGENT_A)
    assert (again.idempotent, again.lease) == (True, "leased")
    assert checkout_lease.read(store, project_uid, task_uid) == before  # тот же etag


def test_lock_synced_under_same_agent_name_is_refused_on_recheckout(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    """Замок A приехал на B pull'ом; «идемпотентный» checkout тем же именем на B — SYNC-108."""
    a, b, store = lab
    _checkout(a, AGENT_A)
    _sync(a, store)
    _sync(b, store)
    assert _task(b).checked_out_by == AGENT_A
    with pytest.raises(CheckoutLeaseHeldError):
        _checkout(b, AGENT_A)


# --- без remote, без сети, при сбое записи ----------------------------------------------


def test_without_remote_checkout_is_ado_039(
    bare: tuple[r.Replica, FileRemote], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, store = bare
    monkeypatch.setattr(checkout_lease, "open_store", lambda *_: pytest.fail("сеть без remote"))
    taken = _checkout(a, AGENT_A, offline=True)
    assert (taken.new_status, taken.lease) == ("in_progress", None)
    assert _markers(a) == []
    assert _events(a, "task.checkout_leased") == []
    _release(a, AGENT_A)
    assert _events(a, "task.checkout_lease_released") == []
    assert not (store.root / "leases").exists()


def test_unreachable_remote_is_sync_109_and_task_untouched(
    lab: tuple[r.Replica, r.Replica, FileRemote], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _b, _store = lab
    monkeypatch.setattr(checkout_lease, "open_store", lambda *_: _Offline())
    with pytest.raises(CheckoutLeaseUnavailableError) as refused:
        _checkout(a, AGENT_A)
    assert refused.value.code == "SYNC-109"
    assert "--offline" in str(refused.value)
    on_a = _task(a)
    assert (on_a.status, on_a.checked_out_by) == ("todo", None)


def test_failed_write_drops_the_lease_and_keeps_the_original_error(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    """Аренда взята, транзакция записи откатилась → аренды нет, наружу — исходная ошибка."""
    a, _b, store = lab

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom), transactional(a.sf) as s:
        checkout_service.checkout(s, TASK, project_id=a.project_id, agent=AGENT_A)
        assert _lease(a, store) is not None  # аренда — до записи
        raise Boom
    assert _lease(a, store) is None
    assert _task(a).checked_out_by is None


def test_rollback_does_not_drop_a_lease_taken_earlier(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    """Идемпотентный checkout в откатившейся транзакции чужую ему (прежнюю) аренду не снимает."""
    a, _b, store = lab
    _checkout(a, AGENT_A)
    with transactional(a.sf, commit=False) as s:
        checkout_service.checkout(s, TASK, project_id=a.project_id, agent=AGENT_A)
    assert _lease(a, store) is not None


# --- снятие: release, complete, release_stale ------------------------------------------


def test_release_drops_the_lease_after_commit(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    a, b, store = lab
    _checkout(a, AGENT_A)
    with transactional(a.sf, commit=False) as s:  # откат — аренда остаётся
        checkout_service.release(s, TASK, project_id=a.project_id, agent=AGENT_A)
    assert _lease(a, store) is not None
    _release(a, AGENT_A)
    assert _lease(a, store) is None
    assert _events(a, "task.checkout_lease_released") == [{"remote": "origin", "offline": False}]
    assert _checkout(b, AGENT_B).lease == "leased"  # теперь свободна


def test_complete_drops_the_lease(lab: tuple[r.Replica, r.Replica, FileRemote]) -> None:
    a, _b, store = lab
    _checkout(a, AGENT_A)
    with transactional(a.sf) as s:
        task_service.complete(
            s, project_id=a.project_id, task_id=TASK, author=AGENT_A, commit_sha="abc1234"
        )
    assert _lease(a, store) is None


def _age_lock(rep: r.Replica, minutes: int) -> None:
    with transactional(rep.sf) as s:
        row = s.scalars(select(TaskModel).where(TaskModel.task_id == TASK)).one()
        row.checked_out_at = datetime.now(UTC) - timedelta(minutes=minutes)


def _put_foreign_lease(rep: r.Replica, store: FileRemote, *, minutes_ago: int) -> None:
    project_uid, task_uid = _uids(rep)
    holder = checkout_lease.Holder(
        task_id=TASK,
        agent="agent:ghost",
        replica="0199f2b0-7c3a-7000-8000-00000000dead",
        acquired_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
    )
    checkout_lease.take_over(store, project_uid, task_uid, holder)


def test_release_stale_drops_own_lease(lab: tuple[r.Replica, r.Replica, FileRemote]) -> None:
    a, _b, store = lab
    _checkout(a, AGENT_A)
    _age_lock(a, 60)
    with transactional(a.sf) as s:
        assert checkout_service.release_stale(s, ttl_minutes=30, project_id=a.project_id) == [TASK]
    assert _lease(a, store) is None


@pytest.mark.parametrize(("minutes_ago", "dropped"), [(60, True), (5, False)])
def test_release_stale_drops_foreign_lease_only_past_its_ttl(
    lab: tuple[r.Replica, r.Replica, FileRemote], minutes_ago: int, dropped: bool
) -> None:
    """Замок чужой реплики приехал pull'ом и протух; её аренда снимается только по её TTL."""
    a, b, store = lab
    _checkout(b, AGENT_B)
    _sync(b, store)
    _sync(a, store)
    _put_foreign_lease(a, store, minutes_ago=minutes_ago)
    _age_lock(a, 60)
    with transactional(a.sf) as s:
        assert checkout_service.release_stale(s, ttl_minutes=30, project_id=a.project_id) == [TASK]
    assert (_lease(a, store) is None) is dropped


def test_release_keeps_another_replicas_lease(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    """Снять замок, приехавший pull'ом, — не снять аренду той машины, что работает."""
    a, b, store = lab
    _checkout(b, AGENT_B)
    _sync(b, store)
    _sync(a, store)
    _release(a, AGENT_B)
    holder = _lease(a, store)
    assert holder is not None
    assert holder.replica == _replica(b)


# --- офлайн: провизорный замок ---------------------------------------------------------


def test_offline_checkout_is_provisional_and_push_leases_it(
    lab: tuple[r.Replica, r.Replica, FileRemote], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _b, store = lab
    monkeypatch.setattr(checkout_lease, "open_store", lambda *_: pytest.fail("сеть при --offline"))
    taken = _checkout(a, AGENT_A, offline=True)
    assert (taken.new_status, taken.lease) == ("in_progress", "offline")
    assert _markers(a) == [_uids(a)[1]]
    assert _lease(a, store) is None
    assert _events(a, "task.checkout_leased") == [{"remote": "origin", "offline": True}]

    push.push(a.sf, r.SLUG, author=r.AUTHOR, store=store)
    holder = _lease(a, store)
    assert holder is not None
    assert (holder.agent, holder.replica) == (AGENT_A, _replica(a))
    assert _markers(a) == []


def test_offline_recheckout_online_leases_now(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    a, _b, store = lab
    _checkout(a, AGENT_A, offline=True)
    again = _checkout(a, AGENT_A)
    assert (again.idempotent, again.lease) == (True, "leased")
    assert _lease(a, store) is not None
    assert _markers(a) == []


def test_offline_lock_against_a_held_lease_stays_provisional_until_merge(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    """B держит аренду, A взяла задачу офлайн: push A не крадёт аренду, слияние ловит захват."""
    a, b, store = lab
    _checkout(b, AGENT_B)  # онлайн, аренда у B; операция ещё не уехала
    _checkout(a, AGENT_A, offline=True)
    _sync(a, store)
    assert _markers(a) == [_uids(a)[1]]  # аренда занята — отметка остаётся
    holder = _lease(a, store)
    assert holder is not None
    assert holder.replica == _replica(b)
    merged = pull.pull(b.sf, r.SLUG, author=r.AUTHOR, store=store)
    assert len(merged.conflicts) == 1
    with transactional(b.sf, commit=False) as s:
        (row,) = conflicts.list_conflicts(s, b.project_id)
    assert row["kind"] == "double_checkout"


# --- разрешение double_checkout --------------------------------------------------------


def _double_offline(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> tuple[str, tuple[object, object]]:
    """Обе реплики взяли задачу офлайн (A раньше); A синхронизировалась первой.

    → (uid конфликта на B, замок B до обмена). Цепочка замка (CLO-044) держит
    захват A на обеих репликах, захват B отвергнут; провизорный замок B
    протух (в БД B больше не её замок) и снимается сверкой без сети.
    """
    a, b, store = lab
    _checkout(a, AGENT_A, offline=True)
    _checkout(b, AGENT_B, offline=True)
    mine = _task(b)
    _sync(a, store)  # аренда свободна — A её получает
    _sync(b, store)  # B сливает захват A со своим: конфликт
    with transactional(b.sf, commit=False) as s:
        (row,) = conflicts.list_conflicts(s, b.project_id)
    assert row["kind"] == "double_checkout"
    assert _task(b).checked_out_by == AGENT_A
    assert _markers(b) == []
    holder = _lease(b, store)
    assert holder is not None
    assert (holder.agent, holder.replica) == (AGENT_A, _replica(a))
    return str(row["uid"]), (mine.checked_out_by, mine.checked_out_at)


def _resolve(rep: r.Replica, uid: str, take: str) -> dict[str, object]:
    with transactional(rep.sf) as s:
        return conflicts.resolve_conflict(s, rep.project_id, uid, take=take, author=r.AUTHOR)


def _open(rep: r.Replica) -> list[dict[str, object]]:
    with transactional(rep.sf, commit=False) as s:
        return conflicts.list_conflicts(s, rep.project_id)


def test_double_checkout_take_theirs_moves_lock_and_lease_to_the_rejected_capture(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    """``--take theirs`` — отвергнутый захват B: замок A снимается у всех, аренда — у B."""
    a, b, store = lab
    uid, b_lock = _double_offline(lab)
    resolved = _resolve(b, uid, "theirs")
    assert resolved["status"] == "resolved"
    assert resolved["revision_id"] is not None
    on_b = _task(b)
    assert (on_b.checked_out_by, on_b.checked_out_at) == b_lock
    holder = _lease(b, store)
    assert holder is not None
    assert (holder.agent, holder.replica) == (AGENT_B, _replica(b))
    _sync(b, store)
    _sync(a, store)
    on_a = _task(a)
    assert (on_a.checked_out_by, on_a.checked_out_at) == b_lock
    assert _open(a) == _open(b) == []  # маркер разрешения закрыл конфликт и на A
    assert r.authored_state(a) == r.authored_state(b)
    with pytest.raises(CheckoutConflictError):  # замок проигравшего снят — держит B
        _checkout(a, AGENT_A)


def test_double_checkout_take_ours_keeps_the_chain_holder_and_its_lease(
    lab: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    """``--take ours`` — замок цепочки (A): реплика держателя — по его операции, аренда — у A."""
    a, b, store = lab
    uid, _b_lock = _double_offline(lab)
    on_a = _task(a)
    _resolve(b, uid, "ours")
    holder = _lease(b, store)
    assert holder is not None
    assert (holder.agent, holder.replica) == (AGENT_A, _replica(a))
    _sync(b, store)
    _sync(a, store)
    for rep in (a, b):
        got = _task(rep)
        assert (got.checked_out_by, got.checked_out_at) == (
            on_a.checked_out_by,
            on_a.checked_out_at,
        )
    assert _open(a) == _open(b) == []
    assert r.authored_state(a) == r.authored_state(b)
    holder = _lease(a, store)  # после обмена аренда по-прежнему у A
    assert holder is not None
    assert (holder.agent, holder.replica) == (AGENT_A, _replica(a))


# --- поверхности ---------------------------------------------------------------------


def test_offline_is_on_both_surfaces() -> None:
    """§10.7/§10.8: ``task_checkout(offline=…)`` в MCP и ``task checkout --offline`` в CLI."""
    from mcp.server.fastmcp import FastMCP

    from cod_doc.cli.task import task_checkout
    from cod_doc.mcp.tools import checkout_tools

    params = {p.name: p for p in task_checkout.params}
    assert params["offline"].opts == ["--offline"]
    assert getattr(params["offline"], "is_flag", False)
    server = FastMCP("clo-031")
    checkout_tools.register(server)
    tool = server._tool_manager._tools["task_checkout"]
    signature = inspect.signature(tool.fn)
    assert signature.parameters["offline"].default is False
    assert "SYNC-108" in (tool.fn.__doc__ or "")
    assert "SYNC-109" in (tool.fn.__doc__ or "")
