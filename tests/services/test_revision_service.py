"""COD-015: RevisionService — write, chain, list, optimistic concurrency."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import revert_service
from cod_doc.services import revision_service as rev


def _add_project(session, slug: str = "p") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def test_write_first_revision_has_no_parent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        r = rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.TASK,
            entity_id=42,
            author="agent:task-steward",
            diff="--- /dev/null\n+++ task\n@@",
            reason="initial",
        )
        assert r.row_id is not None
        assert r.parent_revision_id is None
        assert len(r.revision_id) == 26  # ULID
        assert r.entity_kind == EntityKind.TASK


def test_second_revision_chains_to_first(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        first = rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.DOCUMENT,
            entity_id=7,
            author="human:dakh",
            diff="d1",
        )
        second = rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.DOCUMENT,
            entity_id=7,
            author="human:dakh",
            diff="d2",
        )
        assert second.parent_revision_id == first.revision_id
        assert second.revision_id != first.revision_id
        assert second.at >= first.at


def test_list_for_entity_oldest_first(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        a = rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.TASK,
            entity_id=1,
            author="x",
            diff="1",
        )
        b = rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.TASK,
            entity_id=1,
            author="x",
            diff="2",
        )
        c = rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.TASK,
            entity_id=1,
            author="x",
            diff="3",
        )
        history = rev.list_for_entity(session, EntityKind.TASK, 1, project_id=proj_id)
        assert [h.revision_id for h in history] == [a.revision_id, b.revision_id, c.revision_id]


def test_list_filters_by_entity(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Revisions of one entity must not leak into another entity's history."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.TASK,
            entity_id=1,
            author="x",
            diff="t1",
        )
        rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.TASK,
            entity_id=2,
            author="x",
            diff="t2",
        )
        rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.DOCUMENT,
            entity_id=1,
            author="x",
            diff="d1",
        )

        assert len(rev.list_for_entity(session, EntityKind.TASK, 1, project_id=proj_id)) == 1
        assert len(rev.list_for_entity(session, EntityKind.TASK, 2, project_id=proj_id)) == 1
        assert len(rev.list_for_entity(session, EntityKind.DOCUMENT, 1, project_id=proj_id)) == 1


def test_expected_parent_match_succeeds(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        first = rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.PLAN,
            entity_id=1,
            author="x",
            diff="d",
        )
        second = rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.PLAN,
            entity_id=1,
            author="x",
            diff="d2",
            expected_parent_revision_id=first.revision_id,
        )
        assert second.parent_revision_id == first.revision_id


def test_expected_parent_mismatch_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        first = rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.STORY,
            entity_id=1,
            author="x",
            diff="d",
        )
        # A concurrent writer landed:
        rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.STORY,
            entity_id=1,
            author="other",
            diff="d2",
        )
        # We still think `first` is the head — must conflict.
        with pytest.raises(rev.RevisionConflictError):
            rev.write(
                session,
                project_id=proj_id,
                entity_kind=EntityKind.STORY,
                entity_id=1,
                author="x",
                diff="d3",
                expected_parent_revision_id=first.revision_id,
            )


def test_explicit_none_expected_parent_on_fresh_entity(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """`expected_parent_revision_id=None` is a valid 'I expect to be first' assertion."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        r = rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.LINK,
            entity_id=1,
            author="x",
            diff="d",
            expected_parent_revision_id=None,
        )
        assert r.parent_revision_id is None


def test_explicit_none_expected_parent_on_existing_entity_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """If history already exists, asserting `None` as parent must fail."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        rev.write(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.MODULE,
            entity_id=1,
            author="x",
            diff="d",
        )
        with pytest.raises(rev.RevisionConflictError):
            rev.write(
                session,
                project_id=proj_id,
                entity_kind=EntityKind.MODULE,
                entity_id=1,
                author="x",
                diff="d2",
                expected_parent_revision_id=None,
            )


def test_revert_of_another_projects_revision_is_not_found(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """AFT-022: ревизия проекта A под ``project_id`` проекта B ищется как отсутствующая."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        a = _add_project(session, "a")
        b = _add_project(session, "b")
        r = rev.write(
            session,
            project_id=a,
            entity_kind=EntityKind.TASK,
            entity_id=42,
            author="agent:task-steward",
            diff="--- /dev/null\n+++ task\n@@",
            reason="initial",
        )
        with pytest.raises(LookupError, match="revision not found"):
            revert_service.revert(session, r.revision_id, author="human:test", project_id=b)
        assert rev.get(session, r.revision_id, project_id=b) is None
        assert rev.get(session, r.revision_id, project_id=a) is not None


def test_revert_raises_lookup_for_unknown_revision_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        pid = _add_project(session)
        with pytest.raises(LookupError, match="revision not found"):
            revert_service.revert(
                session, "01HQX5Z9F0K8R0000000000000", author="human:test", project_id=pid
            )


def test_reads_by_entity_are_scoped_by_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """AFT-023: entity_id — row_id на всю hub-БД; история, head и выборка по
    ULID ревизии проекта A под project_id проекта B пусты."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _add_project(session, "a")
        b = _add_project(session, "b")
        r = rev.write(
            session,
            project_id=a,
            entity_kind=EntityKind.TASK,
            entity_id=42,
            author="agent:task-steward",
            diff="--- /dev/null\n+++ task\n@@",
            reason="initial",
        )

        assert [
            x.revision_id for x in rev.list_for_entity(session, EntityKind.TASK, 42, project_id=a)
        ] == [r.revision_id]
        assert rev.list_for_entity(session, EntityKind.TASK, 42, project_id=b) == []
        assert rev.list_for_entity(session, EntityKind.TASK, 42, limit=1, project_id=b) == []
        assert rev.head_for_entity(session, EntityKind.TASK, 42, project_id=a) == r.revision_id
        assert rev.head_for_entity(session, EntityKind.TASK, 42, project_id=b) is None
        assert rev.get(session, r.revision_id, project_id=b) is None
