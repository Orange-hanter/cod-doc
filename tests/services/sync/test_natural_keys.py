"""CLO-023: склейка имён-смыслов и перебуквление секций плана (RFC 29 §3.3, Q-007).

Acceptance: «Ключи из RFC 29 §3.3: совпавшее имя-смысл склеивается (второй uid →
``entity_alias reason=merged``, тела — конфликт ``key_merge`` если разные);
совпавшая буква ``plan_section`` перебуквляется одинаково на репликах, событие
``plan_section.relettered``; тесты на SCN-090, SCN-091.» Плюс находка F2 секции
B: реплики, заведённые ``project add`` по отдельности, сходятся после pull, а не
падают ``IntegrityError`` на дефолтных рутинах и дереве разделов.

Каждый тест гоняет обе очерёдности: кто первым пушит, не зависит от того, кто
первым создал. Выживает uid с меньшим ``(hlc создания, uid)`` — одинаково при
любой очерёдности. Алиас и конфликт — local: их пишет реплика, которая склеивала.
Если первым пушит создавший раньше, склеивает только вторая, и её проигравший
uid не уезжает (хвост переписан на выжившего). Если первым пушит создавший
позже, его uid уже уехал — склеивают обе, и uid конфликта у них один.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Priority,
    Sensitivity,
    TaskType,
)
from cod_doc.infra import sync_capture as cap
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    DocNodeModel,
    DocumentModel,
    EntityAliasModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RoutineModel,
    SectionModel,
    TaskModel,
)
from cod_doc.services import doc_service, plan_service, project_service, task_service
from cod_doc.services.sync_service import conflicts, digest, pull, push
from cod_doc.services.sync_service.errors import ObjectMissingError
from tests.services.sync import _replicas as r
from tests.services.sync.test_apply_untrusted import _forge, _refused

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

DOC = "rfc-29"
ANCHOR = "8-открытые-вопросы"
_TICK = 0.003


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    return a, r.replica_b(tmp_path, a), r.remote(tmp_path)


def _push(rep: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _sync(first: r.Replica, second: r.Replica, store: FileRemote) -> None:
    """first push → second pull + push → first pull: до тишины."""
    _push(first, store)
    _pull(second, store)
    _push(second, store)
    _pull(first, store)


def _digest(rep: r.Replica) -> str:
    with transactional(rep.sf, commit=False) as s:
        return digest.compute(s, rep.project_id)


def _open(rep: r.Replica) -> list[dict[str, object]]:
    with transactional(rep.sf, commit=False) as s:
        return conflicts.list_conflicts(s, rep.project_id)


def _aliases(rep: r.Replica) -> list[tuple[str, str, str, str]]:
    with transactional(rep.sf, commit=False) as s:
        rows = s.scalars(select(EntityAliasModel).order_by(EntityAliasModel.alias))
        return [(a.entity_kind, a.alias, a.uid, a.reason) for a in rows]


def _converged(a: r.Replica, b: r.Replica, store: FileRemote) -> None:
    assert r.pending(a) == r.pending(b) == 0
    assert r.authored_state(a) == r.authored_state(b)
    assert _digest(a) == _digest(b)
    for rep in (a, b):
        assert digest.verify(rep.sf, r.SLUG, store=store).equal


def _order(a: r.Replica, b: r.Replica, first: str) -> tuple[r.Replica, r.Replica]:
    return (a, b) if first == "a" else (b, a)


def _gluers(a: r.Replica, b: r.Replica, first_push: str) -> tuple[r.Replica, ...]:
    """Кто склеивает, если A создал раньше: B всегда, A — если B запушил первым."""
    return (b,) if first_push == "a" else (a, b)


def _conflict_uids(rep: r.Replica) -> list[object]:
    return [c["uid"] for c in _open(rep)]


# --- SCN-090: имя-смысл ------------------------------------------------------------


def _add_section(rep: r.Replica, body: str) -> str:
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        sec = doc_service.add_section(
            s,
            project_id=rep.project_id,
            document_id=r.doc_id(rep, DOC),
            anchor=ANCHOR,
            heading="8. Открытые вопросы",
            level=2,
            position=1,
            body=body,
            author=r.AUTHOR,
            reindex=False,
        )
        row = s.get_one(SectionModel, sec.row_id)
        return str(row.uid)


def _sections(rep: r.Replica) -> list[tuple[str, str]]:
    with transactional(rep.sf, commit=False) as s:
        rows = s.scalars(select(SectionModel).where(SectionModel.anchor == ANCHOR))
        return [(str(row.uid), row.body) for row in rows]


@pytest.mark.parametrize("first_push", ["a", "b"])
def test_scn_090_same_heading_glues_into_one_section(pair: tuple, first_push: str) -> None:
    """SCN-090: одна секция, uid второй — алиас на первую, конфликт по телу X/Y."""
    a, b, store = pair
    r.make_doc(a, DOC, {"intro": "вступление"})
    _sync(a, b, store)
    uid_a = _add_section(a, "тело X")  # шаг 1: A создаёт раньше
    uid_b = _add_section(b, "тело Y")  # шаг 2

    pusher, puller = _order(a, b, first_push)
    _push(pusher, store)  # шаг 3
    pulled = _pull(puller, store)

    assert len(pulled.conflicts) == 1
    for rep in (puller,):
        (only,) = _sections(rep)
        assert only[0] == uid_a  # выжил uid с меньшим HLC создания
        assert _aliases(rep) == [("section", uid_b, uid_a, "merged")]
    _push(puller, store)
    _pull(pusher, store)

    gluers = _gluers(a, b, first_push)
    for rep in (a, b):
        assert [uid for uid, _ in _sections(rep)] == [uid_a]
        if rep not in gluers:  # проигравший uid сюда не доехал
            assert _aliases(rep) == []
            assert _open(rep) == []
            continue
        assert _aliases(rep) == [("section", uid_b, uid_a, "merged")]
        (conflict,) = _open(rep)
        assert (conflict["kind"], conflict["entity_uid"], conflict["field"]) == (
            "key_merge",
            uid_a,
            None,
        )
        sides = {conflict["ours"]["fields"]["body"], conflict["theirs"]["fields"]["body"]}  # type: ignore[index]
        assert sides == {"тело X", "тело Y"}
        with transactional(rep.sf, commit=False) as s:
            events = s.scalars(
                select(ActivityEventModel).where(ActivityEventModel.kind == "sync.alias_created")
            ).all()
            assert [e.payload for e in events] == [
                {"alias": uid_b, "uid": uid_a, "reason": "merged"}
            ]
    if len(gluers) == 2:
        assert _conflict_uids(a) == _conflict_uids(b)
    _converged(a, b, store)

    # resolve на одной реплике пишет тело в выжившую строку и уезжает обычной правкой.
    (conflict,) = _open(b)
    with transactional(b.sf) as s:
        conflicts.resolve_conflict(
            s, b.project_id, str(conflict["uid"]), take="theirs", author=r.AUTHOR
        )
    chosen = conflict["theirs"]["fields"]["body"]  # type: ignore[index]
    _sync(b, a, store)
    assert _sections(a) == _sections(b) == [(uid_a, chosen)]


@pytest.mark.parametrize("first_push", ["a", "b"])
def test_equal_bodies_glue_without_conflict(pair: tuple, first_push: str) -> None:
    a, b, store = pair
    r.make_doc(a, DOC, {"intro": "вступление"})
    _sync(a, b, store)
    uid_a = _add_section(a, "одно тело")
    uid_b = _add_section(b, "одно тело")

    _sync(*_order(a, b, first_push), store)

    for rep in (a, b):
        assert _sections(rep) == [(uid_a, "одно тело")]
        expected = [("section", uid_b, uid_a, "merged")] if rep in _gluers(a, b, first_push) else []
        assert _aliases(rep) == expected
        assert _open(rep) == []
    _converged(a, b, store)


@pytest.mark.parametrize("first_push", ["a", "b"])
def test_glued_document_children_follow_the_alias(pair: tuple, first_push: str) -> None:
    """Секции, созданные под проигравшим документом, ссылаются на его uid: резолв — алиас."""
    a, b, store = pair
    r.make_doc(a, DOC, {"from-a": "A"})
    time.sleep(_TICK)
    r.make_doc(b, DOC, {"from-b": "B"})

    _sync(*_order(a, b, first_push), store)

    for rep in (a, b):
        with transactional(rep.sf, commit=False) as s:
            anchors = s.scalars(select(SectionModel.anchor).order_by(SectionModel.anchor)).all()
        assert anchors == ["from-a", "from-b"]
        expected = ["document"] if rep in _gluers(a, b, first_push) else []
        assert [alias[0] for alias in _aliases(rep)] == expected
    _converged(a, b, store)


@pytest.mark.parametrize("first_push", ["a", "b"])
def test_later_edit_of_the_losing_uid_lands_on_the_survivor(pair: tuple, first_push: str) -> None:
    """Правка проигравшего uid, сделанная до склейки, ложится на выжившую строку.

    Правится скалярный ``heading``: тело — текст, его 3-way merge — CLO-019.
    """
    a, b, store = pair
    r.make_doc(a, DOC, {"intro": "вступление"})
    _sync(a, b, store)
    uid_a = _add_section(a, "одно тело")
    _add_section(b, "одно тело")
    time.sleep(_TICK)
    with transactional(b.sf) as s:  # правка проигравшего uid до склейки
        cap.set_author(s, r.AUTHOR)
        row = s.scalars(select(SectionModel).where(SectionModel.anchor == ANCHOR)).one()
        row.heading = "Заголовок B после создания"

    _sync(*_order(a, b, first_push), store)

    for rep in (a, b):
        with transactional(rep.sf, commit=False) as s:
            row = s.scalars(select(SectionModel).where(SectionModel.anchor == ANCHOR)).one()
            assert (row.uid, row.heading) == (uid_a, "Заголовок B после создания")
    _converged(a, b, store)


def _heading(rep: r.Replica, value: str) -> None:
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        row = s.scalars(select(SectionModel).where(SectionModel.anchor == ANCHOR)).one()
        row.heading = value


@pytest.mark.parametrize("first_push", ["a", "b"])
def test_glued_fields_follow_lww_by_last_write(pair: tuple, first_push: str) -> None:
    """Поле склеенной строки — по последней записи: правка A раньше вставки B проигрывает ей."""
    a, b, store = pair
    r.make_doc(a, DOC, {"intro": "вступление"})
    _sync(a, b, store)
    uid_a = _add_section(a, "одно тело")
    _heading(a, "заголовок A")  # раньше вставки B
    _add_section(b, "одно тело")  # heading «8. Открытые вопросы», штамп позже

    _sync(*_order(a, b, first_push), store)

    for rep in (a, b):
        with transactional(rep.sf, commit=False) as s:
            row = s.scalars(select(SectionModel).where(SectionModel.anchor == ANCHOR)).one()
            assert (row.uid, row.heading) == (uid_a, "8. Открытые вопросы")
    _converged(a, b, store)


def test_key_twin_of_a_restricted_row_is_refused(pair: tuple) -> None:
    """Совпадение ключа с restricted-строкой — SYNC-102: склейка не пишет в local-строку."""
    a, b, store = pair
    r.make_doc(a, "secret", {"intro": "A"})
    _push(a, store)
    with transactional(b.sf) as s:
        doc_service.create(
            s,
            project_id=b.project_id,
            doc_key="secret",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="secret",
            author=r.AUTHOR,
            path="docs/secret.md",
            sensitivity=Sensitivity.RESTRICTED,
            reindex=False,
        )
    before = r.authored_state(b)

    with pytest.raises(ObjectMissingError) as err:
        _pull(b, store)

    assert err.value.code == "SYNC-102"
    assert r.authored_state(b) == before
    assert _aliases(b) == []


def _plan_only_on_b(b: r.Replica, scope: str) -> None:
    """План B, заведённый до журнала: его uid никто не знает, чужая вставка выиграет."""
    with Session(b.engine) as s:
        now = datetime.now(UTC)
        plan = PlanModel(project_id=b.project_id, scope=scope, created=now, last_updated=now)
        plan.uid = "0192-b-plan"
        s.add(plan)
        s.commit()


def test_key_merge_op_with_a_local_column_is_refused(pair: tuple) -> None:
    """Чужая вставка-двойник с local-колонкой — SYNC-102 до склейки, ничего не записано."""
    a, b, store = pair
    _push(a, store)
    _pull(b, store)
    _plan_only_on_b(b, "twin")
    project_uid = _uid_of(b, ProjectModel, b.project_id)
    _forge(
        a,
        store,
        [
            {
                "entity_kind": "plan",
                "entity_uid": "0192-forged-plan",
                "fields": {"project_id": project_uid, "scope": "twin", "row_id": 1},
            }
        ],
    )

    _refused(b, store, "колонка не уезжает")
    assert _aliases(b) == []


def test_key_merge_cannot_point_a_glued_row_at_a_restricted_document(pair: tuple) -> None:
    """Склейка пишет поля через тот же финальный гейт: ссылка на restricted — SYNC-102."""
    a, b, store = pair
    _push(a, store)
    _pull(b, store)
    with transactional(b.sf) as s:
        doc = doc_service.create(
            s,
            project_id=b.project_id,
            doc_key="secret",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="secret",
            author=r.AUTHOR,
            path="docs/secret.md",
            sensitivity=Sensitivity.RESTRICTED,
            reindex=False,
        )
        secret_uid = str(s.get_one(DocumentModel, doc.row_id).uid)
    _plan_only_on_b(b, "twin")
    project_uid = _uid_of(b, ProjectModel, b.project_id)
    _forge(
        a,
        store,
        [
            {
                "entity_kind": "plan",
                "entity_uid": "0192-forged-plan",
                "fields": {"project_id": project_uid, "scope": "twin", "parent_doc_id": secret_uid},
            }
        ],
    )

    _refused(b, store, "придерживает")
    assert _aliases(b) == []


def _uid_of(rep: r.Replica, model: type, row_id: int) -> str:
    with transactional(rep.sf, commit=False) as s:
        return str(s.get_one(model, row_id).uid)


# --- SCN-091: метка-порядок --------------------------------------------------------


def _section_with_task(rep: r.Replica, title: str, task_id: str) -> str:
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        plan_service.create_section(
            s,
            project_id=rep.project_id,
            plan_scope="demo-plan",
            letter="K",
            title=title,
            author=r.AUTHOR,
        )
        sec = s.scalars(select(PlanSectionModel).where(PlanSectionModel.title == title)).one()
        task_service.create(
            s,
            project_id=rep.project_id,
            plan_id=sec.plan_id,
            section_id=sec.row_id,
            task_id=task_id,
            title=f"Task {task_id}",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author=r.AUTHOR,
        )
        return str(sec.uid)


@pytest.mark.parametrize("first_push", ["a", "b"])
def test_scn_091_same_letter_is_relettered_deterministically(pair: tuple, first_push: str) -> None:
    """SCN-091: K — созданная раньше по HLC, L — вторая; задачи при своих секциях."""
    a, b, store = pair
    _sync(a, b, store)
    uid_a = _section_with_task(a, "Remote sync", "DEM-001")  # шаг 1
    uid_b = _section_with_task(b, "Postgres parity", "DEM-002")  # шаг 2

    _sync(*_order(a, b, first_push), store)  # шаг 3

    for rep in (a, b):
        with transactional(rep.sf, commit=False) as s:
            letters = dict(
                s.execute(select(PlanSectionModel.uid, PlanSectionModel.letter)).tuples().all()
            )
            assert (letters[uid_a], letters[uid_b]) == ("K", "L")
            sec_b = s.scalars(select(PlanSectionModel).where(PlanSectionModel.uid == uid_b)).one()
            assert sec_b.slug.startswith("L-")
            owners = dict(
                s.execute(
                    select(TaskModel.task_id, PlanSectionModel.uid).join(
                        PlanSectionModel, PlanSectionModel.row_id == TaskModel.section_id
                    )
                )
                .tuples()
                .all()
            )
            assert owners == {"DEM-001": uid_a, "DEM-002": uid_b}
            events = s.scalars(
                select(ActivityEventModel).where(
                    ActivityEventModel.kind == "plan_section.relettered"
                )
            ).all()
            assert [e.payload for e in events] == [{"from": "K", "to": "L", "section_uid": uid_b}]
        assert _aliases(rep) == []
    _converged(a, b, store)


# --- F2: две реплики, заведённые project add по отдельности ------------------------


def _seed(rep: r.Replica) -> None:
    """То, что ``project add`` заводит на каждой машине: дефолтные рутины и дерево."""
    with transactional(rep.sf) as s:
        project_service._bootstrap_default_routines(s, rep.project_id)
        project_service._bootstrap_doc_tree(s, rep.project_id)


def _counts(rep: r.Replica) -> tuple[int, int]:
    with transactional(rep.sf, commit=False) as s:
        routines = s.scalar(select(func.count()).select_from(RoutineModel)) or 0
        nodes = s.scalar(select(func.count()).select_from(DocNodeModel)) or 0
        return routines, nodes


@pytest.mark.parametrize("first_push", ["a", "b"])
def test_f2_independent_project_add_converges(pair: tuple, first_push: str) -> None:
    a, b, store = pair
    _seed(a)
    time.sleep(_TICK)
    _seed(b)
    seeded = _counts(a)
    assert seeded == _counts(b)
    assert seeded[0] > 0
    assert seeded[1] > 0

    _sync(*_order(a, b, first_push), store)

    assert _counts(a) == _counts(b) == seeded
    for rep in (a, b):
        kinds = {alias[0] for alias in _aliases(rep)}
        assert kinds == ({"routine", "doc_node"} if rep in _gluers(a, b, first_push) else set())
        assert _open(rep) == []
    _converged(a, b, store)


# --- F2 сквозняком: CLI project add на двух «машинах» --------------------------------


def _run(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    return result.exit_code, result.stdout, result.stderr


def _json(*args: str) -> object:
    code, out, err = _run(*args, "--json")
    assert code == 0, (out, err)
    return json.loads(out)


def _machine(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("COD_DOC_HOME", str(home))
    Config.clear_cache()
    root = home / "repo"
    if not root.exists():
        root.mkdir()
        code, out, err = _run("project", "add", str(root), "--name", r.SLUG)
        assert code == 0, (out, err)


def _factory():  # type: ignore[no-untyped-def]
    entry = Config.load().get_project(r.SLUG)
    assert entry is not None
    return db_for_entry(entry)[0]


def _cli_digest() -> str:
    with transactional(_factory(), commit=False) as s:
        pid = s.scalars(select(ProjectModel.row_id).where(ProjectModel.slug == r.SLUG)).one()
        return digest.compute(s, pid)


def _cli_counts() -> tuple[int, int]:
    with transactional(_factory(), commit=False) as s:
        routines = s.scalar(select(func.count()).select_from(RoutineModel)) or 0
        nodes = s.scalar(select(func.count()).select_from(DocNodeModel)) or 0
        return routines, nodes


@pytest.mark.parametrize("first_push", ["a", "b"])
def test_f2_end_to_end_two_machines_after_project_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_push: str
) -> None:
    """Аудит секции B §8 без обходного шага: рутины и дерево B не удаляются.

    B — свой ``project add``, затем uid проекта A и пустой журнал (так её оставил
    бы clone). Его строки рутин и разделов — те же натуральные ключи, другой uid;
    pull склеивает их с приехавшими с A вместо ``IntegrityError``.
    """
    url = (tmp_path / "remote").as_uri()
    (tmp_path / "remote").mkdir()
    homes = {"a": tmp_path / "home-a", "b": tmp_path / "home-b"}
    _machine(homes["a"], monkeypatch)
    with transactional(_factory(), commit=False) as s:
        uid = s.scalars(select(ProjectModel.uid).where(ProjectModel.slug == r.SLUG)).one()
    _run("remote", "add", "origin", url, "-p", r.SLUG)
    _machine(homes["b"], monkeypatch)
    with transactional(_factory()) as s:
        s.execute(text("DELETE FROM sync_op"))
        s.execute(text("UPDATE project SET uid = :u WHERE slug = :s"), {"u": uid, "s": r.SLUG})
    _run("remote", "add", "origin", url, "-p", r.SLUG)
    seeded = _cli_counts()

    def on(name: str) -> None:
        _machine(homes[name], monkeypatch)

    second = "b" if first_push == "a" else "a"
    if first_push == "b":
        on("b")  # у B своего хвоста нет: пушит документ, A сливает его раньше своего push
        _seed_doc()
        assert _json("push", "-p", r.SLUG)
        on("a")
        _json("pull", "-p", r.SLUG)
    on("a")
    _json("push", "-p", r.SLUG)
    on("b")
    pulled = _json("pull", "-p", r.SLUG)
    assert isinstance(pulled, dict)
    assert pulled["applied"]
    _json("push", "-p", r.SLUG)
    on("a")
    _json("pull", "-p", r.SLUG)

    digests = {}
    for name in (first_push, second):
        on(name)
        assert _cli_counts() == seeded
        assert _json("sync", "verify", "-p", r.SLUG)["equal"] is True  # type: ignore[index]
        digests[name] = _cli_digest()
    assert digests["a"] == digests["b"]


def _seed_doc() -> None:
    with transactional(_factory()) as s:
        pid = s.scalars(select(ProjectModel.row_id).where(ProjectModel.slug == r.SLUG)).one()
        doc_service.create(
            s,
            project_id=pid,
            doc_key="guide/hello",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="Hello",
            author=r.AUTHOR,
            path="docs/guide/hello.md",
            sensitivity=Sensitivity.INTERNAL,
            reindex=False,
        )
