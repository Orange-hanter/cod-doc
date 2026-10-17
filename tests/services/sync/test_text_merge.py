"""CLO-019: 3-way merge текстовых колонок (RFC 29 §3.7).

Acceptance: «Колонки text_columns из реестра (section.body, task.description,
task.acceptance, task_document.body, adr.decision, user_story.narrative)
сливаются построчно от base; непересекающиеся ханки сливаются,
пересекающиеся — конфликт kind=text, в БД остаётся «наше».»

Сценарные тесты гоняют одни офлайн-правки в двух порядках push/pull
(``_two_orders``): сведённый текст и дайджест одинаковы у всех четырёх
реплик — это и есть симметрия ``merge3(base, A, B) == merge3(base, B, A)``
на живых репликах. Конфликт открывает тот, кто сливал вторым, в БД у него
остаётся своё; после разрешения одним и тем же текстом обе вселенные
сходятся к одному дайджесту.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import transactional
from cod_doc.infra.models import SectionModel, TaskModel
from cod_doc.services.sync_service import merge, text_merge
from cod_doc.services.sync_service.text_merge import Conflict, merge3, split_lines
from tests.services.sync import _replicas as r
from tests.services.sync import _two_orders as t

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

TASK = "DEM-001"
BASE = "один\nдва\nтри\nчетыре\nпять\n"


# --- merge3 ---------------------------------------------------------------------------


def test_registry_text_columns_are_exactly_the_rfc_list() -> None:
    columns = {(t_, c) for t_, spec in reg.REGISTRY.items() for c in spec.text_columns}
    assert columns == {
        ("section", "body"),
        ("task", "description"),
        ("task", "acceptance"),
        ("task_document", "body"),
        ("adr", "decision"),
        ("user_story", "narrative"),
    }
    assert {merge.classify(t_, c) for t_, c in columns} == {merge.TEXT}


def test_disjoint_hunks_merge_in_either_order() -> None:
    ours = BASE.replace("один", "ОДИН")
    theirs = BASE.replace("пять", "ПЯТЬ")
    expected = "ОДИН\nдва\nтри\nчетыре\nПЯТЬ\n"
    assert merge3(BASE, ours, theirs) == merge3(BASE, theirs, ours) == expected


def test_adjacent_lines_and_insertions_at_a_boundary_merge() -> None:
    ours = BASE.replace("два\n", "ДВА\n")  # строка 2
    theirs = BASE.replace("три\n", "ТРИ\n")  # соседняя строка 3
    assert merge3(BASE, ours, theirs) == "один\nДВА\nТРИ\nчетыре\nпять\n"
    inserted = BASE.replace("три\n", "вставка\nтри\n")  # вставка перед строкой 3
    changed = BASE.replace("три\n", "ТРИ\n")
    assert merge3(BASE, inserted, changed) == merge3(BASE, changed, inserted)
    assert merge3(BASE, inserted, changed) == "один\nдва\nвставка\nТРИ\nчетыре\nпять\n"


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        (BASE.replace("три", "А"), BASE.replace("три", "Б")),  # одна строка
        (BASE.replace("два\nтри", "А"), BASE.replace("три\nчетыре", "Б")),  # перекрытие
        (BASE + "А\n", BASE + "Б\n"),  # две вставки в одну точку
        (BASE.replace("два\nтри\nчетыре", "А"), BASE.replace("три\n", "три\nБ\n")),
    ],
)
def test_overlapping_hunks_conflict_in_either_order(ours: str, theirs: str) -> None:
    with pytest.raises(Conflict):
        merge3(BASE, ours, theirs)
    with pytest.raises(Conflict):
        merge3(BASE, theirs, ours)


def test_identical_edits_are_absorbed_and_one_side_unchanged_takes_the_other() -> None:
    both = BASE.replace("три", "ТРИ")
    assert merge3(BASE, both, both) == both
    # Одинаковая правка внутри разных общих: тот же ханк с обеих сторон.
    ours = both.replace("один", "ОДИН")
    theirs = both.replace("пять", "ПЯТЬ")
    assert merge3(BASE, ours, theirs) == "ОДИН\nдва\nТРИ\nчетыре\nПЯТЬ\n"
    assert merge3(BASE, BASE, theirs) == theirs
    assert merge3(BASE, ours, BASE) == ours


def test_trailing_newline_crlf_and_empty_base() -> None:
    no_nl = "a\nb\nc"
    assert split_lines(no_nl) == ["a\n", "b\n", "c"]
    # Дописать строку после последней без перевода — значит изменить и её.
    assert merge3(no_nl, "A\nb\nc", "a\nb\nc\nd") == "A\nb\nc\nd"
    with pytest.raises(Conflict):
        merge3(no_nl, "a\nb\nc\n", "a\nb\nC")
    crlf = "a\r\nb\r\nc\r\nd\r\n"
    merged = merge3(crlf, crlf.replace("a", "A"), crlf.replace("d", "D"))
    assert merged == "A\r\nb\r\nc\r\nD\r\n"  # \r\n не теряется
    assert merge3("", "только A", "") == "только A"
    assert merge3("", "одно", "одно") == "одно"
    with pytest.raises(Conflict):
        merge3("", "A\n", "B\n")  # два тела с нуля: вставки в одну точку


def _mutate(rng: random.Random, lines: list[str], lo: int, hi: int) -> list[str]:
    out = list(lines)
    i = rng.randint(lo, hi)
    action = rng.choice(("replace", "insert", "delete"))
    if action == "replace" and i < hi:
        out[i] = f"зам{rng.randint(0, 9)}\n"
    elif action == "delete" and i < hi:
        del out[i]
    else:
        out.insert(i, f"нов{rng.randint(0, 9)}\n")
    return out


@pytest.mark.parametrize("seed", range(40))
def test_merge3_is_symmetric(seed: int) -> None:
    """Любые правки двух сторон: результат или конфликт не зависит от того, кто «наш»."""
    rng = random.Random(seed)
    base = [f"строка {i}\n" for i in range(rng.randint(0, 8))]
    sides = []
    for _ in range(2):
        side = list(base)
        for _ in range(rng.randint(1, 3)):
            side = _mutate(rng, side, 0, len(side))
        sides.append("".join(side))
    text = "".join(base)
    outcomes = []
    for ours, theirs in (sides, sides[::-1]):
        try:
            outcomes.append(merge3(text, ours, theirs))
        except Conflict:
            outcomes.append(None)
    assert outcomes[0] == outcomes[1]


def _clash(base: object, ours: object, theirs: object, ours_base: object = None) -> object:
    return merge.FieldClash(
        table="task",
        entity_uid="u",
        column="description",
        ours=ours,  # type: ignore[arg-type]
        theirs=theirs,  # type: ignore[arg-type]
        ours_base=base if ours_base is None else ours_base,  # type: ignore[arg-type]
        theirs_base=base,  # type: ignore[arg-type]
        ours_stamp=("1", "a"),
        theirs_stamp=("2", "b"),
        theirs_op="b",
    )


def test_resolve_outcomes() -> None:
    def outcome(*args: object) -> merge.Resolution:
        return text_merge.resolve(_clash(*args))  # type: ignore[arg-type]

    sub = BASE.replace("два", "ДВА")
    assert outcome(BASE, sub, sub.replace("пять", "ПЯТЬ")).outcome is merge.Outcome.THEIRS
    # Сведённое совпало с «нашим» — всё равно merged: наш штамп мог проиграть.
    res = outcome(BASE, sub.replace("пять", "ПЯТЬ"), sub)
    assert (res.outcome, res.value) == (merge.Outcome.MERGED, "один\nДВА\nтри\nчетыре\nПЯТЬ\n")
    # diff3 без «zealous»-доводки: ханк одной стороны накрыл ту же правку другой
    # и соседнюю строку — это пересечение, а не поглощение.
    res = outcome(BASE, sub, sub.replace("один", "ОДИН"))
    assert res.outcome is merge.Outcome.CONFLICT
    res = outcome(BASE, BASE.replace("три", "А"), BASE.replace("три", "Б"))
    assert (res.outcome, res.conflict_kind) == (merge.Outcome.CONFLICT, "text")
    res = outcome(BASE, "x", "y", "другая база")
    assert res.outcome is merge.Outcome.CONFLICT  # разные базы: общего предка нет
    assert outcome(None, "одно", "одно").outcome is merge.Outcome.THEIRS


# --- две реплики --------------------------------------------------------------------


def _task_setup(a: r.Replica, _b: r.Replica, _store: FileRemote) -> None:
    r.make_task(a, TASK, description=BASE)


def _set_task(rep: r.Replica, column: str, text: str) -> None:
    t.tick()
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        row = s.scalars(select(TaskModel).where(TaskModel.task_id == TASK)).one()
        setattr(row, column, text)


def _column(rep: r.Replica, column: str) -> str | None:
    with transactional(rep.sf, commit=False) as s:
        row = s.scalars(select(TaskModel).where(TaskModel.task_id == TASK)).one()
        return getattr(row, column)  # type: ignore[no-any-return]


@pytest.mark.parametrize("column", ["description", "acceptance"])
def test_disjoint_edits_of_a_task_text_merge_on_both_replicas(tmp_path: Path, column: str) -> None:
    def setup(a: r.Replica, b: r.Replica, store: FileRemote) -> None:
        _task_setup(a, b, store)
        _set_task(a, column, BASE)

    def edits(a: r.Replica, b: r.Replica) -> None:
        _set_task(a, column, BASE.replace("один", "ОДИН от A"))
        _set_task(b, column, BASE.replace("пять", "ПЯТЬ от B"))

    universes = t.two_orders(tmp_path, setup, edits)
    expected = "ОДИН от A\nдва\nтри\nчетыре\nПЯТЬ от B\n"
    for u in universes:
        pulled = t.exchange(u)
        assert pulled.conflicts == []
        assert pulled.fast_forward is False
        t.settle(u)
        for rep in u.replicas:
            assert _column(rep, column) == expected
            assert t.signatures(rep) == set()
        t.assert_converged(u)
    assert len({t.digest_of(rep) for u in universes for rep in u.replicas}) == 1
    t.dispose(*universes)


def test_identical_edits_on_both_replicas_are_absorbed(tmp_path: Path) -> None:
    same = BASE.replace("три", "ТРИ")

    def edits(a: r.Replica, b: r.Replica) -> None:
        _set_task(a, "description", same)
        _set_task(b, "description", same)

    universes = t.two_orders(tmp_path, _task_setup, edits)
    for u in universes:
        assert t.exchange(u).conflicts == []
        t.settle(u)
        assert _column(u.a, "description") == _column(u.b, "description") == same
        t.assert_converged(u)
    assert len({t.digest_of(rep) for u in universes for rep in u.replicas}) == 1
    t.dispose(*universes)


def test_overlapping_edits_conflict_keep_ours_and_converge_after_resolve(tmp_path: Path) -> None:
    ours_a = BASE.replace("три", "три от A")
    ours_b = BASE.replace("три", "три от B")

    def edits(a: r.Replica, b: r.Replica) -> None:
        _set_task(a, "description", ours_a)
        _set_task(b, "description", ours_b)

    universes = t.two_orders(tmp_path, _task_setup, edits)
    signatures = []
    for u in universes:
        pulled = t.exchange(u)
        second = u.second
        own, foreign = (ours_b, ours_a) if second is u.b else (ours_a, ours_b)
        assert len(pulled.conflicts) == 1
        assert _column(second, "description") == own  # в БД остаётся «наше»
        (row,) = t.open_conflicts(second)
        assert row["uid"] == pulled.conflicts[0]
        assert (row["kind"], row["field"], row["ours"], row["theirs"], row["base"]) == (
            "text",
            "description",
            own,
            foreign,
            BASE,
        )
        first = u.a if second is u.b else u.b
        assert t.signatures(first) == set()  # конфликт — у того, кто сливал
        signatures.append(t.signatures(second))
        t.resolve_all(second, lambda _row: "сведено руками\n")
        t.settle(u)
        assert _column(u.a, "description") == _column(u.b, "description") == "сведено руками\n"
        assert t.signatures(u.a) == t.signatures(u.b) == set()
        t.assert_converged(u)
    assert signatures[0] == signatures[1]  # тот же конфликт при любом порядке
    assert len({t.digest_of(rep) for u in universes for rep in u.replicas}) == 1
    t.dispose(*universes)


def test_section_body_merges_line_by_line(tmp_path: Path) -> None:
    def setup(a: r.Replica, _b: r.Replica, _store: FileRemote) -> None:
        r.make_doc(a, "guide/merge", {"intro": BASE})

    def set_body(rep: r.Replica, text: str) -> None:
        t.tick()
        with transactional(rep.sf) as s:
            cap.set_author(s, r.AUTHOR)
            row = s.scalars(select(SectionModel).where(SectionModel.anchor == "intro")).one()
            row.body = text

    def edits(a: r.Replica, b: r.Replica) -> None:
        set_body(a, BASE.replace("два", "два (A)"))
        set_body(b, BASE.replace("четыре", "четыре (B)"))

    universes = t.two_orders(tmp_path, setup, edits)
    for u in universes:
        assert t.exchange(u).conflicts == []
        t.settle(u)
        for rep in u.replicas:
            with transactional(rep.sf, commit=False) as s:
                body = s.scalars(select(SectionModel.body)).one()
            assert body == "один\nдва (A)\nтри\nчетыре (B)\nпять\n"
        t.assert_converged(u)
    assert len({t.digest_of(rep) for u in universes for rep in u.replicas}) == 1
    t.dispose(*universes)
