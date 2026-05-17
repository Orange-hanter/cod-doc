"""COD-013 / RFL-071: sync_section + resolve / verify pipeline of LinkService."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    LinkKind,
    Priority,
    TaskType,
    UserStoryStatus,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    UserStoryModel,
)
from cod_doc.services import doc_service as docs
from cod_doc.services import link_service as links
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _add_doc(session: Session, project_id: int, *, doc_key: str, title: str | None = None) -> int:
    doc = docs.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.GUIDE,
        status=DocumentStatus.ACTIVE,
        title=title or doc_key,
        author="human:test",
        owner="human:test",
    )
    return doc.row_id  # type: ignore[return-value]


# ============================================================================ #
# sync_section: replace stored links from current body                          #
# ============================================================================ #


def test_sync_section_creates_link_rows(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        doc = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=doc,
            anchor="intro",
            heading="Intro",
            level=2,
            position=0,
            body="See [[doc:target]] and [[task:AUTH-001]].",
            author="human:test",
        )
        rows = links.sync_section(session, section_id=sec.row_id)
        kinds = {r.kind for r in rows}
        assert kinds == {LinkKind.CANONICAL, LinkKind.TASK}


def test_sync_section_replaces_old_links(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        doc = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=doc,
            anchor="intro",
            heading="Intro",
            level=2,
            position=0,
            body="See [[doc:old]].",
            author="human:test",
        )
        first = links.sync_section(session, section_id=sec.row_id)
        assert len(first) == 1 and first[0].raw == "[[doc:old]]"

        # Patch body, sync should replace.
        docs.patch_section(
            session,
            document_id=doc,
            anchor="intro",
            new_body="See [[doc:new]] and [[task:T-1]].",
            author="human:test",
        )
        second = links.sync_section(session, section_id=sec.row_id)
        keys = {(r.kind, r.raw) for r in second}
        assert (LinkKind.CANONICAL, "[[doc:new]]") in keys
        assert (LinkKind.TASK, "[[task:T-1]]") in keys
        # Old row gone.
        assert all(r.raw != "[[doc:old]]" for r in second)


# ============================================================================ #
# resolve                                                                      #
# ============================================================================ #


def test_resolve_canonical_finds_doc(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc(session, proj, doc_key="modules/M1-auth/overview")
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="Ref [[doc:modules/M1-auth/overview]].",
            author="human:test",
        )
        rows = links.sync_section(session, section_id=sec.row_id)
        resolved = links.resolve(session, rows[0].row_id)
        assert resolved.resolved is True
        assert resolved.to_doc_key == "modules/M1-auth/overview"
        assert resolved.broken_reason is None


def test_resolve_markdown_strips_docs_prefix(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Importer strips a leading ``docs/`` from doc_keys (`_derive_doc_key`).

    Links written as ``[x](docs/foo/bar.md)`` from a root-level doc must still
    resolve to the imported key ``foo/bar``. The resolver applies the same
    normalization as a candidate fallback.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc(session, proj, doc_key="architecture/polyglot")
        host = _add_doc(session, proj, doc_key="CLAUDE")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="See [polyglot](docs/architecture/polyglot.md).",
            author="human:test",
        )
        rows = links.sync_section(session, section_id=sec.row_id)
        resolved = links.resolve(session, rows[0].row_id)
        assert resolved.resolved is True
        assert resolved.to_doc_key == "architecture/polyglot"


def test_resolve_markdown_relative_to_source_dir(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Bare-filename refs like ``[x](concept.md)`` are relative to the source
    doc's directory: from ``architecture/overview`` they target ``architecture/concept``.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc(session, proj, doc_key="architecture/concept")
        host = _add_doc(session, proj, doc_key="architecture/overview")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="See [concept](concept.md).",
            author="human:test",
        )
        rows = links.sync_section(session, section_id=sec.row_id)
        resolved = links.resolve(session, rows[0].row_id)
        assert resolved.resolved is True
        assert resolved.to_doc_key == "architecture/concept"


def test_resolve_intra_doc_anchor(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """``[X](#anchor)`` is an intra-doc anchor — the parser sees no doc_key,
    so the resolver must fall back to the source doc.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        host = _add_doc(session, proj, doc_key="business/requirements")
        docs.add_section(
            session,
            document_id=host,
            anchor="target-anchor",
            heading="Target",
            level=2,
            position=0,
            body="Target section.",
            author="human:test",
        )
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="src",
            heading="Src",
            level=2,
            position=1,
            body="Jump to [Target](#target-anchor).",
            author="human:test",
        )
        rows = links.sync_section(session, section_id=sec.row_id)
        resolved = links.resolve(session, rows[0].row_id)
        assert resolved.resolved is True
        assert resolved.to_doc_key == "business/requirements"


def test_resolve_markdown_walks_up_source_dir(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Parser eats ``../`` blindly without counting levels, so a target like
    ``[x](../polyglot.md)`` from ``architecture/v2/cloud_connectivity`` arrives
    at the resolver as ``polyglot``. The resolver tries each ancestor dir of
    the source doc as a candidate prefix and finds ``architecture/polyglot``.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc(session, proj, doc_key="architecture/polyglot")
        host = _add_doc(session, proj, doc_key="architecture/v2/cloud_connectivity")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="See [Polyglot](../polyglot.md).",
            author="human:test",
        )
        rows = links.sync_section(session, section_id=sec.row_id)
        resolved = links.resolve(session, rows[0].row_id)
        assert resolved.resolved is True
        assert resolved.to_doc_key == "architecture/polyglot"


def test_resolve_canonical_unknown_marks_unresolved(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="Dangling [[doc:no-such-thing]].",
            author="human:test",
        )
        rows = links.sync_section(session, section_id=sec.row_id)
        result = links.resolve(session, rows[0].row_id)
        assert result.resolved is False
        assert result.to_doc_key is None  # not auto-stamped if missing


def test_resolve_section_ref_validates_anchor(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        target = _add_doc(session, proj, doc_key="t")
        docs.add_section(
            session,
            document_id=target,
            anchor="data-model",
            heading="Data Model",
            level=2,
            position=0,
            body="x",
            author="human:test",
        )
        host = _add_doc(session, proj, doc_key="src")
        sec_ok = docs.add_section(
            session,
            document_id=host,
            anchor="ok",
            heading="OK",
            level=2,
            position=0,
            body="See [[doc:t#data-model]].",
            author="human:test",
        )
        sec_bad = docs.add_section(
            session,
            document_id=host,
            anchor="bad",
            heading="BAD",
            level=2,
            position=1,
            body="See [[doc:t#nope]].",
            author="human:test",
        )
        ok = links.resolve_section(session, sec_ok.row_id)
        bad = links.resolve_section(session, sec_bad.row_id)
        assert ok[0].resolved is True
        assert bad[0].resolved is False
        assert bad[0].broken_reason and "nope" in bad[0].broken_reason


def test_resolve_task_ref(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        # Need a plan + section to host a task.
        now = datetime.now(UTC)
        plan = PlanModel(project_id=proj, scope="x-plan", created=now, last_updated=now)
        session.add(plan)
        session.flush()
        ps = PlanSectionModel(plan_id=plan.row_id, letter="A", title="X", slug="A-X", position=0)
        session.add(ps)
        session.flush()
        tasks.create(
            session,
            project_id=proj,
            plan_id=plan.row_id,
            section_id=ps.row_id,
            task_id="AUTH-025",
            title="t",
            type=TaskType.FEATURE,
            priority=Priority.LOW,
            author="human:test",
        )
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="Blocked by [[task:AUTH-025]] and [[task:GHOST-999]].",
            author="human:test",
        )
        rows = links.resolve_section(session, sec.row_id)
        by_target = {r.to_task_id or r.raw: r for r in rows}
        assert by_target["AUTH-025"].resolved is True
        ghost = next(r for r in rows if "GHOST" in r.raw)
        assert ghost.resolved is False


def test_resolve_story_ref(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        now = datetime.now(UTC)
        story = UserStoryModel(
            project_id=proj,
            story_id="US-014",
            persona="user",
            narrative="...",
            status=UserStoryStatus.ACCEPTED.value,
            priority=Priority.MEDIUM.value,
            created=now,
            last_updated=now,
        )
        session.add(story)
        session.flush()
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="See [[story:US-014]] and [[story:US-999]].",
            author="human:test",
        )
        rows = links.resolve_section(session, sec.row_id)
        ok = next(r for r in rows if r.to_story_id == "US-014")
        bad = next(r for r in rows if "US-999" in r.raw)
        assert ok.resolved is True
        assert bad.resolved is False


def test_resolve_wiki_exact_match_only(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Wiki-link `[[Some Title]]` resolves on exact title or doc_key match."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc(session, proj, doc_key="auth-overview", title="M1 AUTH v2")
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="Ref [[M1 AUTH v2]] vs [[Unknown Title]].",
            author="human:test",
        )
        rows = links.resolve_section(session, sec.row_id)
        ok = next(r for r in rows if "M1 AUTH" in r.raw)
        bad = next(r for r in rows if "Unknown" in r.raw)
        assert ok.resolved is True
        assert ok.to_doc_key == "auth-overview"
        assert bad.resolved is False


def test_resolve_url_marks_resolved(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """URLs are accepted as 'resolved' without HTTP check (verify is separate)."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="See https://example.com/x.",
            author="human:test",
        )
        rows = links.resolve_section(session, sec.row_id)
        assert rows[0].kind is LinkKind.URL
        assert rows[0].resolved is True


# ============================================================================ #
# verify                                                                       #
# ============================================================================ #


def test_verify_recomputes_resolved_state(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """After deleting target doc, verify must flip a previously-resolved link to broken."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        target = _add_doc(session, proj, doc_key="target")
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="Ref [[doc:target]].",
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        # Sanity: link is resolved.
        rows_before = links.list_for_section(session, sec.row_id)
        assert rows_before[0].resolved is True

        # Delete target doc; verify must mark the link broken.
        from cod_doc.infra.models import DocumentModel

        session.delete(session.get(DocumentModel, target))
        session.flush()

        report = links.verify_section(session, sec.row_id)
        assert report.broken == 1 and report.ok == 0
        rows_after = links.list_for_section(session, sec.row_id)
        assert rows_after[0].resolved is False
        assert rows_after[0].broken_reason is not None
        assert rows_after[0].last_checked is not None


def test_verify_skips_url_kind(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """URL links are not network-checked here; they keep their resolve state."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="See https://example.com/x.",
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        report = links.verify_section(session, sec.row_id)
        assert report.skipped == 1
        rows = links.list_for_section(session, sec.row_id)
        assert rows[0].resolved is True


def test_resolve_adr_ref(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """[[adr:ADR-NNN]] resolves to an existing ADR row in the same project."""
    from cod_doc.services import adr_service

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        adr_service.create(session, project_id=proj, title="layered", status="accepted")
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="Governed by [[adr:ADR-001]] and [[adr:ADR-999]].",
            author="human:test",
        )
        rows = links.resolve_section(session, sec.row_id)
        # Two ADR links, one good one broken.
        adrs = [r for r in rows if r.kind is LinkKind.ADR]
        assert len(adrs) == 2
        by_id = {r.to_adr_id or "broken": r for r in adrs}
        # ADR-001 resolved
        assert any(r.resolved is True and r.to_adr_id == "ADR-001" for r in adrs)
        # ADR-999 broken
        ghost = next(r for r in adrs if r.to_adr_id != "ADR-001")
        assert ghost.resolved is False
        assert ghost.broken_reason and "adr not found" in ghost.broken_reason


def test_resolve_adr_bare_token(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Bare ADR-007 in prose resolves to ADR-007."""
    from cod_doc.services import adr_service

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        adr_service.create(session, project_id=proj, title="X", status="accepted")
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="This is governed by ADR-001.",
            author="human:test",
        )
        rows = links.resolve_section(session, sec.row_id)
        adrs = [r for r in rows if r.kind is LinkKind.ADR]
        assert len(adrs) == 1
        assert adrs[0].to_adr_id == "ADR-001"
        assert adrs[0].resolved is True
