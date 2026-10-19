"""CLO-052: алиасы клона выводятся из проверенной истории, а не из снапшота.

Acceptance: «Clone не читает алиасы из снапшота; алиасы на клоне совпадают с
алиасами обычной реплики, получившей ту же историю (тест); подложенная в снапшот
строка alias игнорируется или даёт SYNC-102; склейка после clone решается так
же, как между обычными репликами.»

«Обычная реплика» здесь — C: своих правок нет, вся история приехала pull'ом.
Алиас склейки local (CLO-023): у реплики, чей проигравший uid не уехал, он есть,
у остальных — нет. Поэтому эталон для клона — не реплика-сборщик снапшота, а C.
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.infra import sync_capture as cap
from cod_doc.infra.db import transactional
from cod_doc.infra.models import EntityAliasModel, SectionModel, SyncOpModel
from cod_doc.services.sync_service import apply, digest, pull, push, snapshot
from tests.services.sync import _replicas as r
from tests.services.sync import test_convergence as conv
from tests.services.sync.test_natural_keys import ANCHOR, DOC, _add_section
from tests.services.sync.test_snapshot_clone import (
    _as_replica,
    _clone,
    _latest,
    _replace_snapshot,
    _rewrite,
    _uid,
)

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

_TICK = 0.003
type Alias = tuple[str, str, str, str]


@pytest.fixture(autouse=True)
def _snapshot_every_push(monkeypatch: pytest.MonkeyPatch) -> None:
    """Снапшот на каждом push: сборщиком становится тот, кто пушил последним."""
    monkeypatch.setattr(snapshot, "SNAPSHOT_EVERY", 1)


def _push(rep: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote) -> None:
    pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _aliases(rep: r.Replica) -> list[Alias]:
    with transactional(rep.sf, commit=False) as s:
        rows = s.scalars(select(EntityAliasModel).order_by(EntityAliasModel.alias))
        return [(a.entity_kind, a.alias, a.uid, a.reason) for a in rows]


def _journal(rep: r.Replica) -> set[tuple[str, str, str]]:
    with transactional(rep.sf, commit=False) as s:
        rows = s.execute(
            select(SyncOpModel.op_id, SyncOpModel.entity_kind, SyncOpModel.entity_uid).where(
                SyncOpModel.changeset.is_not(None)
            )
        )
        return {(op_id, kind, uid) for op_id, kind, uid in rows}


def _digest(rep: r.Replica) -> str:
    with transactional(rep.sf, commit=False) as s:
        return digest.compute(s, rep.project_id)


def _ordinary(tmp_path: Path, a: r.Replica, store: FileRemote, name: str = "c") -> r.Replica:
    """Реплика без своих правок: вся история — pull'ом."""
    rep = r.replica_b(tmp_path, a, name=name)
    _pull(rep, store)
    return rep


def _heading(rep: r.Replica, value: str) -> None:
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        row = s.scalars(select(SectionModel).where(SectionModel.anchor == ANCHOR)).one()
        row.heading = value


def _headings(rep: r.Replica) -> list[tuple[str, str]]:
    with transactional(rep.sf, commit=False) as s:
        rows = s.scalars(select(SectionModel).where(SectionModel.anchor == ANCHOR))
        return [(str(row.uid), row.heading) for row in rows]


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    store = r.remote(tmp_path)
    r.make_doc(a, DOC, {"intro": "вступление"})
    _push(a, store)
    _pull(b, store)
    return a, b, store


# --- алиасы клона = алиасы обычной реплики ------------------------------------------


@pytest.mark.parametrize("builder", ["a", "b"])
@pytest.mark.parametrize("first_push", ["a", "b"])
def test_clone_aliases_equal_an_ordinary_replica(
    pair: tuple[r.Replica, r.Replica, FileRemote], tmp_path: Path, first_push: str, builder: str
) -> None:
    """Двойник секции склеен на A и B; снапшот собирает A или B; C — вся история pull'ом.

    A создал раньше: выживает его uid. Если первым пушит A, склеивает только B, и
    его проигравший uid не уезжает — алиас есть лишь у B; если первым пушит B, оба
    uid в истории, и алиас есть у всех, кто видел обе вставки. Клон обязан совпасть
    с C при любом сборщике снапшота: раньше снапшот B вёз алиас на uid, которого нет
    в истории, и clone падал SYNC-102.
    """
    a, b, store = pair
    uid_a = _add_section(a, "тело X")
    uid_b = _add_section(b, "тело Y")
    first, second = (a, b) if first_push == "a" else (b, a)
    _push(first, store)
    _pull(second, store)
    _push(second, store)
    _pull(first, store)
    maker = a if builder == "a" else b
    r.make_task(maker, "DEM-001")  # последний push — сборщика: снапшот с его состояния
    head = _push(maker, store).head
    assert snapshot.pointer(store, _uid(a))[0].sha == head  # type: ignore[index]

    c = _ordinary(tmp_path, a, store)
    _clone(store, a, tmp_path / "clone")
    k = _as_replica("k")

    expected = [("section", uid_b, uid_a, "merged")] if first_push == "b" else []
    assert _aliases(c) == expected
    assert _aliases(k) == _aliases(c)
    assert _journal(k) == _journal(c)
    assert _digest(k) == _digest(c)
    with transactional(k.sf, commit=False) as sk, transactional(c.sf, commit=False) as sc:
        for uid in (uid_a, uid_b):
            assert apply.alias_target(sk, k.project_id, "section", uid) == apply.alias_target(
                sc, c.project_id, "section", uid
            )


def test_edit_after_clone_is_judged_against_the_losers_history(
    pair: tuple[r.Replica, r.Replica, FileRemote], tmp_path: Path
) -> None:
    """Журнал проигравшего — история выжившего и на клоне: LWW поздней правки тот же.

    B заводит двойника (uid B) и пушит первым; E и F получают его. F правит заголовок
    uid B (t2) и молчит; E правит его же позже (t3) и пушит. A (создал раньше, правит
    свой заголовок в t1) склеивает, пушит и пишет снапшот. Клон K и обычная реплика C
    получают правку F уже переписанной на uid A: в истории выжившего есть t3 от E,
    и t2 проигрывает. Без перенесённого журнала у клона победила бы t2.
    """
    a, b, store = pair
    e = _ordinary(tmp_path, a, store, "e")
    f = _ordinary(tmp_path, a, store, "f")
    uid_a = _add_section(a, "тело X")
    _heading(a, "A t1")
    _add_section(b, "тело Y")
    _push(b, store)
    _pull(e, store)
    _pull(f, store)
    _heading(f, "F t2")
    _heading(e, "E t3")
    _push(e, store)
    _pull(a, store)
    _push(a, store)  # склейка у A, снапшот с головы A

    c = _ordinary(tmp_path, a, store)
    _clone(store, a, tmp_path / "clone")
    k = _as_replica("k")
    assert _aliases(k) == _aliases(c) != []
    assert _journal(k) == _journal(c)

    _pull(f, store)
    _push(f, store)
    for rep in (a, c, k):
        _pull(rep, store)
    assert _headings(k) == _headings(c) == _headings(a) == [(uid_a, "E t3")]
    assert _digest(k) == _digest(c) == _digest(a)


def test_glue_after_clone_resolves_like_between_ordinary_replicas(
    pair: tuple[r.Replica, r.Replica, FileRemote], tmp_path: Path
) -> None:
    """Снапшот снят до второго двойника; склейку клон делает сам, обычным pull'ом."""
    a, b, store = pair
    uid_a = _add_section(a, "тело X")
    uid_b = _add_section(b, "тело Y")
    _push(b, store)  # снапшот с головы B: двойника A в нём нет
    _clone(store, a, tmp_path / "clone")
    k = _as_replica("k")
    assert _aliases(k) == []

    _pull(a, store)
    _push(a, store)
    c = _ordinary(tmp_path, a, store)
    _pull(k, store)
    assert _aliases(k) == _aliases(c) == [("section", uid_b, uid_a, "merged")]
    assert _journal(k) == _journal(c)
    assert _digest(k) == _digest(c)


# --- снапшот алиасов не несёт --------------------------------------------------------


def test_snapshot_carries_no_alias_lines(
    pair: tuple[r.Replica, r.Replica, FileRemote],
) -> None:
    """Алиасы дайджест не покрывает: снапшот их не пишет, даже если у сборщика они есть."""
    a, b, store = pair
    _add_section(a, "тело X")
    _add_section(b, "тело Y")
    _push(b, store)
    _pull(a, store)
    assert _aliases(a) != []
    _push(a, store)
    _, data = _latest(store, a)
    kinds = {ln[0] for ln in list(snapshot._lines(data))[1:] if isinstance(ln, list)}
    assert kinds == {"row"}


def test_forged_alias_line_is_ignored(
    pair: tuple[r.Replica, r.Replica, FileRemote],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Подложенная строка alias (снапшот старого формата или подлог) не создаёт алиаса.

    Строки и дайджест верны, а алиас перенаправил бы операции над выдуманным uid в
    живую секцию. История авторитетна: строка пропускается с записью в лог.
    """
    a, _b, store = pair
    uid_a = _add_section(a, "тело X")
    _push(a, store)
    _, data = _latest(store, a)
    fake = "0199ffff-0000-7000-8000-00000000dead"
    forged = [
        "alias",
        {"entity_kind": "section", "alias": fake, "uid": uid_a, "reason": "merged"},
    ]
    _replace_snapshot(store, a, _rewrite(data, lambda lines: [*lines, forged]))

    with caplog.at_level(logging.WARNING, logger="cod_doc.services.sync_service.snapshot"):
        _clone(store, a, tmp_path / "clone")
    k = _as_replica("k")
    assert _aliases(k) == []
    with transactional(k.sf, commit=False) as s:
        assert apply.alias_target(s, k.project_id, "section", fake) is None
    assert "alias" in caplog.text
    assert digest.verify(k.sf, r.SLUG, store=store).equal


# --- property: история со склейками, clone в конце ------------------------------------


def _clone_seeds() -> list[int]:
    """``CLO052_SEEDS`` в окружении расширяет прогон, как ``CLO027_SEEDS`` у сходимости."""
    return list(range(int(os.environ.get("CLO052_SEEDS", "3"))))


@pytest.mark.parametrize("seed", _clone_seeds())
def test_clone_after_random_history_with_twins_matches_an_ordinary_replica(
    tmp_path: Path, seed: int
) -> None:
    """Три реплики правят офлайн (двойники документа, секции, тега среди прочего) и
    синхронизируются случайной перестановкой; снапшоты — на каждом push, сборщик —
    кто пушил последним. Клон и обычная реплика D (вся история pull'ом) совпадают
    по дайджесту, журналу и множеству алиасов.
    """
    rng = random.Random(seed)
    u = conv._three(tmp_path)
    for rnd in range(1, conv.ROUNDS + 1):
        for rep in u.reps:  # двойники в каждом раунде на каждой реплике
            time.sleep(conv.TICK)
            conv._twin_doc(rep, rng, rnd * 100)
            time.sleep(conv.TICK)
            conv._twin_section(rep, rng, rnd * 100)
        conv._offline(u.reps, rng, rnd)
        conv._settle(u, random.Random(f"{seed}/{rnd}"))
    a = u.reps[0]
    d = _ordinary(tmp_path, a, u.store, "d")
    _clone(u.store, a, tmp_path / "clone")
    k = _as_replica("k")

    if not _aliases(d):  # двойник первого пушившего уезжает один: склейки в истории нет
        pytest.skip(f"сид {seed}: в истории нет склейки с обеими вставками")
    assert _aliases(k) == _aliases(d)
    assert _journal(k) == _journal(d)
    assert _digest(k) == _digest(d)
    conv._dispose(u)
