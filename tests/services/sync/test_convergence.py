"""CLO-027: сходимость трёх реплик при любой перестановке pull/push (RFC 29 §5, SCN-087).

Acceptance: «Property-тест: три реплики с пересекающимися офлайн-правками в
любой перестановке pull/push приходят к одному дайджесту и одному множеству
открытых конфликтов по uid; тест на SCN-087.»

**Как устроен прогон.** Реплики A, B, C одного проекта и ``FileRemote`` в
tmp; общая история заведена на A и разошлась по всем. Дальше несколько
раундов: каждая реплика офлайн делает случайные правки всех видов слияния —
LWW поля, вставки по rank, текст (diff3), статус и checkout, рёбра и тег-связи,
удаление против правки, двойники натурального ключа (документ, секция,
тег, буква секции плана), сценарии на restricted-документе своей реплики
(ссылка и ``group_key`` придержаны, F8) и правки их заголовка с других реплик. Затем картина копируется в несколько «вселенных»
(все три БД и remote), и в каждой синхронизация идёт своей случайной
перестановкой ``push``/``pull`` (push без повтора: отказ CAS — тоже шаг
перестановки), пока система не затихнет: хвостов нет, все три головы равны
голове remote.

**Что сравнивается и чем это отличается от буквы acceptance.**

1. *Дайджест — после разрешения всех конфликтов, а не сразу.* Конфликт
   оставляет в БД «наше» (RFC §3.7), и реплика, открывшая его, держит своё
   значение поля, пока человек не решит (D1 PR #225). Поэтому до разрешения
   три реплики законно расходятся в полях открытых конфликтов. Проверка:
   каждая реплика разрешает свои открытые конфликты (``take`` выводится из
   подписи — одинаково во всех вселенных), система снова затихает, и так до
   пустого списка на всех трёх; затем полный дайджест (вместе с журналом
   ревизий) одинаков у трёх реплик, ``sync verify`` равен remote, уезжающее
   состояние совпадает построчно.
2. *Множество конфликтов — по подписи, а не по uid.* ``sync_conflict`` —
   local-таблица (§3.2): конфликт открывает реплика, которая сливала свой
   хвост с чужим changeset'ом, и uid у него uuid7 этой реплики (у
   ``key_merge`` — uuid5, но и он есть только у склеивших). «Одно множество
   по uid» на трёх репликах недостижимо по построению. Сравнивается
   множество подписей ``(entity_kind, entity_uid, field, kind)`` открытых
   конфликтов, объединённое по трём репликам в момент затихания: оно
   одинаково во всех вселенных одного сида, то есть не зависит от
   перестановки. Сравнение по репликам внутри вселенной — нет: кто открыл,
   зависит от того, кто сливал.
3. Статус пишут любые реплики любое число раз: цепочка записей решается
   одинаково при любом порядке доставки (CLO-044).
4. Двойной офлайн-checkout (``double_checkout``) генератор не порождает:
   checkout задачи в раунде делает одна реплика. Его разрешение — механика
   аренды CLO-031, до неё конфликт не сходится (D6 PR #225). Двойники
   leased-ключей (``task_id``) тоже вне генератора — CLO-030.

``CLO027_SEEDS`` в окружении расширяет прогон (``CLO027_SEEDS=200``); упавший
сид воспроизводится одним параметром.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import random
import shutil
import sqlite3
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Priority,
    ScenarioKind,
    Sensitivity,
    TaskType,
)
from cod_doc.infra import sync_capture as cap
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    DocumentTagModel,
    PlanSectionModel,
    ScenarioModel,
    TagModel,
    TaskModel,
)
from cod_doc.infra.remote.file import FileRemote
from cod_doc.services import checkout_service, doc_service, scenario_service, task_service
from cod_doc.services.plan_service import sections as plan_sections
from cod_doc.services.sync_service import _local, conflicts, digest, pull, push
from cod_doc.services.sync_service.errors import PushRejectedError
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

TICK = 0.002  # больше миллисекунды HLC
TASKS = ("DEM-001", "DEM-002", "DEM-003")
LOCK_TASK = "DEM-003"
MAIN_DOC = "spec/main"
DOOMED_DOC = "spec/doomed"
TAG = "remote"
SECRET_DOC = "secret/vault"
LINES = 6
STATUSES = ("backlog", "todo", "in_progress", "blocked", "review", "done", "cancelled")
ROUNDS = 2
UNIVERSES = 3
RANDOM_STEPS = 14
MAX_SETTLE = 12
MAX_RESOLVE = 6

type Signature = tuple[str, str, str | None, str]
type Edit = Callable[[r.Replica, random.Random, int], None]


def _seeds() -> list[int]:
    return list(range(int(os.environ.get("CLO027_SEEDS", "8"))))


# --- общая история -------------------------------------------------------------------


def _text(prefix: str) -> str:
    return "\n".join(f"{prefix} строка {i}" for i in range(LINES))


def _setup(a: r.Replica) -> None:
    for task_id in TASKS:
        r.make_task(a, task_id, description=_text(task_id))
    r.make_doc(a, MAIN_DOC, {f"s{i}": _text(f"s{i}") for i in range(3)})
    r.make_doc(a, DOOMED_DOC, {"d0": _text("d0"), "d1": _text("d1")})
    with transactional(a.sf) as s:
        cap.set_author(s, r.AUTHOR)
        s.add(TagModel(project_id=a.project_id, name=TAG))
        scenario_service.create(
            s,
            project_id=a.project_id,
            title="Шаги",
            kind=ScenarioKind.HAPPY_PATH,
            preconditions="p",
            expected="e",
            author=r.AUTHOR,
            steps=["1", "2", "3"],
            group_key="remote-sync",
            scenario_id="SCN-500",
        )


# --- офлайн-правки -------------------------------------------------------------------


def _task_row(s: object, task_id: str) -> TaskModel:
    return s.scalars(select(TaskModel).where(TaskModel.task_id == task_id)).one()  # type: ignore[attr-defined]


def _edit_title(rep: r.Replica, rng: random.Random, n: int) -> None:
    title = f"{rep.name}-{n}-{rng.choice('xyz')}"
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        row = _task_row(s, rng.choice(TASKS))
        row.title = title
        row.normalized_title = title


def _edit_priority(rep: r.Replica, rng: random.Random, _n: int) -> None:
    with transactional(rep.sf) as s:
        task_service.update_priority(
            s,
            project_id=rep.project_id,
            task_id=rng.choice(TASKS),
            new_priority=rng.choice(list(Priority)),
            author=r.AUTHOR,
        )


def _edit_line(text: str | None, rng: random.Random, mark: str) -> str:
    lines = (text or "").split("\n")
    i = rng.randrange(len(lines))
    lines[i] = mark
    return "\n".join(lines)


def _edit_description(rep: r.Replica, rng: random.Random, n: int) -> None:
    task_id = rng.choice(TASKS)
    with transactional(rep.sf) as s:
        current = _task_row(s, task_id).description
        task_service.update_description(
            s,
            project_id=rep.project_id,
            task_id=task_id,
            new_description=_edit_line(current, rng, f"{rep.name}-{n}"),
            author=r.AUTHOR,
        )


def _edit_section(rep: r.Replica, rng: random.Random, n: int, doc_key: str, anchor: str) -> None:
    with transactional(rep.sf) as s:
        doc_id = r.doc_id(rep, doc_key)
        sections = doc_service.get_sections(s, doc_id, project_id=rep.project_id)
        sec = next(x for x in sections if x.anchor == anchor)
        doc_service.patch_section(
            s,
            project_id=rep.project_id,
            document_id=doc_id,
            anchor=anchor,
            new_body=_edit_line(sec.body, rng, f"{rep.name}-{n}"),
            author=r.AUTHOR,
            reindex=False,
        )


def _edit_main_body(rep: r.Replica, rng: random.Random, n: int) -> None:
    _edit_section(rep, rng, n, MAIN_DOC, rng.choice(["s0", "s1", "s2"]))


def _edit_doomed_body(rep: r.Replica, rng: random.Random, n: int) -> None:
    _edit_section(rep, rng, n, DOOMED_DOC, rng.choice(["d0", "d1"]))


def _delete_doomed(rep: r.Replica, _rng: random.Random, _n: int) -> None:
    with transactional(rep.sf) as s:
        doc_service.delete(s, project_id=rep.project_id, doc_key=DOOMED_DOC, author=r.AUTHOR)


def _write_status(rep: r.Replica, task_id: str, status: str) -> None:
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        _task_row(s, task_id).status = status


def _set_status(rep: r.Replica, rng: random.Random, _n: int) -> None:
    """Статус через ORM: значение любое, писателей и записей сколько угодно.

    Переход проверяет слияние — проигрыванием цепочки всех записей статуса
    задачи (CLO-044); ограничение «две записи статуса на раунд» снято.
    """
    _write_status(rep, rng.choice(TASKS[:2]), rng.choice(STATUSES))


def _checkout_or_release(rep: r.Replica, _rng: random.Random, _n: int) -> None:
    with transactional(rep.sf) as s:
        row = _task_row(s, LOCK_TASK)
        if row.checked_out_by is None:
            checkout_service.checkout(s, LOCK_TASK, project_id=rep.project_id, agent=r.AUTHOR)
        else:
            checkout_service.release(
                s, LOCK_TASK, project_id=rep.project_id, agent=r.AUTHOR, force=True
            )


def _add_dependency(rep: r.Replica, rng: random.Random, n: int) -> None:
    with transactional(rep.sf) as s:
        task_service.add_dependency(
            s,
            project_id=rep.project_id,
            task_id=TASKS[0],
            blocker_task_id=TASKS[1],
            note=f"{rep.name}-{n}" if rng.random() < 0.5 else None,
            author=r.AUTHOR,
        )


def _remove_dependency(rep: r.Replica, _rng: random.Random, _n: int) -> None:
    with transactional(rep.sf) as s:
        task_service.remove_dependency(
            s,
            project_id=rep.project_id,
            task_id=TASKS[0],
            blocker_task_id=TASKS[1],
            author=r.AUTHOR,
        )


def _dependency_note(rep: r.Replica, _rng: random.Random, n: int) -> None:
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        s.scalars(select(DependencyModel)).one().note = f"{rep.name}-{n}"


def _toggle_tag(rep: r.Replica, _rng: random.Random, _n: int) -> None:
    doc = r.doc_id(rep, MAIN_DOC)
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        link = s.scalars(select(DocumentTagModel).where(DocumentTagModel.document_id == doc))
        found = link.first()
        if found is not None:
            s.delete(found)
            return
        tag = s.scalars(select(TagModel).where(TagModel.name == TAG)).one()
        s.add(DocumentTagModel(document_id=doc, tag_id=tag.row_id))


def _add_step(rep: r.Replica, rng: random.Random, n: int) -> None:
    with transactional(rep.sf) as s:
        scenario_service.add_step(
            s,
            project_id=rep.project_id,
            scenario_id="SCN-500",
            text=f"шаг {rep.name}-{n}",
            author=r.AUTHOR,
            position=rng.choice([None, 0, 1, 2]),
        )


def _twin_doc(rep: r.Replica, _rng: random.Random, n: int) -> None:
    """Один doc_key на нескольких репликах — склейка имени-смысла (CLO-023)."""
    r.make_doc(rep, f"twin/round-{n // 100}", {"intro": _text(f"twin {rep.name}")})


def _twin_section(rep: r.Replica, _rng: random.Random, n: int) -> None:
    with transactional(rep.sf) as s:
        doc_service.add_section(
            s,
            project_id=rep.project_id,
            document_id=r.doc_id(rep, MAIN_DOC),
            anchor=f"faq-{n // 100}",
            heading="FAQ",
            level=2,
            position=3,
            body=_text(f"faq {rep.name}"),
            author=r.AUTHOR,
            reindex=False,
        )


def _twin_tag(rep: r.Replica, _rng: random.Random, n: int) -> None:
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        s.add(TagModel(project_id=rep.project_id, name=f"tag-{n // 100}"))


def _plan_letter(rep: r.Replica, _rng: random.Random, n: int) -> None:
    """Буква секции плана — метка-порядок: вторая получает следующую букву."""
    with transactional(rep.sf) as s:
        letters = set(s.scalars(select(PlanSectionModel.letter)))
        letter = next(x for x in "KLMNOPQRSTUVWXYZ" if x not in letters)
        plan_sections.create_section(
            s,
            project_id=rep.project_id,
            plan_scope="demo-plan",
            letter=letter,
            title=f"{rep.name} {n}",
            author=r.AUTHOR,
        )


def _restricted_scenario(rep: r.Replica, rng: random.Random, n: int) -> None:
    """Сценарий на restricted-документе своей реплики: ссылка и group_key придержаны.

    Restricted-документ не уезжает, поэтому у каждой реплики свой; сценарий
    уезжает без ``doc_key``/``document_id``/якоря/хэша/``group_key`` (F8).
    """
    offset = {"a": 600, "b": 700, "c": 800}[rep.name]
    with transactional(rep.sf) as s:
        if doc_service.get(s, rep.project_id, SECRET_DOC) is None:
            doc_service.create(
                s,
                project_id=rep.project_id,
                doc_key=SECRET_DOC,
                type=DocumentType.MODULE_SPEC,
                status=DocumentStatus.DRAFT,
                title="vault",
                author=r.AUTHOR,
                path=f"docs/{SECRET_DOC}.md",
                sensitivity=Sensitivity.RESTRICTED,
                reindex=False,
            )
        scenario_service.create(
            s,
            project_id=rep.project_id,
            title=f"тайный {rep.name} {n}",
            kind=ScenarioKind.INVARIANT,
            preconditions="p",
            expected="e",
            author=r.AUTHOR,
            scenario_id=f"SCN-{offset + n % 100}",
            doc_key=SECRET_DOC,
            group_key=rng.choice([None, "vault-group"]),
            steps=["шаг"],
        )


def _scenario_title(rep: r.Replica, rng: random.Random, n: int) -> None:
    """Заголовок любого сценария — в том числе чужого, чья ссылка придержана."""
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        rows = list(s.scalars(select(ScenarioModel).order_by(ScenarioModel.scenario_id)))
        rng.choice(rows).title = f"{rep.name}-{n}"


def _new_task(rep: r.Replica, _rng: random.Random, n: int) -> None:
    """Новая задача со своим номером (двойники leased-ключа — CLO-030, вне генератора)."""
    offset = {"a": 100, "b": 200, "c": 300}[rep.name]
    plan_id, section_id = r.plan_ids(rep)
    with transactional(rep.sf) as s:
        task_service.create(
            s,
            project_id=rep.project_id,
            plan_id=plan_id,
            section_id=section_id,
            task_id=f"DEM-{offset + n % 100}",
            title=f"new {rep.name} {n}",
            type=TaskType.FEATURE,
            priority=Priority.LOW,
            author=r.AUTHOR,
        )


EDITS: tuple[tuple[Edit, int], ...] = (
    (_edit_title, 3),
    (_edit_priority, 2),
    (_edit_description, 4),
    (_edit_main_body, 4),
    (_edit_doomed_body, 2),
    (_delete_doomed, 1),
    (_set_status, 3),
    (_add_dependency, 2),
    (_remove_dependency, 1),
    (_dependency_note, 1),
    (_toggle_tag, 2),
    (_add_step, 2),
    (_twin_doc, 1),
    (_twin_section, 1),
    (_twin_tag, 1),
    (_plan_letter, 1),
    (_new_task, 1),
    (_restricted_scenario, 1),
    (_scenario_title, 1),
)


def _offline(reps: tuple[r.Replica, ...], rng: random.Random, rnd: int) -> None:
    """Правки раунда: на каждой реплике 2–5 случайных; checkout — у одной реплики."""
    edits = [e for e, _ in EDITS]
    weights = [w for _, w in EDITS]
    locker = reps[rnd % len(reps)]
    for rep in reps:
        count = rng.randint(2, 5)
        for i in range(count):
            n = rnd * 100 + i + 1
            edit = rng.choices(edits, weights)[0]
            time.sleep(TICK)
            # Локальная запись не прошла (строки нет, ребро уже есть): правки нет.
            with contextlib.suppress(Exception):
                edit(rep, rng, n)
        if rep is locker and rng.random() < 0.6:
            time.sleep(TICK)
            with contextlib.suppress(Exception):  # замок чужой — checkout'а нет
                _checkout_or_release(rep, rng, rnd)


# --- синхронизация -------------------------------------------------------------------


@dataclass
class Universe:
    reps: tuple[r.Replica, ...]
    store: FileRemote


def _push(rep: r.Replica, store: FileRemote) -> None:
    # remote ушёл вперёд — отказ тоже шаг перестановки
    with contextlib.suppress(PushRejectedError):
        push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store, max_retries=0)


def _pull(rep: r.Replica, store: FileRemote) -> None:
    pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _head(rep: r.Replica) -> str | None:
    with transactional(rep.sf, commit=False) as s:
        return _local.local_head(s, rep.project_id)


def _quiet(u: Universe) -> bool:
    with transactional(u.reps[0].sf, commit=False) as s:
        uid = str(_local.project_ref(s, r.SLUG).uid)
    ref = u.store.get_ref(uid, _local.MAIN)
    remote_head = None if ref is None else ref.sha
    return all(r.pending(rep) == 0 and _head(rep) == remote_head for rep in u.reps)


def _settle(u: Universe, rng: random.Random) -> None:
    """Случайная перестановка push/pull, затем круги pull+push до затихания."""
    for _ in range(RANDOM_STEPS):
        rep = rng.choice(u.reps)
        (_push if rng.random() < 0.5 else _pull)(rep, u.store)
    for _ in range(MAX_SETTLE):
        if _quiet(u):
            return
        for rep in rng.sample(u.reps, len(u.reps)):
            _pull(rep, u.store)
            _push(rep, u.store)
    assert _quiet(u), "синхронизация не затихла"


def _signatures(rep: r.Replica) -> set[Signature]:
    with transactional(rep.sf, commit=False) as s:
        return {
            (str(c["entity_kind"]), str(c["entity_uid"]), c["field"], str(c["kind"]))  # type: ignore[misc]
            for c in conflicts.list_conflicts(s, rep.project_id)
        }


def _take(row: dict[str, object]) -> str:
    """``ours``/``theirs`` из подписи — одинаково во всех вселенных."""
    key = f"{row['entity_kind']}|{row['entity_uid']}|{row['field']}|{row['kind']}"
    return "ours" if hashlib.sha256(key.encode()).digest()[0] % 2 else "theirs"


def _resolve_everything(u: Universe, rng: random.Random) -> None:
    for _ in range(MAX_RESOLVE):
        if not any(_signatures(rep) for rep in u.reps):
            return
        for rep in u.reps:
            with transactional(rep.sf) as s:
                for row in conflicts.list_conflicts(s, rep.project_id):
                    time.sleep(TICK)
                    conflicts.resolve_conflict(
                        s, rep.project_id, str(row["uid"]), take=_take(row), author=r.AUTHOR
                    )
        _settle(u, rng)
    left = {rep.name: _signatures(rep) for rep in u.reps}
    assert not any(left.values()), f"конфликты не кончаются: {left}"


def _digest(rep: r.Replica) -> str:
    with transactional(rep.sf, commit=False) as s:
        return digest.compute(s, rep.project_id)


def _state(rep: r.Replica) -> list[bytes]:
    """Строки дайджеста: ровно то, что хешируется (придержанное — значением приёмника)."""
    with transactional(rep.sf, commit=False) as s:
        return [digest._line(*line) for line in cap.shipped_state(s, rep.project_id)]


def _assert_converged(u: Universe, where: str) -> None:
    digests = {rep.name: _digest(rep) for rep in u.reps}
    assert len(set(digests.values())) == 1, f"{where}: дайджесты разошлись {digests}"
    states = [_state(rep) for rep in u.reps]
    assert states[0] == states[1] == states[2], f"{where}: состояние разошлось"
    for rep in u.reps:
        assert digest.verify(rep.sf, r.SLUG, store=u.store).equal, f"{where}: verify {rep.name}"


def _copy(tmp: Path, u: Universe) -> Universe:
    tmp.mkdir()
    reps = []
    for rep in u.reps:
        path = tmp / rep.db_path.name
        with sqlite3.connect(rep.db_path) as from_, sqlite3.connect(path) as to:
            from_.backup(to)
        engine = make_engine(f"sqlite:///{path}")
        reps.append(replace(rep, sf=make_session_factory(engine), engine=engine, db_path=path))
    shutil.copytree(u.store.root, tmp / "remote")
    return Universe(tuple(reps), FileRemote(tmp / "remote"))


def _dispose(*universes: Universe) -> None:
    for u in universes:
        for rep in u.reps:
            rep.engine.dispose()


def _three(tmp_path: Path) -> Universe:
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    c = r.replica_b(tmp_path, a, name="c")
    u = Universe((a, b, c), r.remote(tmp_path))
    _setup(a)
    _push(a, u.store)
    _settle(u, random.Random(0))
    return u


def run_seed(tmp_path: Path, seed: int) -> None:
    """Один сид: ``ROUNDS`` раундов офлайн-правок, каждый — в ``UNIVERSES`` перестановках."""
    rng = random.Random(seed)
    base = _three(tmp_path)
    for rnd in range(1, ROUNDS + 1):
        _offline(base.reps, rng, rnd)
        copies = [_copy(tmp_path / f"r{rnd}u{i}", base) for i in range(1, UNIVERSES)]
        unions: list[set[Signature]] = []
        for i, u in enumerate((base, *copies)):
            order = random.Random(f"{seed}/{rnd}/{i}")
            _settle(u, order)
            unions.append(set().union(*(_signatures(rep) for rep in u.reps)))
            _resolve_everything(u, order)
            _assert_converged(u, f"сид {seed}, раунд {rnd}, вселенная {i}")
        assert all(x == unions[0] for x in unions), (
            f"сид {seed}, раунд {rnd}: подписи конфликтов зависят от перестановки {unions}"
        )
        _dispose(*copies)
    _dispose(base)


@pytest.mark.parametrize("seed", _seeds())
def test_three_replicas_converge_in_any_push_pull_order(tmp_path: Path, seed: int) -> None:
    run_seed(tmp_path, seed)


def test_scn_087_three_offline_changesets_converge(tmp_path: Path) -> None:
    """SCN-087 как написан: A, B, C правят пересекающееся офлайн; pull+push на A, B, C;
    pull на всех, пока новое приходит; ``sync verify`` на каждой — один дайджест."""
    u = _three(tmp_path)
    a, b, c = u.reps
    time.sleep(TICK)
    _edit_title(a, random.Random(1), 1)  # LWW-поле
    r.set_description(a, TASKS[0], _edit_line(_text(TASKS[0]), random.Random(0), "от A"))
    time.sleep(TICK)
    r.set_description(b, TASKS[0], _edit_line(_text(TASKS[0]), random.Random(3), "от B"))
    _twin_tag(b, random.Random(0), 1)
    time.sleep(TICK)
    _edit_title(c, random.Random(2), 1)
    _twin_tag(c, random.Random(0), 1)
    _add_step(c, random.Random(0), 1)

    for rep in (a, b, c):  # шаг 1
        _pull(rep, u.store)
        _push(rep, u.store)
    for _ in range(MAX_SETTLE):  # шаг 2
        if _quiet(u):
            break
        for rep in (a, b, c):
            _pull(rep, u.store)
            _push(rep, u.store)

    assert _quiet(u)
    assert set().union(*(_signatures(rep) for rep in u.reps)) == set()
    _assert_converged(u, "SCN-087")  # шаг 3
    _dispose(u)


# --- находки property-теста: регрессии ------------------------------------------------


def _synced(u: Universe, *order: r.Replica) -> None:
    for rep in order:
        _pull(rep, u.store)
        _push(rep, u.store)


def test_double_delete_with_an_own_edit_before_it_reaches_the_third_replica(
    tmp_path: Path,
) -> None:
    """Сид 0: A правит секцию и удаляет документ, C удаляет тот же документ.

    C пушит первым, B принимает удаление. A сливает: своё удаление в хвосте,
    чужое удаление строки, которой у A уже нет, — пустое. Хвост A (правка
    секции и документа, затем удаление) уезжает, и B получал правку строки,
    которую уже снёс: ``SYNC-102 … её создание старше журнала``. Удаление уже в
    истории B, правка мёртвой строки — пустая операция.
    """
    u = _three(tmp_path)
    a, b, c = u.reps
    time.sleep(TICK)
    _edit_doomed_body(a, random.Random(0), 1)
    _delete_doomed(a, random.Random(0), 1)
    time.sleep(TICK)
    _delete_doomed(c, random.Random(0), 1)

    _synced(u, c, b, a)
    _synced(u, b, c)

    assert _quiet(u)
    assert set().union(*(_signatures(rep) for rep in u.reps)) == set()
    _assert_converged(u, "двойное удаление")
    _dispose(u)


def test_double_delete_opens_no_delete_edit_whoever_pushes_first(tmp_path: Path) -> None:
    """Сиды 8 и 44: B правит секцию и удаляет документ, C удаляет тот же документ.

    Пушит первым C — конфликта нет нигде. Пушит первым B — C сливал правку B
    против своего удаления и открывал ``delete_edit``, хотя тот же changeset B
    дальше удаляет корень сам: правка обеих сторон мертва. Подпись открытых
    конфликтов зависела от перестановки.
    """
    u = _three(tmp_path)
    a, b, c = u.reps
    time.sleep(TICK)
    _edit_doomed_body(b, random.Random(0), 1)
    _delete_doomed(b, random.Random(0), 1)
    time.sleep(TICK)
    _delete_doomed(c, random.Random(0), 1)

    _synced(u, b, c, a)
    _synced(u, b)

    assert _quiet(u)
    assert set().union(*(_signatures(rep) for rep in u.reps)) == set()
    _assert_converged(u, "двойное удаление, B первым")
    _dispose(u)


def test_edge_removed_and_readded_on_one_replica_survives_on_the_others(tmp_path: Path) -> None:
    """Сид 53: B снял тег-связь и поставил её снова — в changeset'е delete и upsert
    одного uid ребра.

    Применение удаляло строку ``session.delete`` без flush, и следующая операция
    находила ту же строку в identity map: вставка ложилась правкой в удаляемый
    объект, flush его стирал. У B связь есть, у A и C — нет.
    """
    u = _three(tmp_path)
    a, b, c = u.reps
    _toggle_tag(a, random.Random(0), 1)
    _synced(u, a, b, c)
    time.sleep(TICK)
    _toggle_tag(b, random.Random(0), 1)
    time.sleep(TICK)
    _toggle_tag(b, random.Random(0), 2)

    _synced(u, b, a, c)

    assert _quiet(u)
    assert all(r.authored_state(rep).get("document_tag") for rep in u.reps)
    _assert_converged(u, "ребро снято и поставлено снова")
    _dispose(u)


def test_three_twins_glue_into_one_survivor_and_resolve_everywhere(tmp_path: Path) -> None:
    """Сид 91: одна секция ``faq`` заведена на A, B и C с разными телами.

    C пушит первым, A склеивает и пушит, B получает сначала двойника C (B
    выживает), потом двойника A (A выживает: создан раньше). Конфликт первой
    склейки оставался на проигравшем uid B — подпись указывала на алиас.
    Его разрешение писало операцию под этим uid, которого никто, кроме B, не
    знает (свой проигравший uid не уезжает), и A падал ``SYNC-102``.
    """
    u = _three(tmp_path)
    a, b, c = u.reps
    for rep in (a, b, c):
        time.sleep(TICK)
        _twin_section(rep, random.Random(0), 100)

    _synced(u, c, a, b, c, a, b)

    survivors = {rep.name: _signatures(rep) for rep in u.reps}
    entities = {sig[1] for sigs in survivors.values() for sig in sigs}
    assert len(entities) == 1, f"конфликты склейки на разных uid: {survivors}"
    _resolve_everything(u, random.Random(0))
    _assert_converged(u, "три двойника")
    _dispose(u)


def test_absorbed_equal_fields_stay_in_the_creation_op_of_a_glued_row(tmp_path: Path) -> None:
    """Сиды 91, 280: три двойника секции, B пушит первым, C склеивается с ним.

    Хвост C — его вставка, переписанная на uid выжившего. A получал её поверх
    своей ещё не отправленной вставки: равные поля (якорь, заголовок,
    документ) поглощались — уходили из хвоста, в том числе из операции
    создания. Вставка A уезжала без NOT NULL-колонок, и реплика, у которой
    этой строки ещё нет, падала ``NOT NULL constraint failed: section.document_id``.
    """
    u = _three(tmp_path)
    a, b, c = u.reps
    for rep in (a, b, c):
        time.sleep(TICK)
        _twin_section(rep, random.Random(0), 100)

    _synced(u, b, c, a, b, c, a)

    _resolve_everything(u, random.Random(0))
    _assert_converged(u, "поглощение в операции создания")
    _dispose(u)


def test_twin_creation_rekeyed_onto_an_existing_row_is_not_a_text_conflict(
    tmp_path: Path,
) -> None:
    """Сид 91, раунд 2: три двойника секции, A пушит, B склеивается и пушит.

    Хвост B — его вставка, переписанная на uid A. C склеивает свой двойник с
    вставкой A (``key_merge``), а затем получает вставку B под тем же uid уже
    как запись в существующую строку: две операции создания тела, общего
    предка нет — ``text`` по D4. В других перестановках вставку B склеивает
    стратегия ключей, и ``text`` нет: множество подписей зависело от порядка.
    """
    u = _three(tmp_path)
    a, b, c = u.reps
    for rep in (a, b, c):
        time.sleep(TICK)
        _twin_section(rep, random.Random(0), 100)

    _push(a, u.store)
    _pull(b, u.store)
    _push(b, u.store)
    _synced(u, a, b, c, a, b, c)

    kinds = {sig[3] for rep in u.reps for sig in _signatures(rep)}
    assert kinds == {"key_merge"}
    _resolve_everything(u, random.Random(0))
    _assert_converged(u, "вставка двойника поверх строки")
    _dispose(u)


def _text_conflict_on_doomed(tmp_path: Path) -> Universe:
    """A и C правят одну строку секции обречённого документа; C сливает — ``text``."""
    u = _three(tmp_path)
    a, _b, c = u.reps
    time.sleep(TICK)
    _edit_section(a, random.Random(0), 1, DOOMED_DOC, "d0")
    time.sleep(TICK)
    _edit_section(c, random.Random(0), 2, DOOMED_DOC, "d0")
    _synced(u, a, c)
    assert {sig[3] for sig in _signatures(c)} == {"text"}
    return u


def test_foreign_delete_closes_conflicts_on_the_rows_it_removes(tmp_path: Path) -> None:
    """Сид 447: удаление B доходит до C, у которой открыт ``text`` на секции документа.

    Хвост C уже уехал, пересечения нет — секция уходит молча, а ``text``
    оставался на строке, которой нет. В перестановке, где удаление приходит к
    C раньше правки A, конфликта ``text`` нет вовсе: множество подписей
    зависело от порядка. Удаление закрывает конфликты на снесённых строках.
    """
    u = _text_conflict_on_doomed(tmp_path)
    a, b, c = u.reps
    time.sleep(TICK)
    _delete_doomed(b, random.Random(0), 1)

    _synced(u, b, c, a)

    assert _signatures(c) == set()
    with transactional(c.sf, commit=False) as s:
        (closed,) = conflicts.list_conflicts(s, c.project_id, status="resolved")
    assert (closed["kind"], closed["resolved_by"]) == ("text", "sync:deleted")
    _resolve_everything(u, random.Random(0))
    _assert_converged(u, "удаление закрыло конфликт")
    _dispose(u)


def test_conflict_on_a_row_deleted_locally_can_still_be_resolved(tmp_path: Path) -> None:
    """Сид 280: конфликт на строке, которой больше нет, ``sync resolve`` не закрывал.

    Ответ был ``SYNC-302: … в проекте больше нет`` — пункт висел в очереди
    куратора навсегда. Так бывает и после своего удаления: его захват
    конфликты не трогает.
    """
    u = _text_conflict_on_doomed(tmp_path)
    c = u.reps[2]
    time.sleep(TICK)
    _delete_doomed(c, random.Random(0), 1)

    _resolve_everything(u, random.Random(0))
    _assert_converged(u, "конфликт на удалённой строке")
    _dispose(u)


def test_local_row_does_not_inherit_revisions_of_a_deleted_row(tmp_path: Path) -> None:
    """Сид 0, раунд 2: B удалил документ с наибольшим ``row_id`` и завёл новый.

    SQLite без AUTOINCREMENT отдаёт новой строке номер удалённой, и ревизии
    удалённого документа (адрес — ``(entity_kind, entity_id)``) прирастали к
    новому: на B они в дайджесте под uid нового документа, на A и C — нет
    (там строку завёл pull с номером выше всех ревизий, ``apply._fresh_row_id``).
    """
    u = _three(tmp_path)
    a, b, c = u.reps
    time.sleep(TICK)
    _delete_doomed(b, random.Random(0), 1)
    r.make_doc(b, "spec/fresh", {"x0": _text("x0")})

    _synced(u, b, a, c)

    assert _quiet(u)
    _assert_converged(u, "номер удалённой строки")
    _dispose(u)


@pytest.mark.parametrize("group_key", [None, "vault-group"])
def test_withheld_columns_hash_the_same_on_owner_and_receivers(
    tmp_path: Path, group_key: str | None
) -> None:
    """F8: у владельца сценария на restricted-документе дайджест был свой.

    Владелец не хешировал придержанные колонки (ссылка, якорь, хэш,
    group_key), приёмники хешировали то, что у них лежит: NULL и заглушку.
    ``sync verify`` — равен у A, не равен у B и C; на main до этого PR так же.
    """
    u = _three(tmp_path)
    a, b, c = u.reps
    rng = random.Random(0 if group_key is None else 1)
    _restricted_scenario(a, rng, 1)
    with transactional(a.sf) as s:
        scenario_service.update(
            s,
            project_id=a.project_id,
            scenario_id="SCN-601",
            author=r.AUTHOR,
            section_anchor="body",
        )

    _synced(u, a, b, c)

    for rep in u.reps:
        assert digest.verify(rep.sf, r.SLUG, store=u.store).equal, rep.name
    _assert_converged(u, "придержанные колонки")
    time.sleep(TICK)
    _scenario_title(b, random.Random(5), 9)  # приёмник правит чужой сценарий
    _synced(u, b, a, c)
    _assert_converged(u, "правка приёмником")
    _dispose(u)


@pytest.mark.parametrize("order", ["abc", "cba", "bca"])
def test_three_sections_claiming_one_letter_get_the_same_letters_everywhere(
    tmp_path: Path, order: str
) -> None:
    """Сид 9: A, B и C завели секцию плана с одной буквой.

    Вторая секция брала следующую свободную букву в момент прихода, и у трёх
    заявителей ответ зависел от порядка: на A и B — b=L, c=M, на C — b=M, c=L.
    """
    u = _three(tmp_path)
    reps = {rep.name: rep for rep in u.reps}
    for rep in u.reps:
        time.sleep(TICK)
        _plan_letter(rep, random.Random(0), 1)

    _synced(u, *(reps[x] for x in order + order))

    assert _quiet(u)
    _assert_converged(u, f"три заявки на букву, порядок {order}")
    _dispose(u)


def _settle_fixed(u: Universe, *order: r.Replica) -> None:
    for _ in range(MAX_SETTLE):
        if _quiet(u):
            return
        _synced(u, *order)
    assert _quiet(u), "синхронизация не затихла"


def _status(rep: r.Replica, task_id: str) -> str:
    with transactional(rep.sf, commit=False) as s:
        return str(_task_row(s, task_id).status)


@pytest.mark.parametrize("order", ["abc", "acb", "bac", "bca", "cab", "cba"])
def test_status_chain_of_three_writers_decides_the_same_in_any_order(
    tmp_path: Path, order: str
) -> None:
    """F11 (сид 386): blocked (A) → in_progress (B) → done (C), каждая из todo, офлайн.

    Попарно: B принимала done и обрезала свой in_progress, тот не уезжал, и C
    судила blocked → done напрямую — конфликт ``status`` только в части
    порядков. Цепочка: blocked, затем in_progress (blocked → in_progress
    допустим), затем done — в любом порядке, без конфликта.
    """
    u = _three(tmp_path)
    reps = {rep.name: rep for rep in u.reps}
    for name, status in (("a", "blocked"), ("b", "in_progress"), ("c", "done")):
        time.sleep(TICK)
        _write_status(reps[name], TASKS[0], status)

    _settle_fixed(u, *(reps[x] for x in order))

    assert {_status(rep, TASKS[0]) for rep in u.reps} == {"done"}
    assert set().union(*(_signatures(rep) for rep in u.reps)) == set()
    _assert_converged(u, f"цепочка статуса, порядок {order}")
    _dispose(u)


@pytest.mark.parametrize("order", ["ab", "ba"])
def test_two_writes_of_one_replica_are_judged_as_a_chain(tmp_path: Path, order: str) -> None:
    """Сид 25: backlog (A) против cancelled → in_review (B).

    Цепочка по штампу: backlog; cancelled поверх todo — конкурентна, backlog →
    cancelled допустим; in_review последовательна (B видела cancelled). Итог
    in_review, без конфликта, при любом порядке.
    """
    u = _three(tmp_path)
    a, b, _c = u.reps
    time.sleep(TICK)
    _write_status(a, TASKS[0], "backlog")
    time.sleep(TICK)
    _write_status(b, TASKS[0], "cancelled")
    time.sleep(TICK)
    _write_status(b, TASKS[0], "in_review")

    first, second = (a, b) if order == "ab" else (b, a)
    _settle_fixed(u, first, second, u.reps[2])

    assert {_status(rep, TASKS[0]) for rep in u.reps} == {"in_review"}
    assert set().union(*(_signatures(rep) for rep in u.reps)) == set()
    _assert_converged(u, f"две записи одной реплики, порядок {order}")
    _dispose(u)


def test_rejected_status_write_opens_the_same_conflict_on_every_replica(tmp_path: Path) -> None:
    """cancelled (A) раньше blocked (B): cancelled → blocked запрещён.

    Проигрывание у всех одно: в БД cancelled на всех трёх репликах, отказ —
    запись B, конфликт с одной подписью у каждой реплики, получившей чужую
    запись цепочки.
    """
    u = _three(tmp_path)
    a, b, c = u.reps
    time.sleep(TICK)
    _write_status(a, TASKS[0], "cancelled")
    time.sleep(TICK)
    _write_status(b, TASKS[0], "blocked")

    _settle_fixed(u, b, c, a)

    assert {_status(rep, TASKS[0]) for rep in u.reps} == {"cancelled"}
    sigs = [_signatures(rep) for rep in u.reps]
    assert sigs[0] == sigs[1] == sigs[2]
    ((kind, _uid, field, ckind),) = sigs[0]
    assert (kind, field, ckind) == ("task", "status", "status")
    with transactional(b.sf, commit=False) as s:
        (row,) = conflicts.list_conflicts(s, b.project_id)
    assert (row["ours"], row["theirs"], row["base"]) == ("cancelled", "blocked", "todo")
    _assert_converged(u, "отказ в цепочке")
    _dispose(u)
