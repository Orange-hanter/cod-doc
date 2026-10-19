"""CLO-029: снапшоты и ``cod-doc clone`` (RFC 29 §3.5, §3.6, §10.6, §10.8).

Acceptance: «Push пишет снапшот snapshots/<epoch>/<sha> каждые 5000 операций;
cod-doc clone = снапшот + хвост, регистрация проекта в config.yaml, replica
is_self; дайджест после clone равен дайджесту головы remote; тест на SCN-082».

Реплика A — ``_replicas.replica_a`` с file-remote в tmp; клон заводится
сервисом ``clone`` в отдельный корень, реестр — ``COD_DOC_HOME`` из autouse-
фикстуры (живой ``~/.cod-doc`` не трогается).
"""

from __future__ import annotations

import time
from compression import zstd
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cod_doc.config import Config
from cod_doc.domain.entities import DocumentStatus, DocumentType, Priority, Sensitivity, TaskType
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.hlc import wall_ms
from cod_doc.infra.models import (
    ChangesetModel,
    DocumentModel,
    ProjectModel,
    ReplicaModel,
    RevisionModel,
    SyncOpModel,
    TaskModel,
)
from cod_doc.services import doc_service, scenario_service
from cod_doc.services.sync_service import clone, digest, pull, push, snapshot
from cod_doc.services.sync_service.errors import ObjectMissingError, RemoteConfigError
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import sessionmaker

    from cod_doc.infra.remote.base import SnapshotRef
    from cod_doc.infra.remote.file import FileRemote

SECRET_DOC = "vault"


@pytest.fixture
def world(tmp_path: Path) -> tuple[r.Replica, FileRemote]:
    return r.replica_a(tmp_path), r.remote(tmp_path)


def _uid(a: r.Replica) -> str:
    with transactional(a.sf) as s:
        return str(s.get_one(ProjectModel, a.project_id).uid)


def _url(store: FileRemote, a: r.Replica) -> str:
    return f"{store.root.as_uri()}/{_uid(a)}"


def _push(a: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(a.sf, r.SLUG, author=r.AUTHOR, store=store)


def _clone(store: FileRemote, a: r.Replica, root: Path, **kw: object) -> clone.CloneResult:
    return clone.clone(_url(store, a), root=root, author=r.AUTHOR, store=store, **kw)  # type: ignore[arg-type]


def _factory(slug: str = r.SLUG) -> sessionmaker[Session]:
    entry = Config.load().get_project(slug)
    assert entry is not None
    return db_for_entry(entry)[0]


def _as_replica(name: str) -> r.Replica:
    entry = Config.load().get_project(r.SLUG)
    assert entry is not None
    sf, engine = db_for_entry(entry)
    with transactional(sf) as s:
        pid = s.scalars(select(ProjectModel.row_id).where(ProjectModel.slug == r.SLUG)).one()
    return r.Replica(name, sf, engine, entry.root / ".cod-doc/state.db", pid, 0, 0)


def _pre_journal_task(a: r.Replica, task_id: str) -> None:
    """Задача, заведённая до журнала (мимо захвата): её создания нет ни в одном changeset'е."""
    plan_id, section_id = r.plan_ids(a)
    with Session(a.engine) as s:
        now = datetime.now(UTC)
        s.add(
            TaskModel(
                project_id=a.project_id,
                plan_id=plan_id,
                section_id=section_id,
                task_id=task_id,
                title=f"Task {task_id}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                status="todo",
                created=now,
                last_updated=now,
                uid="0199aaaa-0000-7000-8000-000000000001",
            )
        )
        s.commit()


def _secret(a: r.Replica) -> None:
    """Restricted-документ и сценарий на нём: документ не уезжает, ссылка придержана."""
    with transactional(a.sf) as s:
        doc_service.create(
            s,
            project_id=a.project_id,
            doc_key=SECRET_DOC,
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="vault",
            author=r.AUTHOR,
            path=f"docs/{SECRET_DOC}.md",
            sensitivity=Sensitivity.RESTRICTED,
            reindex=False,
        )
        from cod_doc.domain.entities import ScenarioKind

        scenario_service.create(
            s,
            project_id=a.project_id,
            title="тайный",
            kind=ScenarioKind.INVARIANT,
            preconditions="p",
            expected="e",
            author=r.AUTHOR,
            scenario_id="SCN-601",
            doc_key=SECRET_DOC,
            group_key="vault-group",
            steps=["шаг"],
        )


def _seed(a: r.Replica) -> None:
    r.make_task(a, "DEM-001")
    r.make_doc(a, "guide", {"intro": "hello", "usage": "see [intro](#intro)"})
    r.tag_doc(a, "guide", "t1")
    r.set_description(a, "DEM-001", "правка — ревизия")
    _secret(a)


def _rewrite(data: bytes, edit: object) -> bytes:
    """Пересобрать снапшот с правкой строк — валидная строка end, подменённое содержимое."""
    lines = list(snapshot._lines(data))
    lines = edit(lines)  # type: ignore[operator]
    writer = snapshot._Writer(10**10)
    for line in lines:
        writer.line(line)
    return writer.finish()


def _latest(store: FileRemote, a: r.Replica) -> tuple[SnapshotRef, bytes]:
    found = snapshot.pointer(store, _uid(a))
    assert found is not None
    return found[0], store.get_snapshot(_uid(a), found[0])


# --- SCN-082 --------------------------------------------------------------------------


def test_scn_082_clone_restores_remote_state_from_snapshot_and_tail(
    world: tuple[r.Replica, FileRemote], tmp_path: Path
) -> None:
    """SCN-082: на чистой машине clone = снапшот + хвост; verify равен голове remote."""
    a, store = world
    _pre_journal_task(a, "DEM-000")
    _seed(a)
    first = _push(a, store)
    assert first.snapshot == first.head  # снапшота на remote не было — пишется сразу
    r.make_task(a, "DEM-002")
    r.set_description(a, "DEM-000", "правка строки до журнала")
    second = _push(a, store)
    assert second.snapshot is None  # до SNAPSHOT_EVERY операций далеко — хвост

    assert Config.load().get_project(r.SLUG) is None
    result = _clone(store, a, tmp_path / "demo")

    assert (result.project, result.snapshot, result.head) == (r.SLUG, first.head, second.head)
    assert result.applied == [second.head]
    entry = Config.load().get_project(r.SLUG)
    assert entry is not None
    assert entry.root == (tmp_path / "demo").resolve()
    assert Config.load().project_remotes(r.SLUG) == {
        "origin": {"url": store.root.as_uri(), "kind": "file"}
    }
    sf = _factory()
    verified = digest.verify(sf, r.SLUG, store=store)
    assert verified.equal, verified
    b = _as_replica("b")
    # Сценарий на restricted-документе у клона держит заглушки придержанных колонок,
    # у владельца их нет в shipped_fields — его сверяет дайджест выше (§6.3 аудита C).
    mine_state, theirs_state = r.authored_state(b), r.authored_state(a)
    assert mine_state.pop("scenario") and theirs_state.pop("scenario")
    assert mine_state == theirs_state
    with transactional(sf) as s:
        tasks = sorted(s.scalars(select(TaskModel.task_id)))
        docs = sorted(s.scalars(select(DocumentModel.doc_key)))
        revisions = len(s.scalars(select(RevisionModel.revision_id)).all())
        mine = s.scalars(select(ReplicaModel).where(ReplicaModel.is_self)).one()
    assert tasks == ["DEM-000", "DEM-001", "DEM-002"]
    assert docs == ["guide"]  # restricted-документ не уехал
    assert revisions > 0
    with transactional(a.sf) as s:
        theirs = s.scalars(select(ReplicaModel.uid).where(ReplicaModel.is_self)).one()
    assert mine.uid != theirs


def _journal(rep: r.Replica) -> set[tuple[str, str, str | None]]:
    with transactional(rep.sf) as s:
        rows = s.execute(select(SyncOpModel.op_id, SyncOpModel.entity_uid, SyncOpModel.changeset))
        return {(op_id, uid, cs) for op_id, uid, cs in rows}


def _set_title(rep: r.Replica, title: str) -> None:
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        s.scalars(select(TaskModel).where(TaskModel.task_id == "DEM-001")).one().title = title


def _title(rep: r.Replica) -> str:
    with transactional(rep.sf) as s:
        return s.scalars(select(TaskModel.title).where(TaskModel.task_id == "DEM-001")).one()


@pytest.mark.parametrize("later", ["origin", "clone"])
def test_lww_after_clone_matches_two_ordinary_replicas(
    world: tuple[r.Replica, FileRemote], tmp_path: Path, later: str
) -> None:
    """Ревью CLO-029: журнал клона — тот же, что у реплики, проигравшей историю.

    A и клон C правят одно LWW-поле (``title``) одной задачи; B — обычная реплика (вся история
    pull'ом). После обмена у всех трёх побеждает более поздняя запись, дайджесты
    равны голове, а журнал C совпадает с журналом B операция в операцию.
    """
    a, store = world
    _seed(a)
    _push(a, store)
    _clone(store, a, tmp_path / "demo")
    c = _as_replica("c")
    b = r.replica_b(tmp_path, a)
    pull.pull(b.sf, r.SLUG, author=r.AUTHOR, store=store)
    assert _journal(c) == _journal(b)

    first, second = (c, a) if later == "origin" else (a, c)
    _set_title(first, f"раньше: {first.name}")
    time.sleep(0.01)  # HLC второй записи заведомо позже
    _set_title(second, f"позже: {second.name}")
    for rep in (a, c, b, a, c):
        push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)
        pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)

    expected = f"позже: {second.name}"
    assert [_title(x) for x in (a, b, c)] == [expected] * 3
    for rep in (a, b, c):
        assert digest.verify(rep.sf, r.SLUG, store=store).equal, rep.name
    assert _journal(c) == _journal(b)


def test_cloned_replica_pushes_and_the_origin_pulls_it(
    world: tuple[r.Replica, FileRemote], tmp_path: Path
) -> None:
    a, store = world
    _seed(a)
    _push(a, store)
    _clone(store, a, tmp_path / "demo")
    b = _as_replica("b")
    r.make_task(b, "DEM-010")
    pushed = push.push(b.sf, r.SLUG, author=r.AUTHOR, store=store)
    assert pushed.ops > 0
    pulled = pull.pull(a.sf, r.SLUG, author=r.AUTHOR, store=store)
    assert pulled.applied == [pushed.head]
    assert digest.verify(a.sf, r.SLUG, store=store).equal
    assert digest.verify(b.sf, r.SLUG, store=store).equal


# --- каденция -------------------------------------------------------------------------


def test_push_writes_snapshot_every_n_ops_and_moves_the_meta_pointer(
    world: tuple[r.Replica, FileRemote], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, store = world
    monkeypatch.setattr(snapshot, "SNAPSHOT_EVERY", 6)
    first = _push(a, store)
    assert first.snapshot == first.head
    found = snapshot.pointer(store, _uid(a))
    assert found is not None
    assert found[0].key == f"snapshots/{found[0].epoch}/{first.head}.db.zst"
    base_ops = found[1]

    r.make_task(a, "DEM-001")  # меньше 6 операций — снапшот не нужен
    small = _push(a, store)
    assert small.snapshot is None
    for n in range(2, 6):
        r.make_task(a, f"DEM-00{n}")
    big = _push(a, store)
    assert big.snapshot == big.head
    moved = snapshot.pointer(store, _uid(a))
    assert moved is not None
    assert moved[0].sha == big.head
    assert moved[1] - base_ops >= 6
    meta = store.get_json(_uid(a), "meta.json")
    assert meta is not None
    assert meta[0]["epoch"] == found[0].epoch


def test_snapshot_cadence_constant_is_5000() -> None:
    assert snapshot.SNAPSHOT_EVERY == 5000


def test_snapshot_is_not_written_when_state_moved_past_the_head(
    world: tuple[r.Replica, FileRemote], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Снапшот — только с состояния, равного голове: своя запись после push — отказ."""
    a, store = world
    monkeypatch.setattr(snapshot, "SNAPSHOT_EVERY", 1)
    _push(a, store)
    r.make_task(a, "DEM-001")
    pushed = _push(a, store)
    assert pushed.head is not None
    with transactional(a.sf) as s:
        head_digest = s.get_one(ChangesetModel, pushed.head).digest
    snapshot_before = snapshot.pointer(store, _uid(a))
    r.make_task(a, "DEM-002")  # своя запись после фиксации: состояние ≠ голове
    wrote = snapshot.write_if_due(a.sf, r.SLUG, store, head=pushed.head, digest_hex=head_digest)
    assert wrote is None
    assert snapshot.pointer(store, _uid(a)) == snapshot_before


# --- без снапшота ---------------------------------------------------------------------


def test_clone_without_snapshot_replays_the_history_and_needs_the_slug(
    world: tuple[r.Replica, FileRemote], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, store = world
    monkeypatch.setattr(snapshot, "write_quietly", lambda *_a, **_k: None)
    _seed(a)
    head = _push(a, store).head
    assert snapshot.pointer(store, _uid(a)) is None

    with pytest.raises(RemoteConfigError, match="--project"):
        _clone(store, a, tmp_path / "x")
    assert not (tmp_path / "x").exists()
    with pytest.raises(RemoteConfigError, match="называется 'demo', а не 'other'"):
        _clone(store, a, tmp_path / "x", slug="other")
    assert not (tmp_path / "x").exists()
    assert Config.load().get_project("other") is None
    result = _clone(store, a, tmp_path / "demo", slug=r.SLUG)
    assert (result.snapshot, result.head) == (None, head)
    assert digest.verify(_factory(), r.SLUG, store=store).equal


# --- отказы ---------------------------------------------------------------------------


def test_clone_refuses_a_registered_slug_and_an_existing_database(
    world: tuple[r.Replica, FileRemote], tmp_path: Path
) -> None:
    a, store = world
    _push(a, store)
    _clone(store, a, tmp_path / "demo")
    with pytest.raises(RemoteConfigError, match="уже есть в реестре"):
        _clone(store, a, tmp_path / "other")
    Config.load().remove_project(r.SLUG)
    with pytest.raises(RemoteConfigError, match="уже есть"):
        _clone(store, a, tmp_path / "demo")
    assert (tmp_path / "demo" / ".cod-doc" / "state.db").exists()  # чужое не тронуто


def test_clone_refuses_a_slug_other_than_the_remote_one(
    world: tuple[r.Replica, FileRemote], tmp_path: Path
) -> None:
    a, store = world
    _push(a, store)
    with pytest.raises(RemoteConfigError, match="называется 'demo'"):
        _clone(store, a, tmp_path / "x", slug="other")


def test_clone_url_must_name_the_project_directory(tmp_path: Path) -> None:
    with pytest.raises(RemoteConfigError, match="project_uid"):
        clone.split_url("file:///")
    assert clone.split_url(f"{tmp_path.as_uri()}/abc-1") == (tmp_path.as_uri(), "abc-1")
    assert clone.split_url("s3://bucket/pre/fix/u1/") == ("s3://bucket/pre/fix", "u1")


def _replace_snapshot(store: FileRemote, a: r.Replica, data: bytes) -> None:
    ref, _ = _latest(store, a)
    path = store.root / _uid(a) / ref.key
    path.write_bytes(data)


def _assert_nothing_left(root: Path) -> None:
    assert not root.exists()
    assert Config.load().get_project(r.SLUG) is None


def test_tampered_snapshot_row_fails_the_digest_and_leaves_nothing(
    world: tuple[r.Replica, FileRemote], tmp_path: Path
) -> None:
    """Подменённое поле при валидной строке end: эталон — дайджест changeset'а по адресу."""
    a, store = world
    _seed(a)
    _push(a, store)
    _, data = _latest(store, a)

    def edit(lines: list[object]) -> list[object]:
        for line in lines:
            if isinstance(line, list) and line[:2] == ["row", "task"]:
                line[3]["title"] = "подмена"  # type: ignore[index]
        return lines

    _replace_snapshot(store, a, _rewrite(data, edit))
    with pytest.raises(ObjectMissingError, match=r"SYNC-102.*дайджест"):
        _clone(store, a, tmp_path / "demo")
    _assert_nothing_left(tmp_path / "demo")


def _after_last_row(lines: list[object], extra: object) -> list[object]:
    last = max(i for i, ln in enumerate(lines) if isinstance(ln, list) and ln[0] == "row")
    return [*lines[: last + 1], extra, *lines[last + 1 :]]


@pytest.mark.parametrize(
    ("label", "edit", "match"),
    [
        (
            "таблица вне захвата",
            lambda ls: [*ls[:2], ["row", "sync_op", "x", {}], *ls[2:]],
            "не синхронизируется",
        ),
        (
            "колонка не уезжает",
            lambda ls: [
                [*ln[:3], {**ln[3], "position": 7}]
                if isinstance(ln, list) and ln[:2] == ["row", "section"]
                else ln
                for ln in ls
            ],
            "не уезжает",
        ),
        (
            "порядок загрузки",
            lambda ls: _after_last_row(
                ls, next(ln for ln in ls[1:] if ln[:2] == ["row", "document"])
            ),
            "вне порядка загрузки",
        ),
        (
            "слаг — путь",
            lambda ls: [ls[0], [*ls[1][:3], {**ls[1][3], "slug": "../evil"}], *ls[2:]],
            "недопустимый слаг",
        ),
        (
            "чужой проект в заголовке",
            lambda ls: [{**ls[0], "project": "other"}, *ls[1:]],
            "не строка project этого снапшота",
        ),
    ],
)
def test_untrusted_snapshot_lines_are_refused(
    world: tuple[r.Replica, FileRemote],
    tmp_path: Path,
    label: str,
    edit: object,
    match: str,
) -> None:
    a, store = world
    _seed(a)
    _push(a, store)
    _, data = _latest(store, a)
    _replace_snapshot(store, a, _rewrite(data, edit))
    with pytest.raises(ObjectMissingError, match=f"SYNC-102.*{match}"):
        _clone(store, a, tmp_path / "demo")
    _assert_nothing_left(tmp_path / "demo")


_FAKE_SHA = "f" * 64
_FAKE_UID = "0199ffff-0000-7000-8000-00000000dead"


def _after_last(lines: list[object], kinds: tuple[str, ...], extra: object) -> list[object]:
    """Вставить строку после последней строки одного из видов — по формату любой версии."""
    last = max(i for i, ln in enumerate(lines) if isinstance(ln, list) and ln[0] in kinds)
    return [*lines[: last + 1], extra, *lines[last + 1 :]]


def _task_uid(lines: list[object]) -> str:
    return next(str(ln[2]) for ln in lines[1:] if ln[:2] == ["row", "task"])  # type: ignore[index]


def _forged_op(lines: list[object]) -> list[object]:
    """Подложенная операция журнала: свежий штамп чужой реплики над живой задачей."""
    op = {
        "op_id": "01JFORGEDOPFORGEDOPFORGED0",
        "hlc": f"{wall_ms():013d}.0000.{_FAKE_UID}",
        "entity_kind": "task",
        "entity_uid": _task_uid(lines),
        "kind": "upsert",
        "fields": {"title": "подлог"},
        "base": {"title": "Task DEM-001"},
        "author": "human:mallory",
    }
    return _after_last(lines, ("row", "op"), ["op", _FAKE_SHA, op])


def _forged_changeset(lines: list[object]) -> list[object]:
    """Подложенная запись индекса: чужой sha стал бы «применённым» — pull его пропустит."""
    entry = {
        "sha": _FAKE_SHA,
        "parents": [],
        "replica": _FAKE_UID,
        "actor": "mallory",
        "epoch": "0050_sync_rank",
        "hlc_max": f"{wall_ms():013d}.0000.{_FAKE_UID}",
        "digest": "0" * 64,
    }
    return _after_last(lines, ("row", "op", "changeset"), ["changeset", entry])


def _forged_alias(lines: list[object]) -> list[object]:
    """Подложенный алиас: операции над выдуманным uid ушли бы в живую задачу."""
    alias = {"entity_kind": "task", "alias": _FAKE_UID, "uid": _task_uid(lines), "reason": "merged"}
    return [*lines, ["alias", alias]]


@pytest.mark.parametrize(
    "forge",
    [_forged_op, _forged_changeset, _forged_alias],
    ids=["op", "changeset-index", "alias"],
)
def test_forged_history_in_a_snapshot_is_refused_and_nothing_is_registered(
    world: tuple[r.Replica, FileRemote], tmp_path: Path, forge: object
) -> None:
    """Ревью CLO-029: журнал, индекс changeset'ов и алиасы снапшоту не доверяются.

    Строки и дайджест у подлога верные — состояние совпадает с головой; врут только
    секции, которые дайджест не покрывает. Clone обязан отказать (SYNC-102), убрать
    недособранную БД и не регистрировать проект.
    """
    a, store = world
    _seed(a)
    _push(a, store)
    _, data = _latest(store, a)
    _replace_snapshot(store, a, _rewrite(data, forge))
    with pytest.raises(ObjectMissingError, match="SYNC-102"):
        _clone(store, a, tmp_path / "demo")
    _assert_nothing_left(tmp_path / "demo")


def test_history_is_read_by_address_and_a_broken_object_fails_the_clone(
    world: tuple[r.Replica, FileRemote], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Журнал клона — из changeset'ов по адресам: подменённый старый объект — SYNC-102."""
    a, store = world
    monkeypatch.setattr(snapshot, "SNAPSHOT_EVERY", 1)
    root = _push(a, store).head
    r.make_task(a, "DEM-001")
    assert _push(a, store).snapshot is not None  # снапшот на второй голове
    assert root is not None
    obj = store.root / _uid(a) / "objects" / root[:2] / f"{root[2:]}.zst"
    obj.write_bytes(obj.read_bytes()[:-1] + b"x")
    with pytest.raises(ObjectMissingError, match="SYNC-102"):
        _clone(store, a, tmp_path / "demo")
    _assert_nothing_left(tmp_path / "demo")


def test_truncated_or_padded_snapshot_is_refused(
    world: tuple[r.Replica, FileRemote], tmp_path: Path
) -> None:
    a, store = world
    _seed(a)
    _push(a, store)
    _, data = _latest(store, a)
    for broken in (data[: len(data) // 2], data + b"x", b"XXXX" + data[4:]):
        with pytest.raises(ObjectMissingError, match="SYNC-102"):
            list(snapshot._lines(broken))
    # Кадр цельный, но обрезан по строкам — без строки end: снапшот не загрузится.
    raw = zstd.decompress(data[4:])
    cut = raw.rstrip(b"\n").rfind(b"\n") + 1
    with pytest.raises(ObjectMissingError, match="нет строки end"):
        list(snapshot._lines(b"CDS1" + zstd.compress(raw[:cut])))


def test_line_over_the_cap_is_refused(
    world: tuple[r.Replica, FileRemote], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, store = world
    _seed(a)
    _push(a, store)
    _, data = _latest(store, a)
    monkeypatch.setattr(snapshot, "MAX_LINE_BYTES", 64)
    with pytest.raises(ObjectMissingError, match="больше 64 байт"):
        list(snapshot._lines(data))


def test_oversized_snapshot_is_not_written_and_push_survives(
    world: tuple[r.Replica, FileRemote], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, store = world
    _seed(a)
    monkeypatch.setattr(snapshot, "MAX_SNAPSHOT_BYTES", 16)
    result = _push(a, store)
    assert result.head is not None
    assert result.snapshot is None
    assert snapshot.pointer(store, _uid(a)) is None


# --- состав снапшота ------------------------------------------------------------------


def test_snapshot_carries_no_restricted_rows_and_no_withheld_columns(
    world: tuple[r.Replica, FileRemote],
) -> None:
    a, store = world
    _seed(a)
    _push(a, store)
    _, data = _latest(store, a)
    lines = list(snapshot._lines(data))
    rows = [ln for ln in lines if isinstance(ln, list) and ln[0] == "row"]
    docs = [ln[3]["doc_key"] for ln in rows if ln[1] == "document"]
    assert docs == ["guide"]
    scenarios = [ln[3] for ln in rows if ln[1] == "scenario"]
    assert len(scenarios) == 1
    withheld = {"doc_key", "document_id", "section_anchor", "doc_content_hash", "group_key"}
    assert not withheld & set(scenarios[0])
    # Журнал и индекс changeset'ов снапшот не несёт: дайджест их не покрывает,
    # клон собирает их из changeset'ов по адресам.
    kinds = {ln[0] for ln in lines[1:] if isinstance(ln, list)}
    assert kinds <= {"row", "alias"}
    header = lines[0]
    assert isinstance(header, dict)
    assert header["kind"] == "header"


def test_load_order_puts_every_not_null_reference_before_its_row() -> None:
    order = snapshot.load_order()
    assert order[0] == "project"
    assert order[-1] == "revision"
    assert set(order) == cap.CAPTURED_TABLES
    pos = {t: i for i, t in enumerate(order)}
    for table in order:
        info = snapshot._info(table)
        for column, target in info.fk_targets.items():
            if column not in info.nullable and target != table:
                assert pos[target] < pos[table], (table, column, target)
        if snapshot.deferred_columns()[table]:
            assert info.has_uid, table  # второй проход ищет строку по uid
    assert all(t in reg.UID_TABLES or not snapshot.deferred_columns()[t] for t in order)


def test_snapshot_bytes_round_trip_through_the_transport(
    world: tuple[r.Replica, FileRemote],
) -> None:
    a, store = world
    for n in range(3):
        r.make_task(a, f"DEM-10{n}")
    _push(a, store)
    ref, data = _latest(store, a)
    header, project_fields = snapshot.peek(data)
    assert header.head == ref.sha
    assert project_fields["slug"] == r.SLUG
