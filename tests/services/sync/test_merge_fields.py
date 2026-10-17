"""CLO-018: LWW по полю, поглощение равных, порядок по rank (RFC 29 §3.7).

Acceptance: «Правки разных полей одной сущности сливаются без конфликта,
одного поля — по большему HLC; равные значения не дают операции; вставки по
rank не трогают соседей; тесты на SCN-081 и SCN-093.»

Главный инвариант — детерминизм: две реплики, применившие одно множество
changeset'ов в любом порядке, приходят к одному дайджесту. Его проверяет
property-тест в конце: одни и те же офлайн-правки сводятся в двух
«вселенных» с разным порядком push/pull, и все четыре дайджеста совпадают.

Реплики — две SQLite в tmp, remote — ``FileRemote`` в tmp. Время правок
разводится паузой в несколько миллисекунд: HLC берёт стенные часы.
"""

from __future__ import annotations

import random
import shutil
import sqlite3
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from cod_doc.domain.entities import Priority, ScenarioKind
from cod_doc.infra import sync_capture as cap
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    ScenarioModel,
    SyncConflictModel,
    SyncOpModel,
    TaskModel,
)
from cod_doc.infra.remote.file import FileRemote
from cod_doc.services import checkout_service, scenario_service, task_service
from cod_doc.services.sync_service import conflicts, digest, merge, pull, push
from cod_doc.services.sync_service.changeset import Op
from cod_doc.services.sync_service.errors import MergeRequiredError
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

TASK = "DEM-001"
_TICK = 0.003  # больше миллисекунды HLC


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    return a, r.replica_b(tmp_path, a), r.remote(tmp_path)


def _push(rep: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _common_task(a: r.Replica, b: r.Replica, store: FileRemote) -> None:
    r.make_task(a, TASK)
    _push(a, store)
    _pull(b, store)


def _set(rep: r.Replica, task_id: str = TASK, **values: object) -> None:
    """Правка полей задачи через фабрику с захватом (сервиса правки title нет)."""
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        row = s.scalars(select(TaskModel).where(TaskModel.task_id == task_id)).one()
        for name, value in values.items():
            setattr(row, name, value)


def _priority(rep: r.Replica, value: Priority, task_id: str = TASK) -> None:
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        task_service.update_priority(
            s, project_id=rep.project_id, task_id=task_id, new_priority=value, author=r.AUTHOR
        )


def _task(rep: r.Replica, task_id: str = TASK) -> TaskModel:
    with transactional(rep.sf) as s:
        row = s.scalars(select(TaskModel).where(TaskModel.task_id == task_id)).one()
        s.expunge(row)
        return row


def _digest(rep: r.Replica) -> str:
    with transactional(rep.sf, commit=False) as s:
        return digest.compute(s, rep.project_id)


def _conflicts(rep: r.Replica) -> int:
    with transactional(rep.sf) as s:
        return int(s.scalar(select(func.count()).select_from(SyncConflictModel)) or 0)


def _pending_fields(rep: r.Replica, entity_kind: str = "task") -> list[dict[str, object]]:
    import json

    with transactional(rep.sf) as s:
        rows = s.scalars(
            select(SyncOpModel)
            .where(SyncOpModel.changeset.is_(None), SyncOpModel.entity_kind == entity_kind)
            .order_by(SyncOpModel.hlc)
        )
        return [json.loads(row.fields) for row in rows]


def _round_trip(first: r.Replica, second: r.Replica, store: FileRemote) -> pull.PullResult:
    """first push → second pull + push → first pull; итог — pull second'а."""
    _push(first, store)
    merged = _pull(second, store)
    _push(second, store)
    _pull(first, store)
    return merged


def _assert_converged(a: r.Replica, b: r.Replica, store: FileRemote) -> None:
    assert r.pending(a) == 0
    assert r.pending(b) == 0
    assert r.authored_state(a) == r.authored_state(b)
    assert _digest(a) == _digest(b)
    for rep in (a, b):
        assert digest.verify(rep.sf, r.SLUG, store=store).equal


# --- разные поля --------------------------------------------------------------------


def test_edits_of_different_fields_merge_without_conflict(pair: tuple) -> None:
    a, b, store = pair
    _common_task(a, b, store)
    _set(a, title="Новый заголовок", normalized_title="новый заголовок")
    _priority(b, Priority.HIGH)

    merged = _round_trip(a, b, store)

    assert merged.fast_forward is False  # хвост B пересёкся с changeset'ом A
    assert merged.conflicts == []
    for rep in (a, b):
        task = _task(rep)
        assert (task.title, task.priority) == ("Новый заголовок", Priority.HIGH.value)
    assert _conflicts(a) == _conflicts(b) == 0
    _assert_converged(a, b, store)


def test_scn_081_title_and_checkout_on_two_offline_replicas(pair: tuple) -> None:
    """SCN-081: title на A, task_checkout на B — оба изменения на обеих, конфликта нет."""
    a, b, store = pair
    _common_task(a, b, store)
    _set(a, title="Title from A", normalized_title="title from a")  # шаг 1
    time.sleep(_TICK)
    with transactional(b.sf) as s:  # шаг 2
        checkout_service.checkout(s, TASK, project_id=b.project_id, agent=r.AUTHOR)

    _push(a, store)  # шаг 3
    pulled = _pull(b, store)  # шаг 4
    _push(b, store)
    _pull(a, store)  # шаг 5

    assert pulled.conflicts == []
    for rep in (a, b):
        task = _task(rep)
        assert task.title == "Title from A"
        assert task.status == "in_progress"
        assert task.checked_out_by == r.AUTHOR
    assert _conflicts(a) == _conflicts(b) == 0
    _assert_converged(a, b, store)


# --- одно поле ----------------------------------------------------------------------


@pytest.mark.parametrize("later", ["a", "b"])
def test_same_field_goes_to_the_larger_hlc_on_both_replicas(pair: tuple, later: str) -> None:
    """Победитель — больший штамп, кто бы ни сливал: у B проигрывает то своё, то чужое."""
    a, b, store = pair
    _common_task(a, b, store)
    first, second = (b, a) if later == "a" else (a, b)
    _set(first, title="раньше")
    _set(second, title="позже")

    _round_trip(a, b, store)  # сливает B: при later="a" его правка проигрывает

    assert _task(a).title == _task(b).title == "позже"
    assert _conflicts(a) == _conflicts(b) == 0
    _assert_converged(a, b, store)


def test_losing_field_leaves_the_tail_and_does_not_ship(pair: tuple) -> None:
    a, b, store = pair
    _common_task(a, b, store)
    _set(b, title="B, раньше")
    _set(a, title="A, позже")
    _push(a, store)

    _pull(b, store)

    assert _task(b).title == "A, позже"
    assert all("title" not in fields for fields in _pending_fields(b))


def test_equal_values_produce_no_operation(pair: tuple) -> None:
    """Одно значение с обеих сторон поглощается: в хвосте B поля нет, конфликта нет."""
    a, b, store = pair
    _common_task(a, b, store)
    _set(a, title="одинаково")
    _set(b, title="одинаково")
    assert any("title" in fields for fields in _pending_fields(b))
    _push(a, store)

    merged = _pull(b, store)

    assert merged.conflicts == []
    assert all("title" not in fields for fields in _pending_fields(b))
    _push(b, store)
    _pull(a, store)
    _assert_converged(a, b, store)


def test_tie_on_time_is_broken_by_replica_uid() -> None:
    """Одинаковые ms и счётчик: побеждает больший uid реплики — одинаково везде."""
    lo = ("1760000000000.0000.0192aaaa", "01J0000000000000000000000Z")
    hi = ("1760000000000.0000.0192bbbb", "01J00000000000000000000000")
    clash = merge.FieldClash(
        table="task",
        entity_uid="t",
        column="title",
        ours="ours",
        theirs="theirs",
        ours_base=None,
        theirs_base=None,
        ours_stamp=lo,
        theirs_stamp=hi,
        theirs_op=hi[1],
    )
    assert merge.lww(clash).outcome is merge.Outcome.THEIRS
    swapped = replace(clash, ours_stamp=hi, theirs_stamp=lo)
    assert merge.lww(swapped).outcome is merge.Outcome.OURS


# --- порядок по rank ----------------------------------------------------------------


def _scenario(a: r.Replica, b: r.Replica, store: FileRemote) -> str:
    with transactional(a.sf) as s:
        scn = scenario_service.create(
            s,
            project_id=a.project_id,
            title="Пять шагов",
            kind=ScenarioKind.HAPPY_PATH,
            preconditions="p",
            expected="e",
            author=r.AUTHOR,
            steps=["1", "2", "3", "4", "5"],
            group_key="remote-sync",
        )
    _push(a, store)
    _pull(b, store)
    return scn.scenario_id


def _steps(rep: r.Replica, scenario_id: str) -> list[str]:
    with transactional(rep.sf) as s:
        row = s.scalars(select(ScenarioModel).where(ScenarioModel.scenario_id == scenario_id)).one()
        return [step.text for step in scenario_service.list_steps(s, row.row_id)]


def _add_step(rep: r.Replica, scenario_id: str, text: str, position: int | None) -> None:
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        scenario_service.add_step(
            s,
            project_id=rep.project_id,
            scenario_id=scenario_id,
            text=text,
            author=r.AUTHOR,
            position=position,
        )


def _step_ops(rep: r.Replica, after: set[str]) -> list[SyncOpModel]:
    with transactional(rep.sf) as s:
        rows = s.scalars(select(SyncOpModel).where(SyncOpModel.entity_kind == "scenario_step"))
        found = [row for row in rows if row.op_id not in after]
        for row in found:
            s.expunge(row)
        return found


def _op_ids(rep: r.Replica) -> set[str]:
    with transactional(rep.sf) as s:
        return set(s.scalars(select(SyncOpModel.op_id)))


def test_scn_093_concurrent_step_inserts_merge_by_rank(pair: tuple) -> None:
    """SCN-093: шаг A между вторым и третьим, шаг B в конец — семь шагов на обеих."""
    a, b, store = pair
    scn = _scenario(a, b, store)
    baseline = {rep.name: _op_ids(rep) for rep in (a, b)}
    _add_step(a, scn, "шаг A", 2)
    _add_step(b, scn, "шаг B", None)

    merged = _round_trip(a, b, store)

    expected = ["1", "2", "шаг A", "3", "4", "5", "шаг B"]
    assert _steps(a, scn) == _steps(b, scn) == expected
    assert merged.conflicts == []
    for rep in (a, b):
        ops = _step_ops(rep, baseline[rep.name])
        # По одной вставке с каждой реплики, ни одной правки rank старых шагов.
        assert len(ops) == 2
        assert all(op.base == "{}" for op in ops)
    _assert_converged(a, b, store)


def test_inserts_between_the_same_neighbours_order_identically(pair: tuple) -> None:
    """Обе реплики вставили между вторым и третьим: равный rank решает uid."""
    a, b, store = pair
    scn = _scenario(a, b, store)
    _add_step(a, scn, "от A", 2)
    _add_step(b, scn, "от B", 2)

    _round_trip(a, b, store)

    order = _steps(a, scn)
    assert order == _steps(b, scn)
    assert order[:2] == ["1", "2"]
    assert order[-3:] == ["3", "4", "5"]
    assert sorted(order[2:4]) == ["от A", "от B"]
    _assert_converged(a, b, store)


# --- границы следующих задач ----------------------------------------------------------


def test_concurrent_text_edits_wait_for_clo_019(pair: tuple) -> None:
    a, b, store = pair
    _common_task(a, b, store)
    r.set_description(a, TASK, "A")
    _push(a, store)
    r.set_description(b, TASK, "B")
    before = r.authored_state(b)

    with pytest.raises(MergeRequiredError, match="CLO-019"):
        _pull(b, store)

    assert r.authored_state(b) == before


def test_status_changed_on_both_sides_waits_for_clo_020(pair: tuple) -> None:
    a, b, store = pair
    _common_task(a, b, store)
    _set(a, status="blocked")
    _push(a, store)
    _set(b, status="cancelled")
    before = r.authored_state(b)

    with pytest.raises(MergeRequiredError, match="CLO-020"):
        _pull(b, store)

    assert r.authored_state(b) == before


def test_delete_against_edit_is_won_by_the_delete(pair: tuple) -> None:
    """Удаление против правки — стратегия CLO-022: удаление выигрывает, правка — в конфликт."""
    a, b, store = pair
    _common_task(a, b, store)
    with transactional(a.sf) as s:
        cap.set_author(s, r.AUTHOR)
        s.delete(s.scalars(select(TaskModel).where(TaskModel.task_id == TASK)).one())
    _push(a, store)
    _set(b, title="правка под удалением")

    pulled = _pull(b, store)

    assert len(pulled.conflicts) == 1
    assert r.task_description(b, TASK) is None  # задачи нет
    _push(b, store)
    _pull(a, store)
    _assert_converged(a, b, store)


def test_edge_delete_against_its_own_upsert_waits_for_clo_021() -> None:
    def op(kind: str, fields: dict[str, object], base: dict[str, object]) -> Op:
        return Op(
            op_id="01J0000000000000000000000" + kind[0].upper(),
            hlc="1760000000000.0000.r",
            entity_kind="dependency",
            entity_uid="edge",
            kind=kind,
            fields=fields,  # type: ignore[arg-type]
            base=base,  # type: ignore[arg-type]
            author=r.AUTHOR,
        )

    ends = {"from_task_id": "t1", "to_task_id": "t2", "kind": "blocks"}
    with pytest.raises(MergeRequiredError, match="CLO-021"):
        merge.check([op("delete", {}, ends)], [op("upsert", ends, {})])


def test_natural_key_twin_waits_for_clo_023_instead_of_integrity_error(pair: tuple) -> None:
    """Один doc_key, созданный на обеих репликах, — склейка (F2 секции B), а не IntegrityError."""
    a, b, store = pair
    r.make_doc(a, "guide/same", {"intro": "A"})
    _push(a, store)
    r.make_doc(b, "guide/same", {"intro": "B"})

    with pytest.raises(MergeRequiredError, match="CLO-023"):
        _pull(b, store)


# --- точки расширения ---------------------------------------------------------------


def _text_plugin(monkeypatch: pytest.MonkeyPatch, resolve: object) -> None:
    real = merge._plugin

    def fake(module: str) -> object:
        return SimpleNamespace(resolve=resolve) if module == "text_merge" else real(module)

    monkeypatch.setattr(merge, "_plugin", fake)


def test_merged_value_from_a_strategy_ships_with_a_fresh_stamp(
    pair: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Стратегия вернула сведённое значение: оно в БД и уезжает новой операцией."""
    a, b, store = pair
    _common_task(a, b, store)
    _text_plugin(
        monkeypatch,
        lambda c: merge.Resolution(merge.Outcome.MERGED, value=f"{c.ours}+{c.theirs}"),
    )
    r.set_description(a, TASK, "A")
    time.sleep(_TICK)
    r.set_description(b, TASK, "B")

    _round_trip(a, b, store)

    assert r.task_description(a, TASK) == r.task_description(b, TASK) == "B+A"
    _assert_converged(a, b, store)


def test_conflict_from_a_strategy_keeps_ours_and_opens_a_row(
    pair: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, store = pair
    _common_task(a, b, store)
    _text_plugin(monkeypatch, lambda c: merge.Resolution(merge.Outcome.CONFLICT))
    r.set_description(a, TASK, "A")
    _push(a, store)
    time.sleep(_TICK)
    r.set_description(b, TASK, "B")

    pulled = _pull(b, store)

    assert len(pulled.conflicts) == 1
    assert r.task_description(b, TASK) == "B"  # в БД остаётся «наше»
    with transactional(b.sf) as s:
        (row,) = conflicts.list_conflicts(s, b.project_id)
        assert (row["kind"], row["field"], row["ours"], row["theirs"]) == (
            "text",
            "description",
            "B",
            "A",
        )
        event = s.scalars(
            select(ActivityEventModel)
            .where(ActivityEventModel.kind == "sync.pulled")
            .order_by(ActivityEventModel.row_id.desc())
            .limit(1)
        ).one()
        assert event.payload["conflicts"] == pulled.conflicts
        assert event.payload["fast_forward"] is False
        conflicts.resolve_conflict(
            s, b.project_id, pulled.conflicts[0], take="theirs", author=r.AUTHOR
        )
    _push(b, store)
    _pull(a, store)
    assert r.task_description(a, TASK) == r.task_description(b, TASK) == "A"
    _assert_converged(a, b, store)


# --- детерминизм: два порядка, один дайджест ------------------------------------------

_TITLES = ("альфа", "бета", "гамма", "дельта")
_PRIORITIES = (Priority.LOW, Priority.MEDIUM, Priority.HIGH)
_TASKS = ("DEM-001", "DEM-002")


def _random_edits(rng: random.Random, rep: r.Replica) -> None:
    for _ in range(rng.randint(0, 3)):
        task_id = rng.choice(_TASKS)
        if rng.random() < 0.5:
            title = rng.choice(_TITLES)
            _set(rep, task_id, title=title, normalized_title=title)
        else:
            _priority(rep, rng.choice(_PRIORITIES), task_id)


def _copy_universe(tmp: Path, a: r.Replica, b: r.Replica, src: FileRemote) -> tuple:
    """Та же картина в другом каталоге: копии обеих БД и remote."""
    tmp.mkdir()
    copies = []
    for rep in (a, b):
        path = tmp / rep.db_path.name
        with sqlite3.connect(rep.db_path) as from_, sqlite3.connect(path) as to:
            from_.backup(to)
        engine = make_engine(f"sqlite:///{path}")
        copies.append(replace(rep, sf=make_session_factory(engine), engine=engine, db_path=path))
    shutil.copytree(src.root, tmp / "remote")
    return copies[0], copies[1], FileRemote(tmp / "remote")


@pytest.mark.parametrize("seed", range(6))
def test_two_replicas_converge_to_one_digest_in_any_order(tmp_path: Path, seed: int) -> None:
    """Одни офлайн-правки, два порядка синхронизации — один дайджест у всех четырёх."""
    rng = random.Random(seed)
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    store = r.remote(tmp_path)
    for task_id in _TASKS:
        r.make_task(a, task_id)
    _push(a, store)
    _pull(b, store)
    for step in range(3):
        _random_edits(rng, a)
        _random_edits(rng, b)
        a2, b2, store2 = _copy_universe(tmp_path / f"u{step}", a, b, store)
        _round_trip(a, b, store)  # A первым
        _round_trip(b2, a2, store2)  # B первым
        digests = {_digest(rep) for rep in (a, b, a2, b2)}
        assert len(digests) == 1, f"seed {seed}, раунд {step}: дайджесты разошлись"
        assert r.authored_state(a) == r.authored_state(b2)
        a2.engine.dispose()
        b2.engine.dispose()
