"""CLO-014: дайджест состояния и ``verify`` (RFC 29 §3.7, §10.6).

Acceptance: «Дайджест по RFC 29 §3.7 не зависит от row_id, position,
parent_revision_id и restricted-строк; две реплики после fast-forward дают один
дайджест; дайджест пишется в changeset».

Независимость проверяется правкой мимо захвата (голая ``Session``/SQL): так
меняется только то, что в дайджест входить не должно, и журнал не трогается.
Контрольная правка authored-колонки тем же способом дайджест меняет — тест не
зеленеет на дайджесте-константе.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.infra.db import transactional
from cod_doc.infra.models import (
    DocumentModel,
    PlanModel,
    ProjectModel,
    RevisionModel,
    SectionModel,
    TaskModel,
)
from cod_doc.services.sync_service import _local, digest, pull, push
from cod_doc.services.sync_service.changeset import decode
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    return a, r.replica_b(tmp_path, a), r.remote(tmp_path)


def _push(rep: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _digest(rep: r.Replica) -> str:
    with transactional(rep.sf, commit=False) as s:
        return digest.compute(s, rep.project_id)


def _verify(rep: r.Replica, store: FileRemote) -> digest.VerifyResult:
    return digest.verify(rep.sf, r.SLUG, store=store)


def _seed(a: r.Replica) -> None:
    r.make_task(a, "DEM-001", description="первая")
    r.make_task(a, "DEM-002", blocked_by=["DEM-001"])
    r.make_doc(a, "spec", {"intro": "one", "body": "two", "tail": "three"})
    r.tag_doc(a, "spec", "sync")


def _raw(rep: r.Replica, sql: str, **params: object) -> None:
    with rep.engine.begin() as conn:  # мимо захвата: журнал не меняется
        conn.execute(text(sql), params)


# --- две реплики --------------------------------------------------------------------


def test_two_replicas_after_fast_forward_have_one_digest(pair: tuple) -> None:
    a, b, store = pair
    _seed(a)
    _push(a, store)
    _pull(b, store)

    assert _digest(a) == _digest(b)
    # row_id у реплик разный по построению: B завела проект первым и одна.
    with transactional(a.sf) as sa, transactional(b.sf) as sb:
        ids_a = sorted(sa.scalars(select(TaskModel.row_id)))
        ids_b = sorted(sb.scalars(select(TaskModel.row_id)))
    assert len(ids_a) == len(ids_b) == 2


def test_digest_is_written_into_the_changeset(pair: tuple) -> None:
    a, _b, store = pair
    _seed(a)
    result = _push(a, store)

    assert result.head is not None
    with transactional(a.sf) as s:
        uid = s.get_one(ProjectModel, a.project_id).uid
    cs = decode(store.get_object(str(uid), result.head), result.head)
    assert cs.digest != _local.DIGEST_PENDING
    assert cs.digest == _digest(a)


def test_digest_after_stacked_pushes_matches_on_both_replicas(pair: tuple) -> None:
    a, b, store = pair
    _seed(a)
    _push(a, store)
    _pull(b, store)
    r.set_description(b, "DEM-001", "правка с B")
    r.untag_doc(b, "spec")
    pushed = _push(b, store)
    _pull(a, store)

    assert pushed.head is not None
    assert _digest(a) == _digest(b)
    assert _verify(a, store).equal
    assert _verify(b, store).equal


# --- от чего дайджест не зависит ----------------------------------------------------


def test_digest_ignores_row_id(pair: tuple) -> None:
    a, b, store = pair
    with Session(b.engine) as s:  # сдвинуть автоинкремент B чужим проектом
        other = ProjectModel(slug="other", title="O", root_path="/tmp/o", config_json={})
        other.created = other.updated = datetime.now(UTC)
        s.add(other)
        s.flush()
        for i in range(5):
            s.add(
                PlanModel(
                    project_id=other.row_id,
                    scope=f"pad-{i}",
                    created=datetime.now(UTC),
                    last_updated=datetime.now(UTC),
                )
            )
        s.commit()
    _seed(a)
    _push(a, store)
    _pull(b, store)

    with transactional(a.sf) as sa, transactional(b.sf) as sb:
        plan_a = sa.scalars(select(PlanModel.row_id).where(PlanModel.scope == "demo-plan")).one()
        plan_b = sb.scalars(select(PlanModel.row_id).where(PlanModel.scope == "demo-plan")).one()
    assert plan_a != plan_b
    assert _digest(a) == _digest(b)


def test_digest_ignores_position_but_not_authored_columns(pair: tuple) -> None:
    a, _b, _store = pair
    _seed(a)
    before = _digest(a)

    _raw(a, "UPDATE section SET position = position + 10")
    assert _digest(a) == before

    _raw(a, "UPDATE section SET body = 'другое' WHERE anchor = 'intro'")
    assert _digest(a) != before


def test_digest_ignores_parent_revision_id(pair: tuple) -> None:
    a, _b, _store = pair
    _seed(a)
    with transactional(a.sf) as s:
        revs = s.scalars(select(RevisionModel.revision_id).order_by(RevisionModel.revision_id))
        first, *rest = revs.all()
    assert rest
    before = _digest(a)

    with a.engine.begin() as conn:
        conn.execute(
            update(RevisionModel)
            .where(RevisionModel.revision_id.in_(rest))
            .values(parent_revision_id=first)
        )
    assert _digest(a) == before


def test_digest_ignores_restricted_rows(pair: tuple) -> None:
    a, b, store = pair
    _seed(a)
    _push(a, store)
    _pull(b, store)
    before = _digest(a)

    with transactional(a.sf) as s:  # restricted через сервис: захват его не везёт
        from cod_doc.services import doc_service

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
            anchor="intro",
            heading="Intro",
            level=2,
            position=0,
            body="тайна",
            author=r.AUTHOR,
            reindex=False,
        )
    with transactional(a.sf) as s:
        assert s.scalars(select(DocumentModel).where(DocumentModel.doc_key == "secret")).one()
        assert s.scalars(select(SectionModel).where(SectionModel.body == "тайна")).one()

    assert _digest(a) == before == _digest(b)
    assert r.pending(a) == 0  # restricted не оставил хвоста — дайджест и журнал согласны


# --- verify -------------------------------------------------------------------------


def test_verify_tracks_head_tail_and_pull(pair: tuple) -> None:
    a, b, store = pair
    _seed(a)
    _push(a, store)

    behind = _verify(b, store)
    assert behind.remote == _digest(a)
    assert not behind.equal

    _pull(b, store)
    assert _verify(b, store) == digest.VerifyResult(_digest(b), _digest(a), equal=True)

    r.set_description(b, "DEM-001", "хвост")  # неупакованный хвост — уже не голова
    assert not _verify(b, store).equal


def test_verify_detects_write_past_the_journal(pair: tuple) -> None:
    a, b, store = pair
    _seed(a)
    _push(a, store)
    _pull(b, store)

    _raw(b, "UPDATE task SET title = 'мимо журнала' WHERE task_id = 'DEM-001'")

    result = _verify(b, store)
    assert result.remote == _digest(a)
    assert not result.equal


def test_verify_on_empty_remote_and_legacy_zero_digest(pair: tuple, tmp_path: Path) -> None:
    a, _b, store = pair
    assert _verify(a, store).remote is None

    _seed(a)
    result = _push(a, store)
    assert result.head is not None
    with transactional(a.sf) as s:
        s.execute(text("UPDATE changeset SET digest = :z"), {"z": _local.DIGEST_PENDING})
    # голова упакована «до CLO-014»: сверять не с чем
    assert _verify(a, store) == digest.VerifyResult(_digest(a), None, equal=False)
