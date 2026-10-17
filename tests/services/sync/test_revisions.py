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

import time
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, update

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    Priority,
    Sensitivity,
)
from cod_doc.infra.db import transactional
from cod_doc.infra.models import (
    ApprovalDocRevisionLinkModel,
    RevisionModel,
    SectionModel,
    SyncOpModel,
    TaskModel,
)
from cod_doc.services import approval_service, doc_service, task_service
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


def test_restricted_document_revisions_never_ship(pair) -> None:
    a, b, store = pair
    with transactional(a.sf) as s:
        doc = doc_service.create(
            s,
            project_id=a.project_id,
            doc_key="secret",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="secret",
            author=r.AUTHOR,
            path="docs/secret.md",
            sensitivity=Sensitivity.RESTRICTED,
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
            body="тайна\n",
            author=r.AUTHOR,
            reindex=False,
        )
    with transactional(a.sf, commit=False) as s:
        assert (
            s.scalars(select(SyncOpModel).where(SyncOpModel.entity_kind == "revision")).all() == []
        )
        assert s.scalars(select(RevisionModel)).all(), "ревизии restricted-документа есть локально"
    r.make_task(a, TASK)
    _push(a, store)
    _pull(b, store)
    with transactional(b.sf, commit=False) as s:
        kinds = set(s.scalars(select(RevisionModel.entity_kind)))
    assert kinds == {EntityKind.TASK.value}


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
