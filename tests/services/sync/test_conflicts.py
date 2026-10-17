"""CLO-017: таблица ``sync_conflict`` и сервис конфликтов (RFC 29 §3.7, §10.6).

Acceptance: «Миграция 0051_sync_conflict (схема RFC 29 §3.7 + колонка kind из
§10.1); list_conflicts и resolve_conflict по §10.6 пишут ревизию и событие
sync.conflict_resolved одной транзакцией; SYNC-301 на неподходящий take,
SYNC-302 на неизвестный/разрешённый.»

Разрешение — обычная запись (§3.7): выбранное значение ложится в сущность
через ``Session``, захват пишет новую операцию, и она уезжает следующим push'ем.
Сама таблица конфликтов local — операций не даёт.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    RevisionModel,
    SyncConflictModel,
    SyncOpModel,
    TaskModel,
)
from cod_doc.services import activity_service
from cod_doc.services.sync_service import conflicts
from cod_doc.services.sync_service.errors import (
    ConflictNotFoundError,
    ConflictResolveError,
    MergeRequiredError,
)
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

TASK = "DEM-001"


@pytest.fixture
def rep(tmp_path: Path) -> r.Replica:
    a = r.replica_a(tmp_path)
    r.make_task(a, TASK, description="общий\nтекст\n")
    return a


def _task(rep: r.Replica) -> TaskModel:
    with transactional(rep.sf) as s:
        row = s.scalars(select(TaskModel).where(TaskModel.task_id == TASK)).one()
        s.expunge(row)
        return row


def _open(
    rep: r.Replica, *, kind: str = "field", field: str | None = "title", **values: object
) -> str:
    task = _task(rep)
    spec = conflicts.ConflictSpec(
        entity_kind="task",
        entity_uid=str(task.uid),
        field=field,
        kind=kind,
        ours=values.get("ours", task.title),
        theirs=values.get("theirs", "Заголовок B"),
        base=values.get("base", "Task DEM-001"),
        theirs_op="01JTHEIRSOP0000000000000000",
    )
    with transactional(rep.sf) as s:
        return conflicts.open_conflict(s, rep.project_id, spec, author="system:sync")


def _pending_ops(rep: r.Replica) -> list[dict[str, object]]:
    with transactional(rep.sf) as s:
        rows = s.scalars(
            select(SyncOpModel).where(SyncOpModel.changeset.is_(None)).order_by(SyncOpModel.hlc)
        )
        return [{"kind": o.entity_kind, "fields": json.loads(o.fields)} for o in rows]


def _task_ops(rep: r.Replica) -> list[dict[str, object]]:
    """Хвост без ревизий: разрешение пишет и ревизию, она уезжает тоже (CLO-024)."""
    return [op for op in _pending_ops(rep) if op["kind"] != "revision"]


def _count(rep: r.Replica, model: type, *where: object) -> int:
    with transactional(rep.sf) as s:
        return int(s.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def _resolve(rep: r.Replica, uid: str, take: str, **kw: object) -> dict[str, object]:
    with transactional(rep.sf) as s:
        return conflicts.resolve_conflict(
            s,
            rep.project_id,
            uid,
            take=take,
            author="human:dakh",
            **kw,  # type: ignore[arg-type]
        )


# --- таблица -----------------------------------------------------------------------


def test_sync_conflict_is_local_and_opening_writes_no_operation(rep: r.Replica) -> None:
    assert reg.spec_for("sync_conflict").cls is reg.SyncClass.LOCAL
    before = _pending_ops(rep)

    uid = _open(rep)

    assert _pending_ops(rep) == before
    assert _count(rep, ActivityEventModel, ActivityEventModel.kind == "sync.conflict_opened") == 1
    with transactional(rep.sf) as s:
        row = s.get_one(SyncConflictModel, uid)
        assert (row.kind, row.status, row.field) == ("field", "open", "title")


def test_list_conflicts_filters_by_status_and_decodes_values(rep: r.Replica) -> None:
    first = _open(rep, theirs="B1")
    second = _open(rep, kind="status", field="status", ours="todo", theirs="done", base="todo")
    _resolve(rep, first, "theirs")

    with transactional(rep.sf) as s:
        open_ = conflicts.list_conflicts(s, rep.project_id)
        resolved = conflicts.list_conflicts(s, rep.project_id, status="resolved")
        everything = conflicts.list_conflicts(s, rep.project_id, status="all")

    assert [c["uid"] for c in open_] == [second]
    assert open_[0]["theirs"] == "done"
    assert open_[0]["kind"] == "status"
    assert [c["uid"] for c in resolved] == [first]
    assert resolved[0]["resolved_by"] == "human:dakh"
    assert [c["uid"] for c in everything] == [first, second]


# --- разрешение -----------------------------------------------------------------------


def test_resolve_theirs_writes_value_operation_revision_and_event(rep: r.Replica) -> None:
    uid = _open(rep, theirs="Заголовок B")
    task = _task(rep)
    revisions = _count(rep, RevisionModel, RevisionModel.entity_id == task.row_id)

    out = _resolve(rep, uid, "theirs", reason="берём B")

    assert out["status"] == "resolved"
    assert _task(rep).title == "Заголовок B"
    # Ревизия разрешения тоже уезжает (CLO-024); значение — последняя операция задачи.
    last = [op for op in _pending_ops(rep) if op["kind"] != "revision"][-1]
    assert last["kind"] == "task"
    assert last["fields"]["title"] == "Заголовок B"  # type: ignore[index]
    assert _count(rep, RevisionModel, RevisionModel.entity_id == task.row_id) == revisions + 1
    with transactional(rep.sf) as s:
        event = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "sync.conflict_resolved")
        ).one()
        assert event.scope_kind == "task"
        assert event.actor_id == "human:dakh"
        assert event.payload == {"conflict_uid": uid, "kind": "field", "take": "theirs"}
        row = s.get_one(SyncConflictModel, uid)
        assert (row.status, row.resolved_by) == ("resolved", "human:dakh")
        assert row.resolved_at is not None


def test_resolve_ours_ships_ours_even_when_the_db_already_holds_it(rep: r.Replica) -> None:
    """«Наше» уже в БД, но его штамп мог проиграть — разрешение пишет новую операцию."""
    uid = _open(rep, theirs="Заголовок B")
    before = len(_task_ops(rep))

    _resolve(rep, uid, "ours")

    ops = _task_ops(rep)
    assert len(ops) == before + 1
    assert ops[-1]["fields"] == {"title": "Task DEM-001"}
    assert _task(rep).title == "Task DEM-001"


def test_custom_text_resolves_a_text_conflict(rep: r.Replica) -> None:
    uid = _open(rep, kind="text", field="description", ours="наше\n", theirs="их\n", base="общий\n")

    _resolve(rep, uid, "сведено руками\n")

    assert _task(rep).description == "сведено руками\n"
    with transactional(rep.sf) as s:
        event = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "sync.conflict_resolved")
        ).one()
    assert event.payload["take"] == "custom"


def test_resolution_is_one_transaction(rep: r.Replica, monkeypatch: pytest.MonkeyPatch) -> None:
    """Событие не записалось — нет ни значения, ни операции, ни ревизии, ни отметки."""
    uid = _open(rep, theirs="Заголовок B")
    task = _task(rep)
    ops = _pending_ops(rep)
    revisions = _count(rep, RevisionModel, RevisionModel.entity_id == task.row_id)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("событие не записалось")

    monkeypatch.setattr(activity_service, "emit_for_write", boom)
    with pytest.raises(RuntimeError, match="событие"):
        _resolve(rep, uid, "theirs")

    assert _task(rep).title == "Task DEM-001"
    assert _pending_ops(rep) == ops
    assert _count(rep, RevisionModel, RevisionModel.entity_id == task.row_id) == revisions
    with transactional(rep.sf) as s:
        assert s.get_one(SyncConflictModel, uid).status == "open"


# --- коды ошибок ------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "field", "take"), [
    ("field", "title", "свой текст"),
    ("status", "status", "blocked"),
    ("delete_edit", None, "свой текст"),
])  # fmt: skip
def test_take_that_does_not_fit_the_kind_is_sync_301(
    rep: r.Replica, kind: str, field: str | None, take: str
) -> None:
    uid = _open(rep, kind=kind, field=field)
    ops = _pending_ops(rep)

    with pytest.raises(ConflictResolveError) as err:
        _resolve(rep, uid, take)

    assert err.value.code == "SYNC-301"
    assert _pending_ops(rep) == ops
    with transactional(rep.sf) as s:
        assert s.get_one(SyncConflictModel, uid).status == "open"


def test_unknown_or_resolved_conflict_is_sync_302(rep: r.Replica) -> None:
    with pytest.raises(ConflictNotFoundError) as unknown:
        _resolve(rep, "0192-нет-такого", "ours")
    assert unknown.value.code == "SYNC-302"

    uid = _open(rep)
    _resolve(rep, uid, "theirs")
    with pytest.raises(ConflictNotFoundError) as twice:
        _resolve(rep, uid, "ours")
    assert twice.value.code == "SYNC-302"


def test_conflict_of_another_project_is_sync_302(rep: r.Replica) -> None:
    uid = _open(rep)
    with pytest.raises(ConflictNotFoundError), transactional(rep.sf) as s:
        conflicts.resolve_conflict(s, rep.project_id + 1, uid, take="ours", author="human:x")
    with transactional(rep.sf) as s:
        assert conflicts.list_conflicts(s, rep.project_id + 1) == []


def test_double_checkout_waits_for_clo_031(rep: r.Replica) -> None:
    """Двойной офлайн-checkout — механика CLO-031; до неё — граница до записи."""
    uid = _open(rep, kind="double_checkout", field=None, ours=None, theirs=None)
    with pytest.raises(MergeRequiredError, match="CLO-031"):
        _resolve(rep, uid, "theirs")
    with transactional(rep.sf) as s:
        assert s.get_one(SyncConflictModel, uid).status == "open"


def test_unknown_status_filter_is_rejected(rep: r.Replica) -> None:
    with transactional(rep.sf) as s, pytest.raises(ValueError, match="status"):
        conflicts.list_conflicts(s, rep.project_id, status="closed")
