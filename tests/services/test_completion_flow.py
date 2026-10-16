"""COD-022: completion flow — RevisionService.revert dispatch + plan staleness."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    Priority,
    TaskStatus,
    TaskType,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DocumentModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
    SectionModel,
)
from cod_doc.services import doc_service as docs
from cod_doc.services import revert_service
from cod_doc.services import revision_service as rev
from cod_doc.services import task_service as tasks
from tests._task_scope import sole_project_id

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make_task(session: Session, proj: int, plan: int, sec: int, tid: str) -> int:
    t = tasks.create(
        session,
        project_id=proj,
        plan_id=plan,
        section_id=sec,
        task_id=tid,
        title=f"Implement: {tid}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
    )
    return t.row_id  # type: ignore[return-value]


# ============================================================================ #
# RevisionService.revert — TASK                                                #
# ============================================================================ #


def test_revert_task_status_change(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_task(session, p, pl, s, "CF-001")

        tasks.update_status(
            session,
            project_id=sole_project_id(session),
            task_id="CF-001",
            new_status=TaskStatus.IN_PROGRESS,
            author="human:test",
            via_checkout=True,
        )
        history = rev.list_for_entity(
            session,
            EntityKind.TASK,
            tasks.get(session, "CF-001", project_id=p).row_id,  # type: ignore[union-attr]
            project_id=p,
        )
        status_rev = history[-1]
        assert status_rev.diff.startswith('{"op": "status"')

        revert_service.revert(
            session, status_rev.revision_id, author="human:test", project_id=status_rev.project_id
        )

        t = tasks.get(session, "CF-001", project_id=p)
        assert t is not None
        # ADO-156: реверт приземляется в канонический бакет, а не в легаси.
        assert t.status is TaskStatus.TODO


def test_revert_task_complete(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_task(session, p, pl, s, "CF-001")
        tasks.complete(
            session,
            project_id=sole_project_id(session),
            task_id="CF-001",
            author="human:test",
            commit_sha="abc",
        )

        history = rev.list_for_entity(
            session,
            EntityKind.TASK,
            tasks.get(session, "CF-001", project_id=p).row_id,  # type: ignore[union-attr]
            project_id=p,
        )
        complete_rev = history[-1]
        revert_service.revert(
            session,
            complete_rev.revision_id,
            author="human:test",
            project_id=complete_rev.project_id,
        )

        t = tasks.get(session, "CF-001", project_id=p)
        assert t is not None
        assert t.status is TaskStatus.TODO


def test_revert_task_unsupported_op_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_task(session, p, pl, s, "CF-001")
        # The initial 'create' revision has op='create' — not revertible.
        history = rev.list_for_entity(
            session,
            EntityKind.TASK,
            tasks.get(session, "CF-001", project_id=p).row_id,  # type: ignore[union-attr]
            project_id=p,
        )
        create_rev = history[0]
        with pytest.raises(rev.RevertNotSupportedError):
            revert_service.revert(
                session,
                create_rev.revision_id,
                author="human:test",
                project_id=create_rev.project_id,
            )


# ============================================================================ #
# RevisionService.revert — SECTION                                             #
# ============================================================================ #


def test_revert_section_patch_restores_old_body(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _, _ = _seed(session)
        doc = docs.create(
            session,
            project_id=p,
            doc_key="test-doc",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="Test",
            author="human:test",
        )
        # Use bodies with trailing '\n' (realistic: markdown files always end with newline).
        # Unified diffs for single-line strings without '\n' are not parseable by
        # _restore_original_from_unified; that edge case is addressed in COD-023.
        sec = docs.add_section(
            session,
            project_id=p,
            document_id=doc.row_id,
            anchor="intro",
            heading="Intro",
            level=2,
            position=0,
            body="Original body.\n",
            author="human:test",
        )
        docs.patch_section(
            session,
            project_id=p,
            document_id=doc.row_id,
            anchor="intro",
            new_body="Updated body.\n",
            author="human:test",
        )
        history = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id, project_id=p)
        patch_rev = history[-1]

        revert_service.revert(
            session, patch_rev.revision_id, author="human:test", project_id=patch_rev.project_id
        )

        sec_model = session.get(SectionModel, sec.row_id)
        assert sec_model is not None
        assert sec_model.body == "Original body.\n"


def test_revert_section_writes_new_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _, _ = _seed(session)
        doc = docs.create(
            session,
            project_id=p,
            doc_key="test-doc",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="Test",
            author="human:test",
        )
        sec = docs.add_section(
            session,
            project_id=p,
            document_id=doc.row_id,
            anchor="intro",
            heading="Intro",
            level=2,
            position=0,
            body="v1\n",
            author="human:test",
        )
        docs.patch_section(
            session,
            project_id=p,
            document_id=doc.row_id,
            anchor="intro",
            new_body="v2\n",
            author="human:test",
        )
        n_before = len(rev.list_for_entity(session, EntityKind.SECTION, sec.row_id, project_id=p))

        patch_rev = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id, project_id=p)[-1]
        revert_service.revert(
            session, patch_rev.revision_id, author="human:test", project_id=patch_rev.project_id
        )

        n_after = len(rev.list_for_entity(session, EntityKind.SECTION, sec.row_id, project_id=p))
        assert n_after == n_before + 1  # the revert is a new revision


# ============================================================================ #
# RevisionService.revert — DOCUMENT rename                                     #
# ============================================================================ #


def test_revert_document_rename_restores_key(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _, _ = _seed(session)
        doc = docs.create(
            session,
            project_id=p,
            doc_key="original-key",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="T",
            author="human:test",
        )
        docs.rename(
            session,
            project_id=p,
            document_id=doc.row_id,
            new_doc_key="renamed-key",
            author="human:test",
        )
        rename_rev = rev.list_for_entity(session, EntityKind.DOCUMENT, doc.row_id, project_id=p)[-1]

        revert_service.revert(
            session, rename_rev.revision_id, author="human:test", project_id=rename_rev.project_id
        )

        doc_model = session.get(DocumentModel, doc.row_id)
        assert doc_model is not None
        assert doc_model.doc_key == "original-key"


# ============================================================================ #
# RevisionService.revert — скоуп проекта сущности (AFT-022)                    #
# ============================================================================ #


def _other_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="other", title="O", root_path="/tmp/o", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _move_revision_to(session: Session, revision_id: str, project_id: int) -> None:
    """Рассинхрон легаси-импорта: ревизия числится за проектом B, сущность — в A."""
    model = session.execute(
        select(RevisionModel).where(RevisionModel.revision_id == revision_id)
    ).scalar_one()
    model.project_id = project_id
    session.flush()


def test_section_label_with_plus_in_doc_key_is_read_and_reverted(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """``doc_key`` не валидируется и может содержать ``+``: метка ревизии всё равно читается."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _, _ = _seed(session)
        doc = docs.create(
            session,
            project_id=p,
            doc_key="c++-guide",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="C++",
            author="human:test",
        )
        sec = docs.add_section(
            session,
            project_id=p,
            document_id=doc.row_id,
            anchor="intro",
            heading="Intro",
            level=2,
            position=0,
            body="Original body.\n",
            author="human:test",
        )
        docs.patch_section(
            session,
            project_id=p,
            document_id=doc.row_id,
            anchor="intro",
            new_body="Updated body.\n",
            author="human:test",
        )
        patch_rev = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id, project_id=p)[-1]
        # Метка несёт doc_key с «+» — именно её прежний [^+]* не дочитывал.
        assert patch_rev.diff.startswith("--- section:c++-guide#intro+++ section:c++-guide#intro@@")

        revert_service.revert(session, patch_rev.revision_id, author="human:test", project_id=p)

        section = session.get(SectionModel, sec.row_id)
        assert section is not None
        assert section.body == "Original body.\n"


@pytest.mark.parametrize(
    ("diff", "anchor"),
    [
        # обычная метка и метка с «+» в doc_key читаются
        ("--- section:d#a+++ section:d#a@@ -1 +1 @@-old\n+new\n", "a"),
        ("--- section:c++-guide#intro+++ section:c++-guide#intro@@ -1 +1 @@-o\n+n\n", "intro"),
        ("--- /dev/null+++ section:d#a@@ -0,0 +1 @@+new\n", "a"),
        # «+++ section:» внутри якоря — заголовок неоднозначен, разбор отказывает
        ("--- section:d#a+++ section:d#evil@@+++ section:d#real@@ -1 +1 @@-o\n+n\n", None),
        ("--- section:d#a+++ section:d#x+++ section:d#y@@ -1 +1 @@-o\n+n\n", None),
        # тело секции не участвует: метка из него не берётся
        ("-old\n+new +++ section:d#body@@ -1 +1 @@\n", None),
        # без ханка метки нет
        ("", None),
    ],
)
def test_section_label_anchor_is_read_only_when_unambiguous(diff: str, anchor: str | None) -> None:
    """Писатель и читатель метки не расходятся: что нельзя разобрать однозначно —
    не откатывается (ai-review #192, parser differential)."""
    assert revert_service._section_label_anchor(diff) == anchor


def test_revert_section_of_another_project_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _, _ = _seed(session)
        other = _other_project(session)
        doc = docs.create(
            session,
            project_id=p,
            doc_key="d",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="T",
            author="human:test",
        )
        sec = docs.add_section(
            session,
            project_id=p,
            document_id=doc.row_id,
            anchor="intro",
            heading="Intro",
            level=2,
            position=0,
            body="Original body.\n",
            author="human:test",
        )
        docs.patch_section(
            session,
            project_id=p,
            document_id=doc.row_id,
            anchor="intro",
            new_body="Updated body.\n",
            author="human:test",
        )
        patch_rev = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id, project_id=p)[-1]
        _move_revision_to(session, patch_rev.revision_id, other)

        with pytest.raises(
            LookupError, match=f"section #{sec.row_id} not found in project #{other}"
        ):
            revert_service.revert(
                session, patch_rev.revision_id, author="human:test", project_id=other
            )
        section = session.get(SectionModel, sec.row_id)
        assert section is not None
        assert section.body == "Updated body.\n"


def test_revert_document_of_another_project_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _, _ = _seed(session)
        other = _other_project(session)
        doc = docs.create(
            session,
            project_id=p,
            doc_key="original-key",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="T",
            author="human:test",
        )
        docs.rename(
            session,
            project_id=p,
            document_id=doc.row_id,
            new_doc_key="renamed-key",
            author="human:test",
        )
        rename_rev = rev.list_for_entity(session, EntityKind.DOCUMENT, doc.row_id, project_id=p)[-1]
        _move_revision_to(session, rename_rev.revision_id, other)

        with pytest.raises(
            LookupError, match=f"document #{doc.row_id} not found in project #{other}"
        ):
            revert_service.revert(
                session, rename_rev.revision_id, author="human:test", project_id=other
            )
        doc_model = session.get(DocumentModel, doc.row_id)
        assert doc_model is not None
        assert doc_model.doc_key == "renamed-key"


def test_revert_unsupported_entity_kind_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _, _ = _seed(session)
        # Write a PLAN revision manually with an unsupported entity_kind.
        written = rev.write(
            session,
            project_id=p,
            entity_kind=EntityKind.PLAN,
            entity_id=1,
            author="human:test",
            diff='{"op": "create"}',
        )
        with pytest.raises(rev.RevertNotSupportedError):
            revert_service.revert(
                session, written.revision_id, author="human:test", project_id=written.project_id
            )


# ============================================================================ #
# Completion flow — plan.last_updated staleness signal                          #
# ============================================================================ #


def test_complete_updates_plan_last_updated(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_task(session, p, pl, s, "CF-001")

        plan_before = session.get(PlanModel, pl)
        # SQLite stores datetimes without tzinfo; normalise for comparison.
        ts_before = plan_before.last_updated.replace(tzinfo=None)  # type: ignore[union-attr]

        tasks.complete(
            session, project_id=sole_project_id(session), task_id="CF-001", author="human:test"
        )

        plan_after = session.get(PlanModel, pl)
        ts_after = plan_after.last_updated.replace(tzinfo=None)  # type: ignore[union-attr]
        assert ts_after >= ts_before


def test_complete_plan_update_is_in_same_transaction(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """plan.last_updated changes atomically with the task completion."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make_task(session, p, pl, s, "CF-001")
        tasks.complete(
            session, project_id=sole_project_id(session), task_id="CF-001", author="human:test"
        )

        # Both changes visible within the same session.
        t = tasks.get(session, "CF-001", project_id=p)
        plan = session.get(PlanModel, pl)
        assert t.status is TaskStatus.DONE  # type: ignore[union-attr]
        # Both timestamps should be very close (same flush cycle).
        plan_ts = plan.last_updated.replace(tzinfo=None)  # type: ignore[union-attr]
        task_ts = t.completed_at.replace(tzinfo=None)  # type: ignore[union-attr]
        assert abs((plan_ts - task_ts).total_seconds()) < 1
