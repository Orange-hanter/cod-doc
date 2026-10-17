"""CLO-024: чужие ревизии поверх локальной головы (RFC 29 §3.7, Q-010).

Acceptance: «Ревизия из чужого changeset встаёт со своим revision_id, author,
reason, parent_revision_id = локальная голова; doc_patch_section с устаревшим
expected_parent_revision_id получает OptimisticLockError;
approval_doc_revision_link валиден на обеих репликах.»

Журнал ``revision`` уезжает операциями (``sync_capture``) и применяется
объединением по ``revision_id`` (``sync_service.journal``). Дайджест считает
состав ревизий, но не ``parent_revision_id``: порядок применения на репликах
может различаться.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, update

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    Priority,
    ScenarioKind,
    Sensitivity,
)
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import transactional
from cod_doc.infra.models import (
    ApprovalDocRevisionLinkModel,
    DocumentModel,
    RevisionModel,
    ScenarioModel,
    SectionModel,
    SyncOpModel,
    TaskModel,
)
from cod_doc.services import approval_service, doc_service, scenario_service, task_service
from cod_doc.services import revision_service as rev
from cod_doc.services.sync_service import digest, pull, push
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

TASK = "DEM-001"
DOC = "spec"
_TICK = 0.003  # больше миллисекунды HLC и ULID


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    return a, r.replica_b(tmp_path, a), r.remote(tmp_path)


def _push(rep: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _revisions(rep: r.Replica) -> dict[str, tuple[str, str | None, str | None]]:
    """revision_id → (author, reason, parent_revision_id) всех ревизий проекта."""
    with transactional(rep.sf, commit=False) as s:
        rows = s.scalars(select(RevisionModel).where(RevisionModel.project_id == rep.project_id))
        return {m.revision_id: (m.author, m.reason, m.parent_revision_id) for m in rows}


def _task_row_id(rep: r.Replica) -> int:
    with transactional(rep.sf, commit=False) as s:
        return s.scalars(select(TaskModel.row_id).where(TaskModel.task_id == TASK)).one()


def _task_head(rep: r.Replica) -> str | None:
    with transactional(rep.sf, commit=False) as s:
        return rev.head_for_entity(s, EntityKind.TASK, _task_row_id(rep), project_id=rep.project_id)


def _section(rep: r.Replica, anchor: str = "intro") -> tuple[int, int]:
    """(document.row_id, section.row_id) секции ``anchor`` документа DOC."""
    doc_id = r.doc_id(rep, DOC)
    with transactional(rep.sf, commit=False) as s:
        sec = s.scalars(
            select(SectionModel.row_id).where(
                SectionModel.document_id == doc_id, SectionModel.anchor == anchor
            )
        ).one()
        return doc_id, sec


def _section_head(rep: r.Replica) -> str | None:
    _doc, sec = _section(rep)
    with transactional(rep.sf, commit=False) as s:
        return rev.head_for_entity(s, EntityKind.SECTION, sec, project_id=rep.project_id)


def _patch(rep: r.Replica, body: str, *, expected: object = rev.NO_PARENT_CHECK) -> None:
    doc_id, _sec = _section(rep)
    with transactional(rep.sf) as s:
        doc_service.patch_section(
            s,
            project_id=rep.project_id,
            document_id=doc_id,
            anchor="intro",
            new_body=body,
            author=r.AUTHOR,
            expected_parent_revision_id=expected,
            reindex=False,
        )


def test_foreign_revision_keeps_identity_and_chains_on_local_head(pair) -> None:
    """Чужая ревизия: свои revision_id/author/reason, parent — голова реплики B."""
    a, b, store = pair
    r.make_task(a, TASK)
    _push(a, store)
    _pull(b, store)
    created = _task_head(a)
    assert created is not None
    assert _task_head(b) == created, "ревизия создания задачи не приехала с changeset'ом"

    # A правит описание раньше, B — приоритет позже: у B голова новее ревизии A.
    time.sleep(_TICK)
    with transactional(a.sf) as s:
        task_service.update_description(
            s,
            project_id=a.project_id,
            task_id=TASK,
            new_description="с реплики A",
            author="human:alice",
            reason="уточнение A",
        )
    ra = _task_head(a)
    time.sleep(_TICK)
    with transactional(b.sf) as s:
        task_service.update_priority(
            s,
            project_id=b.project_id,
            task_id=TASK,
            new_priority=Priority.HIGH,
            author="human:bob",
        )
    rb = _task_head(b)
    assert ra is not None and rb is not None and ra != rb
    _push(a, store)
    _pull(b, store)

    got = _revisions(b)
    assert got[ra] == ("human:alice", "уточнение A", rb), (
        "чужая ревизия встаёт со своим автором и причиной поверх локальной головы"
    )
    assert _task_head(b) == ra, "голова — последняя применённая, а не самая поздняя по at"

    # Обратный путь: ревизия B на A встаёт поверх головы A (ra), история линейна.
    _push(b, store)
    _pull(a, store)
    assert _revisions(a)[rb][2] == ra
    assert _revisions(a).keys() == _revisions(b).keys()
    with transactional(a.sf, commit=False) as sa, transactional(b.sf, commit=False) as sb:
        assert digest.compute(sa, a.project_id) == digest.compute(sb, b.project_id)


def test_stale_expected_parent_after_pull_raises_optimistic_lock(pair) -> None:
    """Клиент B видел голову секции до pull: его правка — OptimisticLockError."""
    a, b, store = pair
    r.make_doc(a, DOC, {"intro": "v1\n"})
    _push(a, store)
    _pull(b, store)
    seen = _section_head(b)
    assert seen is not None and seen == _section_head(a)

    time.sleep(_TICK)
    _patch(a, "v2 с реплики A\n")
    _push(a, store)
    _pull(b, store)
    assert _section_head(b) == _section_head(a)

    with pytest.raises(rev.OptimisticLockError):
        _patch(b, "v3 с реплики B\n", expected=seen)
    _patch(b, "v3 с реплики B\n", expected=_section_head(b))


def test_approval_doc_revision_link_resolves_on_both_replicas(pair) -> None:
    a, b, store = pair
    r.make_doc(a, DOC, {"intro": "v1\n"})
    head = _section_head(a)
    assert head is not None
    with transactional(a.sf) as s:
        approval_service.request(
            s,
            a.project_id,
            approval_type="plan_review",
            requested_by=r.AUTHOR,
            linked_doc_revision_ids=[head],
        )
    _push(a, store)
    _pull(b, store)

    with transactional(b.sf, commit=False) as s:
        link = s.scalars(select(ApprovalDocRevisionLinkModel.revision_id)).one()
        kind = s.scalars(
            select(RevisionModel.entity_kind).where(
                RevisionModel.revision_id == link, RevisionModel.project_id == b.project_id
            )
        ).one()
    assert link == head
    assert kind == EntityKind.SECTION.value


def _restricted_doc(rep: r.Replica, doc_key: str = "secret") -> int:
    """Restricted-документ с секцией (ревизии DOCUMENT и SECTION); вернуть row_id."""
    with transactional(rep.sf) as s:
        doc = doc_service.create(
            s,
            project_id=rep.project_id,
            doc_key=doc_key,
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title=doc_key,
            author=r.AUTHOR,
            path=f"docs/{doc_key}.md",
            sensitivity=Sensitivity.RESTRICTED,
            reindex=False,
        )
        doc_service.add_section(
            s,
            project_id=rep.project_id,
            document_id=doc.row_id,
            anchor="body",
            heading="Body",
            level=2,
            position=0,
            body="тайна\n",
            author=r.AUTHOR,
            reindex=False,
        )
        return doc.row_id


def _revision_ops(rep: r.Replica) -> list[dict[str, object]]:
    with transactional(rep.sf, commit=False) as s:
        rows = s.scalars(select(SyncOpModel).where(SyncOpModel.entity_kind == "revision"))
        return [json.loads(row.fields) for row in rows]


def _local_kinds(rep: r.Replica) -> set[str]:
    with transactional(rep.sf, commit=False) as s:
        return set(s.scalars(select(RevisionModel.entity_kind)))


def test_restricted_document_and_section_revisions_never_ship(pair) -> None:
    a, b, store = pair
    _restricted_doc(a)
    assert _local_kinds(a) >= {EntityKind.DOCUMENT.value, EntityKind.SECTION.value}
    assert _revision_ops(a) == []
    r.make_task(a, TASK)
    _push(a, store)
    _pull(b, store)
    assert _local_kinds(b) == {EntityKind.TASK.value}


def test_revision_of_entity_with_withheld_restricted_anchor_never_ships(pair) -> None:
    """Сценарий на restricted-документе уезжает без doc_key — его ревизия не уезжает вовсе.

    diff ревизии создания повторяет doc_key и якорь, которые реестр у самого
    сценария придерживает (security review CLO-024).
    """
    a, _b, _store = pair
    _restricted_doc(a)
    with transactional(a.sf) as s:
        scenario_service.create(
            s,
            project_id=a.project_id,
            title="Сценарий на тайном документе",
            kind=ScenarioKind.INVARIANT,
            preconditions="p",
            expected="e",
            author=r.AUTHOR,
            scenario_id="SCN-001",
            doc_key="secret",
            steps=["шаг"],
            group_key="g",  # по умолчанию — basename doc_key (см. отчёт CLO-024)
        )
        scenario_service.update(
            s,
            project_id=a.project_id,
            scenario_id="SCN-001",
            author=r.AUTHOR,
            section_anchor="body",
        )
    assert EntityKind.SCENARIO.value in _local_kinds(a)
    ops = _revision_ops(a)
    assert [op for op in ops if op["entity_kind"] == EntityKind.SCENARIO.value] == []
    assert [op for op in ops if "secret" in json.dumps(op, ensure_ascii=False)] == []
    with transactional(a.sf, commit=False) as s:
        shipped = s.scalars(
            select(SyncOpModel.fields).where(SyncOpModel.entity_kind == "scenario")
        ).all()
    assert shipped and all("secret" not in f for f in shipped), "сам сценарий — без doc_key"


@pytest.mark.parametrize("group_key", [None, "explicit-group"])
def test_group_key_of_a_scenario_on_a_restricted_document_never_ships(
    pair, group_key: str | None
) -> None:
    """group_key по умолчанию — basename doc_key: у restricted-документа он выдавал имя,
    которое реестр придерживает (находка #226). Fail-closed: пока ссылка на
    документ придержана, group_key не уезжает, каким бы он ни был; приёмник
    ставит заглушку ``sync_registry.RESTRICTED_GROUP_KEY`` в NOT NULL-колонку.
    """
    a, b, store = pair
    _restricted_doc(a, "secret/spec-alpha")
    with transactional(a.sf) as s:
        scenario_service.create(
            s,
            project_id=a.project_id,
            title="Сценарий на тайном документе",
            kind=ScenarioKind.INVARIANT,
            preconditions="p",
            expected="e",
            author=r.AUTHOR,
            scenario_id="SCN-001",
            doc_key="secret/spec-alpha",
            group_key=group_key,
            steps=["шаг"],
        )
    with transactional(a.sf, commit=False) as s:
        shipped = s.scalars(
            select(SyncOpModel.fields).where(SyncOpModel.entity_kind == "scenario")
        ).all()
    assert shipped
    assert all("spec-alpha" not in f and "group_key" not in f for f in shipped)

    _push(a, store)
    _pull(b, store)

    for path in store.root.rglob("*"):
        if path.is_file():
            assert b"spec-alpha" not in path.read_bytes(), path
    with transactional(b.sf, commit=False) as s:
        row = s.scalars(select(ScenarioModel).where(ScenarioModel.scenario_id == "SCN-001")).one()
        assert (row.group_key, row.doc_key) == (reg.RESTRICTED_GROUP_KEY, None)


def test_revision_diff_never_carries_local_columns(pair) -> None:
    """Ключи local-колонок сущности вычищаются из diff, и дайджест реплик сходится."""
    a, b, store = pair
    r.make_task(a, TASK)
    r.make_doc(a, DOC, {"intro": "v1\n"})
    doc_id = r.doc_id(a, DOC)
    with transactional(a.sf) as s:
        rev.write(
            s,
            project_id=a.project_id,
            entity_kind=EntityKind.TASK,
            entity_id=_task_row_id(a),
            author=r.AUTHOR,
            diff=json.dumps({"op": "x", "row_id": 5, "title": "t"}),
        )
        rev.write(
            s,
            project_id=a.project_id,
            entity_kind=EntityKind.DOCUMENT,
            entity_id=doc_id,
            author=r.AUTHOR,
            diff=json.dumps(
                {
                    "op": "y",
                    "changes": [
                        {"field": "projection_hash", "old": "aaa", "new": "bbb"},
                        {"field": "title", "old": "s", "new": "spec"},
                    ],
                    "node_position": 3,
                }
            ),
        )
    diffs = [str(op["diff"]) for op in _revision_ops(a)]
    joined = "\n".join(diffs)
    for leaked in ('"row_id"', '"node_position"', "aaa", "bbb"):
        assert leaked not in joined, leaked
    assert '"new": "spec"' in joined
    _push(a, store)
    _pull(b, store)
    with transactional(a.sf, commit=False) as sa, transactional(b.sf, commit=False) as sb:
        assert digest.compute(sa, a.project_id) == digest.compute(sb, b.project_id)


def test_document_made_restricted_later_in_the_same_transaction(pair) -> None:
    """Ревизии захвачены flush'ем, документ стал restricted к commit'у — не уезжают."""
    a, _b, _store = pair
    with transactional(a.sf) as s:
        doc = doc_service.create(
            s,
            project_id=a.project_id,
            doc_key="later",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="later",
            author=r.AUTHOR,
            path="docs/later.md",
            sensitivity=Sensitivity.INTERNAL,
            reindex=False,
        )
        doc_service.add_section(
            s,
            project_id=a.project_id,
            document_id=doc.row_id,
            anchor="body",
            heading="Body",
            level=2,
            position=0,
            body="станет тайной\n",
            author=r.AUTHOR,
            reindex=False,
        )
        s.flush()
        doc_model = s.get_one(DocumentModel, doc.row_id)
        doc_model.sensitivity = Sensitivity.RESTRICTED.value
    assert _local_kinds(a) >= {EntityKind.DOCUMENT.value, EntityKind.SECTION.value}
    assert _revision_ops(a) == []


def test_revision_of_deleted_entity_stays_local(pair) -> None:
    a, _b, _store = pair
    r.make_task(a, TASK)
    with transactional(a.sf) as s:
        task = s.scalars(select(TaskModel).where(TaskModel.task_id == TASK)).one()
        row = {
            "revision_id": "01J0000000000000000000000Z",
            "project_id": a.project_id,
            "entity_kind": EntityKind.TASK.value,
            "entity_id": task.row_id + 1000,
        }
        assert cap.shipped_fields(s, "revision", row) is None


def test_digest_counts_revisions_but_not_their_parent(pair) -> None:
    a, _b, _store = pair
    r.make_task(a, TASK)
    with transactional(a.sf, commit=False) as s:
        before = digest.compute(s, a.project_id)
    with transactional(a.sf) as s:
        s.execute(update(RevisionModel).values(parent_revision_id="01J0000000000000000000000X"))
    with transactional(a.sf, commit=False) as s:
        assert digest.compute(s, a.project_id) == before, "parent_revision_id вне дайджеста"
    with transactional(a.sf) as s:
        s.delete(s.scalars(select(RevisionModel)).first())
    with transactional(a.sf, commit=False) as s:
        assert digest.compute(s, a.project_id) != before, "состав ревизий входит в дайджест"
