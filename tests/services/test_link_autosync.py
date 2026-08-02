"""COD-079: link rows are auto-populated when sections land via doc_service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Sensitivity,
)
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.repositories import (
    LinkRepository,
    ProjectRepository,
)
from cod_doc.services import doc_service, link_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session, slug: str) -> int:
    now = datetime.now(UTC)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug=slug, title=slug, root_path=f"/tmp/{slug}", config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _create_doc(session: Session, project_id: int, doc_key: str, title: str = "T"):
    return doc_service.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        title=title,
        author="human:test",
        owner="human:test",
        sensitivity=Sensitivity.INTERNAL,
        preamble="",
    )


def test_add_section_auto_syncs_link_rows(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """COD-079: a [text](other-doc) markdown link in a section body should
    leave a Link row in the DB without anyone calling sync_section."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "auto")
        # Two documents — body of A links to B.
        doc_a = _create_doc(session, project_id, "doc-a")
        _create_doc(session, project_id, "doc-b")

        section = doc_service.add_section(
            session,
            document_id=doc_a.row_id,
            anchor="see-b",
            heading="See also",
            level=2,
            position=0,
            body="Reference to [Doc B](doc-b#intro) for the upstream details.",
            author="human:test",
        )

        links = LinkRepository(session).list_for_section(section.row_id)
    # One parsed link should now be persisted (unresolved is OK — sync only
    # writes raw rows; resolution is a separate pass).
    assert len(links) == 1
    assert links[0].raw == "[Doc B](doc-b#intro)"


def test_patch_section_replaces_link_rows(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "patch")
        doc_a = _create_doc(session, project_id, "doc-a")
        _create_doc(session, project_id, "doc-b")
        _create_doc(session, project_id, "doc-c")
        section = doc_service.add_section(
            session,
            document_id=doc_a.row_id,
            anchor="ref",
            heading="Refs",
            level=2,
            position=0,
            body="See [B](doc-b).",
            author="human:test",
        )
        # Replace body — old link should disappear, new one appear.
        doc_service.patch_section(
            session,
            document_id=doc_a.row_id,
            anchor="ref",
            new_body="Now we point at [C](doc-c) instead.",
            author="human:test",
        )
        links = LinkRepository(session).list_for_section(section.row_id)
    raws = {lk.raw for lk in links}
    assert raws == {"[C](doc-c)"}


def test_link_backfill_picks_up_legacy_sections(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A section that landed via raw repo writes (importer-shaped path)
    has no link rows; ``sync_section`` recovers them. This is the runtime
    contract the new ``cod-doc link backfill`` CLI relies on."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session, "back")
        doc_a = _create_doc(session, project_id, "doc-a")
        _create_doc(session, project_id, "doc-b")
        section = doc_service.add_section(
            session,
            document_id=doc_a.row_id,
            anchor="x",
            heading="X",
            level=2,
            position=0,
            body="Stale body without links.",
            author="human:test",
        )
        # Simulate "post-import body update without sync" — wipe link rows
        # then mutate section body in raw fashion.
        LinkRepository(session).delete_for_section(section.row_id)
        from cod_doc.infra.models import SectionModel

        sec_model = session.get(SectionModel, section.row_id)
        sec_model.body = "Now linking [B](doc-b#here)."
        session.flush()

        # Pre-condition: no link rows.
        assert LinkRepository(session).list_for_section(section.row_id) == []

        # Call sync (the CLI iterates all sections doing exactly this).
        link_service.sync_section(session, section.row_id)
        links = LinkRepository(session).list_for_section(section.row_id)
    assert len(links) == 1
