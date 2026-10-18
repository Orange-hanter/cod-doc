"""CLO-020: слияние статуса задачи через статус-машину (RFC 29 §3.7, Q-011).

Acceptance: «При правке status на двух репликах побеждает больший HLC только
при допустимом переходе по ALLOWED_TRANSITIONS; недопустимая пара и двойной
офлайн-checkout → конфликт (status / double_checkout).»

Каждый сценарий сводится в двух порядках push/pull (``_two_orders``).
Допустимая пара — одно значение у всех четырёх реплик; недопустимая —
один и тот же конфликт у сливающей реплики при любом порядке, после
разрешения одним значением обе вселенные сходятся.
"""

from __future__ import annotations

import itertools
import random
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.infra import sync_capture as cap
from cod_doc.infra.db import transactional
from cod_doc.infra.models import TaskModel
from cod_doc.services import checkout_service
from cod_doc.services.sync_service import conflicts, merge, status_merge
from cod_doc.services.task_status_machine import ALLOWED_TRANSITIONS
from tests.services.sync import _replicas as r
from tests.services.sync import _two_orders as t

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

TASK = "DEM-001"


def _setup(a: r.Replica, _b: r.Replica, _store: FileRemote) -> None:
    r.make_task(a, TASK)


def _set_status(rep: r.Replica, status: str) -> None:
    """Статус через ORM с захватом: пара значений любая, без проверки перехода."""
    t.tick()
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        row = s.scalars(select(TaskModel).where(TaskModel.task_id == TASK)).one()
        row.status = status


def _task(rep: r.Replica) -> TaskModel:
    with transactional(rep.sf, commit=False) as s:
        row = s.scalars(select(TaskModel).where(TaskModel.task_id == TASK)).one()
        s.expunge(row)
        return row


# --- правило на паре значений -------------------------------------------------------


def _clash(column: str, ours: object, theirs: object, *, theirs_later: bool, **bases: object):
    early, late = ("1760000000000.0000.a", "a"), ("1760000000001.0000.b", "b")
    return merge.FieldClash(
        table="task",
        entity_uid="u",
        column=column,
        ours=ours,  # type: ignore[arg-type]
        theirs=theirs,  # type: ignore[arg-type]
        ours_base=bases.get("ours_base"),  # type: ignore[arg-type]
        theirs_base=bases.get("theirs_base"),  # type: ignore[arg-type]
        ours_stamp=early if theirs_later else late,
        theirs_stamp=late if theirs_later else early,
        theirs_op="b",
    )


def _value(res: merge.Resolution, ours: str, theirs: str) -> str | None:
    """Чем кончится поле на этой реплике; ``None`` — конфликт."""
    if res.outcome is merge.Outcome.CONFLICT:
        assert res.conflict_kind == "status"
        return None
    return theirs if res.outcome is merge.Outcome.THEIRS else ours


@pytest.mark.parametrize(("x", "y"), list(itertools.permutations(ALLOWED_TRANSITIONS, 2)))
def test_every_status_pair_decides_the_same_on_both_replicas(x: str, y: str) -> None:
    """x записан раньше, y позже: реплика с x в хвосте и реплика с y — одно решение."""
    on_x = status_merge.resolve(_clash("status", x, y, theirs_later=True))
    on_y = status_merge.resolve(_clash("status", y, x, theirs_later=False))
    decided = {_value(on_x, x, y), _value(on_y, y, x)}
    assert len(decided) == 1
    allowed = y in ALLOWED_TRANSITIONS.get(x, frozenset())
    assert decided == ({y} if allowed else {None})


def test_legacy_spelling_of_the_same_bucket_is_plain_lww() -> None:
    res = status_merge.resolve(_clash("status", "pending", "todo", theirs_later=True))
    assert res.outcome is merge.Outcome.THEIRS
    res = status_merge.resolve(_clash("status", "pending", "todo", theirs_later=False))
    assert res.outcome is merge.Outcome.OURS


def test_lock_fields_conflict_only_when_both_took_a_free_lock() -> None:
    double = status_merge.resolve(_clash("checked_out_by", "x", "y", theirs_later=True))
    assert (double.outcome, double.conflict_kind) == (merge.Outcome.CONFLICT, "double_checkout")
    released = status_merge.resolve(
        _clash("checked_out_by", "x", None, theirs_later=True, theirs_base="x")
    )
    assert released.outcome is merge.Outcome.THEIRS  # снятие против захвата — LWW
    assert merge.classify("task", "checked_out_at") == merge.STATUS


@pytest.mark.parametrize("column", status_merge.CHAIN_COLUMNS)
def test_replay_of_any_number_of_writes_does_not_depend_on_their_order(column: str) -> None:
    """CLO-044: итог цепочки — функция множества записей, а не порядка их прихода.

    Сколько угодно записей с любыми значениями и ``base``; повтор операции
    (тот же ``op_id``, доставленный дважды) считается один раз.
    """
    rng = random.Random(column)
    values: list[object] = (
        [*ALLOWED_TRANSITIONS, "pending", "review", None]
        if column == "status"
        else ["agent:x", "agent:y", None]
    )
    for _ in range(400):
        writes = [
            status_merge.Write(
                f"17600000{rng.randrange(100):05d}.{rng.randrange(3):04d}.{rng.choice('abc')}",
                f"op{i:02d}",
                rng.choice(values),  # type: ignore[arg-type]
                rng.choice(values),  # type: ignore[arg-type]
            )
            for i in range(rng.randint(1, 7))
        ]
        expected = status_merge.replay(column, writes)
        shuffled = [*writes, *rng.sample(writes, k=min(2, len(writes)))]
        rng.shuffle(shuffled)
        assert status_merge.replay(column, shuffled) == expected


def test_sequential_writes_are_not_judged_concurrent_ones_are() -> None:
    """Запись поверх текущего значения цепочки применяется как есть; поверх иного — судится."""
    w = status_merge.Write
    linear = [w("1.0000.a", "1", "done", "todo"), w("2.0000.a", "2", "backlog", "done")]
    assert status_merge.replay("status", linear) == status_merge.Replay("backlog", ())
    concurrent = [w("1.0000.a", "1", "cancelled", "todo"), w("2.0000.b", "2", "blocked", "todo")]
    result = status_merge.replay("status", concurrent)
    assert result.value == "cancelled"
    assert [(r.write.op_id, r.current) for r in result.rejected] == [("2", "cancelled")]


# --- две реплики --------------------------------------------------------------------


def test_allowed_pair_goes_to_the_later_write_on_all_replicas(tmp_path: Path) -> None:
    """blocked (A, раньше) → cancelled (B, позже): переход допустим, побеждает B."""

    def edits(a: r.Replica, b: r.Replica) -> None:
        _set_status(a, "blocked")
        _set_status(b, "cancelled")

    universes = t.two_orders(tmp_path, _setup, edits)
    for u in universes:
        assert t.exchange(u).conflicts == []
        t.settle(u)
        assert _task(u.a).status == _task(u.b).status == "cancelled"
        assert t.signatures(u.a) == t.signatures(u.b) == set()
        t.assert_converged(u)
    assert len({t.digest_of(rep) for u in universes for rep in u.replicas}) == 1
    t.dispose(*universes)


def test_later_write_wins_even_when_it_is_the_merging_side(tmp_path: Path) -> None:
    """in_review (A, раньше) и done (B, позже): победитель — своё у B и чужое у A."""

    def edits(a: r.Replica, b: r.Replica) -> None:
        _set_status(a, "in_progress")
        _set_status(a, "in_review")
        _set_status(b, "in_progress")
        _set_status(b, "done")

    universes = t.two_orders(tmp_path, _setup, edits)
    for u in universes:
        assert t.exchange(u).conflicts == []
        t.settle(u)
        assert _task(u.a).status == _task(u.b).status == "done"
        t.assert_converged(u)
    assert len({t.digest_of(rep) for u in universes for rep in u.replicas}) == 1
    t.dispose(*universes)


def test_forbidden_pair_opens_a_status_conflict_and_converges_after_resolve(
    tmp_path: Path,
) -> None:
    """cancelled (A, раньше) → blocked (B, позже): cancelled→blocked запрещён.

    Цепочка (CLO-044): в БД cancelled на обеих репликах при любом порядке,
    отказ — запись B; конфликт с одной подписью у обеих — каждая проиграла
    цепочку с чужой записью. ``ours`` — значение цепочки, ``theirs`` — отказ.
    """

    def edits(a: r.Replica, b: r.Replica) -> None:
        _set_status(a, "cancelled")
        _set_status(b, "blocked")

    universes = t.two_orders(tmp_path, _setup, edits)
    signatures = []
    for u in universes:
        pulled = t.exchange(u)
        assert len(pulled.conflicts) == 1
        assert _task(u.a).status == _task(u.b).status == "cancelled"
        for rep in u.replicas:
            (row,) = t.open_conflicts(rep)
            assert (row["kind"], row["field"], row["ours"], row["theirs"], row["base"]) == (
                "status",
                "status",
                "cancelled",
                "blocked",
                "todo",
            )
        assert t.signatures(u.a) == t.signatures(u.b)
        signatures.append(t.signatures(u.second))
        # Разрешаем значением цепочки на каждой реплике, где конфликт открыт.
        for rep in u.replicas:
            t.resolve_all(rep, lambda _row: "ours")
        t.settle(u)
        assert _task(u.a).status == _task(u.b).status == "cancelled"
        assert t.signatures(u.a) == t.signatures(u.b) == set()
        t.assert_converged(u)
    assert signatures[0] == signatures[1]
    # Ревизия разрешения у каждой вселенной своя (CLO-024): между вселенными —
    # без журнала, внутри вселенной полный дайджест сверил assert_converged.
    assert len({t.authored_digest_of(rep) for u in universes for rep in u.replicas}) == 1
    t.dispose(*universes)


@pytest.mark.parametrize("same_agent", [False, True])
def test_double_offline_checkout_opens_one_conflict_per_task(
    tmp_path: Path, same_agent: bool
) -> None:
    """Обе реплики взяли свободную задачу офлайн: конфликт double_checkout.

    Тот же агент на двух машинах — тоже двойной захват: ``checked_out_by``
    совпадает, различается ``checked_out_at``. Цепочка замка (CLO-044): замок
    у первого по штампу захвата (A) на обеих репликах, отказ — захват B,
    конфликт с одной подписью у обеих.
    """
    agents = {"a": "agent:laptop", "b": "agent:laptop" if same_agent else "agent:desktop"}

    locks: dict[str, tuple[object, object]] = {}

    def take(rep: r.Replica) -> None:
        t.tick()
        with transactional(rep.sf) as s:
            checkout_service.checkout(s, TASK, project_id=rep.project_id, agent=agents[rep.name])
        got = _task(rep)
        locks[rep.name] = (got.checked_out_by, got.checked_out_at)

    def edits(a: r.Replica, b: r.Replica) -> None:
        take(a)
        take(b)

    universes = t.two_orders(tmp_path, _setup, edits)
    signatures = []
    for u in universes:
        before = _task(u.a)
        pulled = t.exchange(u)
        assert len(pulled.conflicts) == 1
        (row,) = t.open_conflicts(u.second)
        assert (row["kind"], row["field"]) == ("double_checkout", None)
        for rep in u.replicas:  # замок первого захвата — у обеих
            after = _task(rep)
            assert (after.status, after.checked_out_by, after.checked_out_at) == (
                "in_progress",
                before.checked_out_by,
                before.checked_out_at,
            )
        assert t.signatures(u.a) == t.signatures(u.b)
        signatures.append(t.signatures(u.second))
        # Разрешение (CLO-031): держатель один — отвергнутый захват B; после
        # обмена он у обеих, а маркер разрешения закрывает конфликт везде.
        b_lock = locks["b"]
        with transactional(u.second.sf) as s:
            conflicts.resolve_conflict(
                s, u.second.project_id, str(row["uid"]), take="theirs", author=r.AUTHOR
            )
        t.settle(u)
        for rep in u.replicas:
            got = _task(rep)
            assert (got.checked_out_by, got.checked_out_at) == b_lock
        assert t.open_conflicts(u.a) == t.open_conflicts(u.b) == []
        t.assert_converged(u)
    assert signatures[0] == signatures[1]
    t.dispose(*universes)


def test_lock_taken_and_released_on_one_side_is_not_a_double_checkout(tmp_path: Path) -> None:
    """A взяла и отпустила задачу, B её взяла: к обмену замок держит одна B.

    Сливающая чужую историю по шагам B видит и захват A, и его снятие; решает
    последняя запись поля в changeset'е (``Context.retract``), поэтому B, как
    и A, у которой та же история в хвосте, конфликта не открывает.
    """

    def edits(a: r.Replica, b: r.Replica) -> None:
        t.tick()
        with transactional(a.sf) as s:
            checkout_service.checkout(s, TASK, project_id=a.project_id, agent="agent:laptop")
        t.tick()
        with transactional(a.sf) as s:
            checkout_service.release(s, TASK, project_id=a.project_id, agent="agent:laptop")
        t.tick()
        with transactional(b.sf) as s:
            checkout_service.checkout(s, TASK, project_id=b.project_id, agent="agent:desktop")

    universes = t.two_orders(tmp_path, _setup, edits)
    for u in universes:
        assert t.exchange(u).conflicts == []
        t.settle(u)
        assert _task(u.a).checked_out_by == _task(u.b).checked_out_by == "agent:desktop"
        t.assert_converged(u)
    assert len({t.digest_of(rep) for u in universes for rep in u.replicas}) == 1
    t.dispose(*universes)


def test_inadmissible_intermediate_status_of_the_other_side_does_not_conflict(
    tmp_path: Path,
) -> None:
    """A: todo → cancelled → todo, B позже: todo → blocked. Итог A (todo) → blocked допустим.

    Промежуточное cancelled → blocked запрещено, но судит последняя запись A.
    """

    def edits(a: r.Replica, b: r.Replica) -> None:
        _set_status(a, "cancelled")
        _set_status(a, "todo")
        _set_status(b, "blocked")

    universes = t.two_orders(tmp_path, _setup, edits)
    for u in universes:
        assert t.exchange(u).conflicts == []
        t.settle(u)
        assert _task(u.a).status == _task(u.b).status == "blocked"
        t.assert_converged(u)
    assert len({t.digest_of(rep) for u in universes for rep in u.replicas}) == 1
    t.dispose(*universes)
