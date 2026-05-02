"""COD-013: LinkService — parse / resolve / verify / rename_cascade."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
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
from cod_doc.services import revision_service as rev
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
# parse — pure function, no DB                                                  #
# ============================================================================ #


def test_parse_canonical_doc_ref() -> None:
    body = "See [[doc:modules/M1-auth/overview]] for details."
    parsed = links.parse(body)

    assert len(parsed) == 1
    p = parsed[0]
    assert p.kind is LinkKind.CANONICAL
    assert p.raw == "[[doc:modules/M1-auth/overview]]"
    assert p.target_doc_key == "modules/M1-auth/overview"
    assert p.anchor is None


def test_parse_canonical_section_ref() -> None:
    body = "See [[doc:modules/M1-auth/overview#data-model]]."
    parsed = links.parse(body)

    assert len(parsed) == 1
    p = parsed[0]
    assert p.kind is LinkKind.SECTION
    assert p.target_doc_key == "modules/M1-auth/overview"
    assert p.anchor == "data-model"


def test_parse_task_ref() -> None:
    parsed = links.parse("Blocked by [[task:AUTH-025]] until release.")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.TASK
    assert parsed[0].target_task_id == "AUTH-025"


def test_parse_story_ref() -> None:
    parsed = links.parse("Implements [[story:US-014]].")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.STORY
    assert parsed[0].target_story_id == "US-014"


def test_parse_wiki_link() -> None:
    """Plain `[[Some Title]]` form, no `kind:` prefix."""
    parsed = links.parse("Reference: [[M1 AUTH v2]].")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.WIKI
    assert parsed[0].raw == "[[M1 AUTH v2]]"
    # Wiki targets resolve later — parser captures the raw label only.
    assert parsed[0].target_doc_key is None


def test_parse_markdown_relative() -> None:
    parsed = links.parse("Read [Auth Overview](../modules/M1-auth/overview.md).")
    assert len(parsed) == 1
    p = parsed[0]
    assert p.kind is LinkKind.MARKDOWN
    assert p.raw == "[Auth Overview](../modules/M1-auth/overview.md)"
    # doc_key derived by stripping leading ../ and trailing .md
    assert p.target_doc_key == "modules/M1-auth/overview"


def test_parse_markdown_with_anchor() -> None:
    parsed = links.parse("See [data model](../modules/M1-auth/overview.md#data-model).")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.SECTION
    assert parsed[0].target_doc_key == "modules/M1-auth/overview"
    assert parsed[0].anchor == "data-model"


def test_parse_url() -> None:
    parsed = links.parse("Tracking issue: https://github.com/x/y/issues/1.")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.URL
    assert parsed[0].raw.startswith("https://github.com")


def test_parse_markdown_url_combines_to_url_kind() -> None:
    parsed = links.parse("See [GitHub](https://github.com/owner/repo).")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.URL
    assert "github.com/owner/repo" in parsed[0].raw


def test_parse_multiple_links_in_order() -> None:
    body = "See [[doc:a]], then [[task:T-001]] and [[doc:b#sec]]. External: https://example.com/x."
    parsed = links.parse(body)
    assert [p.kind for p in parsed] == [
        LinkKind.CANONICAL,
        LinkKind.TASK,
        LinkKind.SECTION,
        LinkKind.URL,
    ]
    assert [p.target_doc_key for p in parsed[:3]] == ["a", None, "b"]
    assert parsed[2].anchor == "sec"


def test_parse_skips_code_blocks() -> None:
    """Links inside fenced code blocks are not parsed."""
    body = (
        "Real link [[doc:real]].\n\n"
        "```python\n"
        "code = '[[doc:fake]]'\n"
        "```\n\n"
        "After code: [[task:TST-002]]."
    )
    parsed = links.parse(body)
    keys = [p.target_doc_key for p in parsed]
    task_ids = [p.target_task_id for p in parsed]
    assert "fake" not in keys
    assert "real" in keys
    assert "TST-002" in task_ids


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


# ============================================================================ #
# rename_cascade                                                               #
# ============================================================================ #


def test_rename_cascade_updates_link_rows(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc(session, proj, doc_key="old-key")
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="See [[doc:old-key]] and [[doc:old-key#a]].",
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        report = links.rename_cascade(
            session,
            project_id=proj,
            old_doc_key="old-key",
            new_doc_key="new-key",
            author="human:test",
        )
        assert report.updated_links == 2
        # All link rows now point to new-key.
        rows = links.list_for_section(session, sec.row_id)
        assert all(r.to_doc_key == "new-key" or r.to_doc_key is None for r in rows)
        # `raw` was rewritten as well.
        for r in rows:
            assert "old-key" not in r.raw
            assert "new-key" in r.raw


def test_rename_cascade_rewrites_section_body_and_writes_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc(session, proj, doc_key="old-key")
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="Pointer: [[doc:old-key]].",
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        before = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id)
        n_before = len(before)

        links.rename_cascade(
            session,
            project_id=proj,
            old_doc_key="old-key",
            new_doc_key="new-key",
            author="human:test",
        )

        # Body in DB updated.
        from cod_doc.infra.models import SectionModel

        body = session.get(SectionModel, sec.row_id).body
        assert "old-key" not in body
        assert "new-key" in body

        after = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id)
        assert len(after) == n_before + 1


def test_rename_cascade_no_op_when_keys_match(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc(session, proj, doc_key="x")
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="[[doc:x]]",
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        report = links.rename_cascade(
            session,
            project_id=proj,
            old_doc_key="x",
            new_doc_key="x",
            author="human:test",
        )
        assert report.updated_links == 0
        assert report.rewritten_sections == 0


def test_rename_cascade_only_touches_matching_links(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Links to other docs must remain unchanged."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc(session, proj, doc_key="old-key")
        _add_doc(session, proj, doc_key="other")
        host = _add_doc(session, proj, doc_key="src")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="See [[doc:old-key]] and [[doc:other]].",
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        links.rename_cascade(
            session,
            project_id=proj,
            old_doc_key="old-key",
            new_doc_key="renamed",
            author="human:test",
        )
        rows = links.list_for_section(session, sec.row_id)
        keys = sorted([r.to_doc_key for r in rows if r.to_doc_key is not None])
        assert keys == ["other", "renamed"]


# ============================================================================ #
# COD-014a — markdown-relative path cascade via path_map                        #
# ============================================================================ #


def _add_doc_with_path(
    session: Session, project_id: int, *, doc_key: str, path: str, title: str | None = None
) -> int:
    doc = docs.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.GUIDE,
        status=DocumentStatus.ACTIVE,
        title=title or doc_key,
        path=path,
        author="human:test",
        owner="human:test",
    )
    return doc.row_id  # type: ignore[return-value]


def test_rename_cascade_rewrites_markdown_relative_via_path_map(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Sibling document referencing a renamed doc via `[label](../old.md)` is rewritten."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        # Target doc: M1-auth/overview at path modules/M1-auth/overview.md
        _add_doc_with_path(
            session,
            proj,
            doc_key="modules/M1-auth/overview",
            path="modules/M1-auth/overview.md",
        )
        # Host doc: lives in modules/M2-billing/spec.md and references the target
        # via a markdown-relative path going up one level.
        host = _add_doc_with_path(
            session, proj, doc_key="modules/M2-billing/spec", path="modules/M2-billing/spec.md"
        )
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="overview",
            heading="Overview",
            level=2,
            position=0,
            body="See [overview](../M1-auth/overview.md) for details.",
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        report = links.rename_cascade(
            session,
            project_id=proj,
            old_doc_key="modules/M1-auth/overview",
            new_doc_key="modules/M1-auth/spec",
            author="human:test",
            path_map={"modules/M1-auth/overview.md": "modules/M1-auth/spec.md"},
        )

        from cod_doc.infra.models import SectionModel

        body = session.get(SectionModel, sec.row_id).body
        assert body == "See [overview](../M1-auth/spec.md) for details."
        assert report.rewritten_sections == 1


def test_rename_cascade_path_only_change_rewrites_markdown(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """When only the document's path changes (doc_key stays), markdown refs still update."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc_with_path(session, proj, doc_key="overview", path="docs/overview.md")
        host = _add_doc_with_path(session, proj, doc_key="src", path="docs/src.md")
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="Read [overview](overview.md).",
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        report = links.rename_cascade(
            session,
            project_id=proj,
            old_doc_key="overview",
            new_doc_key="overview",
            author="human:test",
            path_map={"docs/overview.md": "docs/intro.md"},
        )

        from cod_doc.infra.models import SectionModel

        body = session.get(SectionModel, sec.row_id).body
        assert body == "Read [overview](intro.md)."
        assert report.rewritten_sections == 1


def test_rename_cascade_with_anchor_in_markdown_relative(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Anchor part of a markdown-relative ref must be preserved across the rewrite."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc_with_path(session, proj, doc_key="modules/auth/spec", path="modules/auth/spec.md")
        host = _add_doc_with_path(
            session, proj, doc_key="modules/billing/spec", path="modules/billing/spec.md"
        )
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="Jump to [step 2](../auth/spec.md#step-2).",
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        links.rename_cascade(
            session,
            project_id=proj,
            old_doc_key="modules/auth/spec",
            new_doc_key="modules/auth/v2",
            author="human:test",
            path_map={"modules/auth/spec.md": "modules/auth/v2.md"},
        )

        from cod_doc.infra.models import SectionModel

        body = session.get(SectionModel, sec.row_id).body
        assert body == "Jump to [step 2](../auth/v2.md#step-2)."


def test_rename_cascade_idempotent_on_repeat(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Re-running the cascade with the new path_map (already applied) is a no-op."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc_with_path(session, proj, doc_key="modules/M1/spec", path="modules/M1/spec.md")
        host = _add_doc_with_path(
            session, proj, doc_key="modules/M2/spec", path="modules/M2/spec.md"
        )
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="See [m1](../M1/spec.md) and [[doc:modules/M1/spec]].",
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        report1 = links.rename_cascade(
            session,
            project_id=proj,
            old_doc_key="modules/M1/spec",
            new_doc_key="modules/M1/v2",
            author="human:test",
            path_map={"modules/M1/spec.md": "modules/M1/v2.md"},
        )
        assert report1.rewritten_sections == 1

        # Second run with the SAME old_doc_key/path is a true no-op because
        # the body now references new_doc_key. We simulate the "post-rename"
        # state and ensure idempotency by re-running with already-current keys.
        report2 = links.rename_cascade(
            session,
            project_id=proj,
            old_doc_key="modules/M1/v2",
            new_doc_key="modules/M1/v2",
            author="human:test",
            path_map={"modules/M1/v2.md": "modules/M1/v2.md"},
        )
        assert report2.rewritten_sections == 0
        assert report2.updated_links == 0


def test_rename_cascade_skips_urls_and_anchors(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """URLs and bare anchors must NOT be rewritten regardless of path_map."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _add_doc_with_path(session, proj, doc_key="x", path="x.md")
        host = _add_doc_with_path(session, proj, doc_key="src", path="src.md")
        body = "External: [a](https://example.com/x.md). Anchor: [up](#x). Local: [x](x.md)."
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body=body,
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        links.rename_cascade(
            session,
            project_id=proj,
            old_doc_key="x",
            new_doc_key="x",
            author="human:test",
            path_map={"x.md": "y.md"},
        )

        from cod_doc.infra.models import SectionModel

        new_body = session.get(SectionModel, sec.row_id).body
        assert "https://example.com/x.md" in new_body
        assert "[up](#x)" in new_body
        assert "[x](y.md)" in new_body


def test_doc_service_rename_with_path_change_cascades_markdown(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """End-to-end: DocService.rename(new_path=...) propagates path_map to LinkService."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        target = _add_doc_with_path(
            session, proj, doc_key="modules/M1/overview", path="modules/M1/overview.md"
        )
        host = _add_doc_with_path(
            session, proj, doc_key="modules/M2/spec", path="modules/M2/spec.md"
        )
        sec = docs.add_section(
            session,
            document_id=host,
            anchor="i",
            heading="I",
            level=2,
            position=0,
            body="Read [overview](../M1/overview.md).",
            author="human:test",
        )
        links.resolve_section(session, sec.row_id)

        docs.rename(
            session,
            document_id=target,
            new_doc_key="modules/M1/spec",
            new_path="modules/M1/spec.md",
            author="human:test",
        )

        from cod_doc.infra.models import SectionModel

        body = session.get(SectionModel, sec.row_id).body
        assert body == "Read [overview](../M1/spec.md)."
