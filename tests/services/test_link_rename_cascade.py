"""COD-013 / COD-014a / RFL-071: rename_cascade — key + path_map rewrites."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import DocumentStatus, DocumentType, EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import link_service as links
from cod_doc.services import revision_service as rev

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


# ============================================================================ #
# rename_cascade — canonical key rewrite                                       #
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

        # Second run with already-current keys is a true no-op.
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
