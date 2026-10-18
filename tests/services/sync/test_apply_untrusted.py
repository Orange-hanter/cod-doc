"""CLO-013: changeset с remote — недоверенный ввод (security review PR B2).

Hub-БД держит несколько проектов, а uid, FK, поля и штампы changeset'а собирает
тот, кто пишет в remote. Каждый тест кладёт на remote поддельный changeset поверх
честной истории и проверяет: pull отказывает с SYNC-102 до первой записи,
состояние реплики не меняется, часы не уходят вперёд.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from compression import zstd
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.infra import sync_capture as cap
from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import MAX_DRIFT_MS, Hlc, HybridClock, clock_for, self_replica, wall_ms
from cod_doc.infra.models import (
    AffectedFileModel,
    ChangesetModel,
    DocumentModel,
    DocumentTagModel,
    ProjectModel,
    SyncConflictModel,
    SyncOpModel,
    TagModel,
    TaskModel,
)
from cod_doc.services import doc_service
from cod_doc.services.sync_service import _local, apply, pull, push
from cod_doc.services.sync_service.changeset import decode
from cod_doc.services.sync_service.errors import (
    ObjectMissingError,
    RemoteConfigError,
    SchemaMismatchError,
)
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

_DAY_MS = 24 * 60 * 60 * 1000


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    store = r.remote(tmp_path)
    r.make_task(a, "DEM-001")
    r.make_doc(a, "spec", {"intro": "hello"})
    push.push(a.sf, r.SLUG, author=r.AUTHOR, store=store)
    pull.pull(b.sf, r.SLUG, author=r.AUTHOR, store=store)
    return a, b, store


def _uid(rep: r.Replica, model: Any, **where: object) -> str:
    with transactional(rep.sf) as s:
        stmt = select(model.uid)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return str(s.scalars(stmt).one())


def _forge(
    rep: r.Replica,
    store: FileRemote,
    ops: list[dict[str, Any]],
    *,
    replica: str | None = None,
    op_replica: str | None = None,
    ahead_ms: int = 1,
    hlc_max: str | None = None,
) -> str:
    """Положить на remote changeset поверх головы, минуя проверки ``Changeset``."""
    project_uid = _uid(rep, ProjectModel, row_id=rep.project_id)
    head = store.get_ref(project_uid, "main")
    assert head is not None
    parent = decode(store.get_object(project_uid, head.sha), head.sha)
    replica = replica or parent.replica
    ms = max(wall_ms(), Hlc.parse(parent.hlc_max).ms) + ahead_ms
    full = []
    for i, op in enumerate(ops):
        stamp = str(Hlc(ms, i, op_replica or replica))
        full.append(
            {
                "op_id": f"01K7Z00000000000000000{i:04d}",
                "hlc": stamp,
                "kind": "upsert",
                "base": {},
                "author": r.AUTHOR,
                **op,
            }
        )
    body = {
        "v": 1,
        "project": project_uid,
        "parents": [head.sha],
        "replica": replica,
        "actor": r.AUTHOR,
        "epoch": "0050_sync_rank",
        "schema_head": "0050_sync_rank",
        "hlc_max": hlc_max or max(op["hlc"] for op in full),
        "digest": "0" * 64,
        "ops": sorted(full, key=lambda op: (op["hlc"], op["op_id"])),
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    address = hashlib.sha256(raw).hexdigest()
    store.put_object(project_uid, address, b"CDC1" + zstd.compress(raw))
    assert store.cas_ref(project_uid, "main", head, address) is not None
    return address


def _refused(b: r.Replica, store: FileRemote, match: str) -> None:
    before = r.authored_state(b)
    with transactional(b.sf) as s:
        applied = s.scalars(select(ChangesetModel.sha)).all()
    with pytest.raises(ObjectMissingError, match=match) as err:
        pull.pull(b.sf, r.SLUG, author=r.AUTHOR, store=store)
    assert err.value.code == "SYNC-102"
    assert r.authored_state(b) == before
    with transactional(b.sf) as s:
        assert s.scalars(select(ChangesetModel.sha)).all() == applied


def _intruder(b: r.Replica) -> tuple[str, str]:
    """Второй проект в той же БД (hub) с тегом: (uid проекта, uid тега)."""
    project_uid, tag_uid = str(uuid.uuid7()), str(uuid.uuid7())
    with Session(b.engine) as s:
        now = datetime.now(UTC)
        proj = ProjectModel(
            slug="intruder", title="I", root_path="/tmp/i", config_json={}, uid=project_uid
        )
        proj.created = now
        proj.updated = now
        s.add(proj)
        s.flush()
        s.add(TagModel(project_id=proj.row_id, name="mine", uid=tag_uid))
        s.commit()
    return project_uid, tag_uid


def _intruder_tag(b: r.Replica) -> tuple[str, int]:
    with Session(b.engine) as s:
        tag = s.scalars(select(TagModel).where(TagModel.name.in_(["mine", "pwned"]))).one()
        return tag.name, tag.project_id


# --- проект ------------------------------------------------------------------------


def test_uid_of_another_projects_row_is_refused(pair: tuple) -> None:
    a, b, store = pair
    _, tag_uid = _intruder(b)
    owner = _intruder_tag(b)[1]
    own = _uid(b, ProjectModel, row_id=b.project_id)
    _forge(
        a,
        store,
        [{"entity_kind": "tag", "entity_uid": tag_uid, "fields": {"name": "pwned"}}],
    )

    _refused(b, store, "другому проекту")

    assert _intruder_tag(b) == ("mine", owner)
    assert own  # проект pull'а свой


def test_fk_to_another_projects_row_is_refused(pair: tuple) -> None:
    a, b, store = pair
    _, tag_uid = _intruder(b)
    doc_uid = _uid(b, DocumentModel, doc_key="spec")
    _forge(
        a,
        store,
        [
            {
                "entity_kind": "document_tag",
                "entity_uid": str(uuid.uuid7()),
                "fields": {"document_id": doc_uid, "tag_id": tag_uid},
            }
        ],
    )

    _refused(b, store, "другому проекту")


def test_project_id_of_another_project_is_refused(pair: tuple) -> None:
    a, b, store = pair
    intruder_uid, _ = _intruder(b)
    _forge(
        a,
        store,
        [
            {
                "entity_kind": "tag",
                "entity_uid": str(uuid.uuid7()),
                "fields": {"name": "planted", "project_id": intruder_uid},
            }
        ],
    )

    _refused(b, store, "другому проекту")
    with Session(b.engine) as s:
        assert s.scalars(select(TagModel).where(TagModel.name == "planted")).all() == []


# --- поля и таблицы ------------------------------------------------------------------


def test_local_column_in_fields_is_refused(pair: tuple) -> None:
    a, b, store = pair
    own = _uid(b, ProjectModel, row_id=b.project_id)
    _forge(
        a, store, [{"entity_kind": "project", "entity_uid": own, "fields": {"root_path": "/evil"}}]
    )

    _refused(b, store, "root_path: колонка не уезжает")
    with Session(b.engine) as s:
        assert s.get_one(ProjectModel, b.project_id).root_path == "/tmp/b"


def test_op_for_a_local_table_is_refused(pair: tuple) -> None:
    """Не красный до правки: таблицы вне захвата отвергались и раньше."""
    a, b, store = pair
    _forge(
        a,
        store,
        [{"entity_kind": "sync_ref", "entity_uid": str(uuid.uuid7()), "fields": {"sha": "x"}}],
    )

    _refused(b, store, "не синхронизируется")


def test_row_under_a_locally_restricted_document_is_refused(pair: tuple) -> None:
    a, b, store = pair
    with Session(b.engine) as s:  # restricted-документ только на B
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
        secret_uid = str(uuid.uuid7())
        s.get_one(DocumentModel, doc.row_id).uid = secret_uid
        s.commit()
    _forge(
        a,
        store,
        [
            {
                "entity_kind": "section",
                "entity_uid": str(uuid.uuid7()),
                "fields": {
                    "anchor": "leak",
                    "body": "planted",
                    "content_hash": "0" * 64,
                    "document_id": secret_uid,
                    "heading": "Leak",
                    "level": 2,
                    "rank": "m",
                },
            }
        ],
    )

    _refused(b, store, "local на этой реплике")


# --- часы ----------------------------------------------------------------------------


def _task_op(b: r.Replica) -> dict[str, Any]:
    task_uid = _uid(b, TaskModel, task_id="DEM-001")
    return {
        "entity_kind": "task",
        "entity_uid": task_uid,
        "fields": {"description": "из будущего"},
        "base": {"description": "old"},
    }


def _clock(b: r.Replica) -> Hlc:
    with transactional(b.sf) as s:
        return clock_for(s, self_replica(s, b.project_id)).now()


def test_far_future_hlc_is_refused_and_clock_stays(pair: tuple) -> None:
    a, b, store = pair
    _forge(a, store, [_task_op(b)], ahead_ms=_DAY_MS)

    _refused(b, store, "впереди стенных часов")

    assert _clock(b).ms <= wall_ms() + MAX_DRIFT_MS


def test_hlc_max_mismatch_is_refused(pair: tuple) -> None:
    """Не красный до правки: ``Changeset`` сверяет hlc_max с B1 (решение B5)."""
    a, b, store = pair
    stamp = str(Hlc(wall_ms() + 10, 0, str(uuid.uuid7())))
    _forge(a, store, [_task_op(b)], hlc_max=stamp)

    _refused(b, store, "hlc_max")


def test_op_stamp_of_another_replica_is_refused(pair: tuple) -> None:
    a, b, store = pair
    _forge(a, store, [_task_op(b)], op_replica=str(uuid.uuid7()))

    _refused(b, store, "реплика штампа")


def test_changeset_impersonating_this_replica_is_refused(pair: tuple) -> None:
    a, b, store = pair
    with transactional(b.sf) as s:
        me = self_replica(s, b.project_id)
    _forge(a, store, [_task_op(b)], replica=me)

    _refused(b, store, "uid этой реплики")


def test_observe_never_moves_the_clock_past_the_drift_ceiling() -> None:
    clock = HybridClock("me", wall_ms=lambda: 1_000)

    seen = clock.observe(Hlc(1_000 + _DAY_MS, 7, "remote"))

    assert seen.ms <= 1_000 + MAX_DRIFT_MS
    assert clock.now().ms <= 1_000 + MAX_DRIFT_MS


# --- каскад удаления ---------------------------------------------------------------


def test_every_schema_cascade_from_a_shipped_table_is_classified() -> None:
    """Новая каскадная FK роняет тест, пока её не классифицируют в CASCADE_PATHS."""
    edges = {
        edge
        for edge in apply.cascade_edges()
        if edge[0] != "project" and edge[0] in cap.CAPTURED_TABLES
    }

    assert edges == set(apply.CASCADE_PATHS)
    for (_, child, _), kind in apply.CASCADE_PATHS.items():
        assert (kind == apply.GATED) == apply.is_gated(child), child


def _restricted_doc(b: r.Replica) -> int:
    with Session(b.engine) as s:  # restricted-документ только на B, мимо захвата
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
        s.get_one(DocumentModel, doc.row_id).uid = str(uuid.uuid7())
        s.commit()
        return doc.row_id


def _delete_on_a(a: r.Replica, model: Any, **where: object) -> None:
    with transactional(a.sf) as s:
        cap.set_author(s, r.AUTHOR)
        stmt = select(model)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        s.delete(s.scalars(stmt).one())


def test_delete_cascading_into_a_restricted_row_is_refused(pair: tuple) -> None:
    a, b, store = pair
    r.tag_doc(a, "spec", "shared")
    push.push(a.sf, r.SLUG, author=r.AUTHOR, store=store)
    pull.pull(b.sf, r.SLUG, author=r.AUTHOR, store=store)
    secret = _restricted_doc(b)
    with Session(b.engine) as s:  # тег на restricted-документе — local на B
        tag = s.scalars(select(TagModel).where(TagModel.name == "shared")).one()
        s.add(DocumentTagModel(document_id=secret, tag_id=tag.row_id))
        s.commit()
    _delete_on_a(a, TagModel, name="shared")
    push.push(a.sf, r.SLUG, author=r.AUTHOR, store=store)

    with pytest.raises(ObjectMissingError, match="держит local") as err:
        pull.pull(b.sf, r.SLUG, author=r.AUTHOR, store=store)

    assert err.value.code == "SYNC-102"
    with Session(b.engine) as s:
        assert s.scalars(select(TagModel).where(TagModel.name == "shared")).one()
        assert len(s.scalars(select(DocumentTagModel)).all()) == 2


def test_delete_of_a_task_takes_its_local_affected_file_along(pair: tuple) -> None:
    a, b, store = pair
    with Session(b.engine) as s:  # машинный путь — local на B, принадлежит задаче
        task = s.scalars(select(TaskModel).where(TaskModel.task_id == "DEM-001")).one()
        s.add(AffectedFileModel(task_id=task.row_id, path="/Users/b/src/x.py", kind="source"))
        s.commit()
    _delete_on_a(a, TaskModel, task_id="DEM-001")
    push.push(a.sf, r.SLUG, author=r.AUTHOR, store=store)

    result = pull.pull(b.sf, r.SLUG, author=r.AUTHOR, store=store)

    assert len(result.applied) == 1
    assert r.task_description(b, "DEM-001") is None
    with Session(b.engine) as s:
        assert s.scalars(select(AffectedFileModel)).all() == []


# --- «свой» changeset ----------------------------------------------------------------


def test_forged_changeset_reusing_a_local_op_id_is_not_taken_as_own(pair: tuple) -> None:
    """op_id своей неотправленной операции + чужое содержимое — подделка, не «свой»."""
    a, b, store = pair
    r.set_description(a, "DEM-001", "правка A, ещё не отправлена")
    with transactional(a.sf) as s:
        mine = s.scalars(select(SyncOpModel.op_id).where(SyncOpModel.changeset.is_(None))).all()
        me = self_replica(s, a.project_id)
    assert mine
    task_uid = _uid(b, TaskModel, task_id="DEM-001")
    _forge(
        a,
        store,
        [
            {
                "op_id": op_id,
                "entity_kind": "task",
                "entity_uid": task_uid,
                "fields": {"description": "подмена"},
                "base": {"description": "old"},
            }
            for op_id in mine
        ],
        replica=me,
    )
    tail = r.pending(a)

    with pytest.raises(ObjectMissingError, match="подделка") as err:
        pull.pull(a.sf, r.SLUG, author=r.AUTHOR, store=store)

    assert err.value.code == "SYNC-102"
    assert r.pending(a) == tail  # хвост A не помечен отправленным
    assert r.task_description(a, "DEM-001") == "правка A, ещё не отправлена"


def test_malformed_time_in_a_foreign_op_is_sync_102(pair: tuple) -> None:
    a, b, store = pair
    op = _task_op(b)
    op["fields"] = {"last_updated": "вчера вечером"}
    _forge(a, store, [op])

    _refused(b, store, "не время ISO-8601")


def test_unknown_local_schema_fails_closed() -> None:
    with pytest.raises(SchemaMismatchError, match="незнакома коду"):
        _local.schema_is_newer("0050_sync_rank", "0000_not_a_revision")
    assert _local.schema_is_newer("0099_from_the_future", "0050_sync_rank") is True
    assert _local.schema_is_newer("0049_sync_identity", "0050_sync_rank") is False


@pytest.mark.parametrize(
    ("url", "ok"),
    [("file:///tmp/remote", True), ("https://sync.example", False), ("s3://b/p", False)],
)
def test_open_store_allows_only_implemented_schemes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str, ok: bool
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("COD_DOC_HOME", str(home))
    entry = {"name": "demo", "path": str(tmp_path), "remotes": {"origin": {"url": url}}}
    (home / "config.yaml").write_text(json.dumps({"projects": [entry]}))
    if ok:
        assert _local.open_store("demo", "origin") is not None
        return
    with pytest.raises(RemoteConfigError) as err:
        _local.open_store("demo", "origin")
    assert err.value.code == "SYNC-110"


# --- маркер разрешения (CLO-045) -------------------------------------------------------


def _marker_op(fields: dict[str, Any], **op: Any) -> dict[str, Any]:
    return {
        "entity_kind": "sync_resolution",
        "entity_uid": str(uuid.uuid7()),
        "fields": fields,
        **op,
    }


def _signature(b: r.Replica) -> dict[str, Any]:
    return {
        "entity_kind": "task",
        "entity_uid": _uid(b, TaskModel, task_id="DEM-001"),
        "field": "status",
        "kind": "status",
    }


@pytest.mark.parametrize(
    ("op", "fields", "match"),
    [
        ({"kind": "delete"}, {}, "только дописывается"),
        ({"base": {"kind": "status"}}, {}, "только дописывается"),
        ({"entity_uid": "not-a-uuid"}, {}, "не UUID"),
        ({}, {"extra": 1}, "поля"),
        ({}, {"entity_kind": "sync_conflict"}, "не захватывается"),
        ({}, {"entity_kind": "revision"}, "журнал"),
        ({}, {"kind": "pwned"}, "вид конфликта"),
        ({}, {"field": "row_id"}, "не уезжает"),
        ({}, {"field": None}, "не уезжает"),
        ({}, {"kind": "delete_edit"}, "поля нет"),
        ({}, {"entity_uid": ""}, "непустая строка"),
    ],
)
def test_malformed_resolution_marker_is_refused(
    pair: tuple, op: dict[str, Any], fields: dict[str, Any], match: str
) -> None:
    a, b, store = pair
    _forge(a, store, [_marker_op(_signature(b) | fields, **op)])

    _refused(b, store, match)


def test_marker_without_a_signature_field_is_refused(pair: tuple) -> None:
    a, b, store = pair
    signature = _signature(b)
    del signature["field"]
    _forge(a, store, [_marker_op(signature)])

    _refused(b, store, "поля")


def test_marker_naming_a_locally_restricted_row_is_refused(pair: tuple) -> None:
    a, b, store = pair
    row_id = _restricted_doc(b)
    with Session(b.engine) as s:
        secret_uid = str(s.get_one(DocumentModel, row_id).uid)
    signature = {"entity_kind": "document", "entity_uid": secret_uid}
    _forge(a, store, [_marker_op(signature | {"field": "title", "kind": "field"})])

    _refused(b, store, "local на этой реплике")


def test_marker_naming_a_row_of_another_project_is_refused(pair: tuple) -> None:
    """Hub: маркер с подписью строки чужого проекта этой БД — SYNC-102, её конфликт открыт."""
    a, b, store = pair
    intruder_uid, tag_uid = _intruder(b)
    with Session(b.engine) as s:
        project_id = s.scalars(
            select(ProjectModel.row_id).where(ProjectModel.uid == intruder_uid)
        ).one()
        s.add(
            SyncConflictModel(
                uid=str(uuid.uuid7()),
                project_id=project_id,
                entity_kind="tag",
                entity_uid=tag_uid,
                field="name",
                kind="field",
                ours='"a"',
                theirs='"b"',
                base='"x"',
                theirs_op="01K7Z000000000000000009999",
                status="open",
            )
        )
        s.commit()
    signature = {"entity_kind": "tag", "entity_uid": tag_uid, "field": "name", "kind": "field"}
    _forge(a, store, [_marker_op(signature)])

    _refused(b, store, "другому проекту")
    with Session(b.engine) as s:
        assert s.scalars(select(SyncConflictModel.status)).all() == ["open"]
