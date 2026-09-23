"""ADO-213: `doc_service.delete_section` — the missing third of the section cycle.

Until this function existed there was no way to remove a section from the DB —
not in the service, not on the CLI, not on MCP. `import_or_update_markdown`
could patch and append, never drop, so a heading that left a markdown file
stayed in the DB forever; `doc accept` then pinned the file hash and
`detect_drift` answered `in_sync` over the divergence.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import (
    Document,
    DocumentStatus,
    DocumentType,
    EntityKind,
    Sensitivity,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, LinkModel, ProjectModel, RevisionModel
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _add_project(session: Session, slug: str = "p") -> int:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _new_doc(session: Session, project_id: int, doc_key: str = "handbook") -> Document:
    return docs.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        title="Handbook",
        author="human:dakh",
        owner="human:dakh",
        sensitivity=Sensitivity.INTERNAL,
        preamble="Intro paragraph.",
    )


def _seed_three(session: Session) -> int:
    """project + doc + three sections at positions 0, 1, 2. Returns doc row_id."""
    proj_id = _add_project(session)
    doc = _new_doc(session, proj_id)
    assert doc.row_id is not None
    for i, anchor in enumerate(("alpha", "beta", "gamma")):
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor=anchor,
            heading=anchor.capitalize(),
            level=2,
            position=i,
            body=f"Body of {anchor}.",
            author="human:dakh",
        )
    return doc.row_id


def test_delete_section_removes_the_row(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        doc_id = _seed_three(session)
        removed = docs.delete_section(
            session, document_id=doc_id, anchor="beta", author="human:dakh"
        )

    assert removed.anchor == "beta"
    assert removed.heading == "Beta"
    assert removed.position == 1

    with transactional(factory) as session:
        anchors = [s.anchor for s in docs.get_sections(session, doc_id)]
    assert anchors == ["alpha", "gamma"]


def test_delete_section_renumbers_the_rest(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """`position` is an index in the document, so it must stay dense (0…n-1)."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        doc_id = _seed_three(session)
        docs.delete_section(session, document_id=doc_id, anchor="alpha", author="human:dakh")

    with transactional(factory) as session:
        sections = docs.get_sections(session, doc_id)
    assert [(s.anchor, s.position) for s in sections] == [("beta", 0), ("gamma", 1)]


def test_delete_then_add_lands_at_the_end(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """The regression a hole in `position` would cause: append collides or skips."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        doc_id = _seed_three(session)
        docs.delete_section(session, document_id=doc_id, anchor="beta", author="human:dakh")
        existing = docs.get_sections(session, doc_id)
        docs.add_section(
            session,
            document_id=doc_id,
            anchor="delta",
            heading="Delta",
            level=2,
            position=max(s.position for s in existing) + 1,
            body="Body of delta.",
            author="human:dakh",
        )

    with transactional(factory) as session:
        sections = docs.get_sections(session, doc_id)
    assert [(s.anchor, s.position) for s in sections] == [
        ("alpha", 0),
        ("gamma", 1),
        ("delta", 2),
    ]


def test_delete_section_removes_the_body_from_the_rendered_document(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        doc_id = _seed_three(session)
        docs.delete_section(session, document_id=doc_id, anchor="beta", author="human:dakh")
        body = docs.render_body(session, doc_id)

    assert body is not None
    assert "Body of beta." not in body
    assert "Body of alpha." in body
    assert "Body of gamma." in body


def test_delete_section_writes_a_revision_carrying_the_body(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ADO-040: the write leaves a trace, and the trace holds what was lost.

    ADO-213: the trace hangs on the living document, not on the deleted row —
    a row id that no longer exists (and may be reused) cannot own history.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        doc_id = _seed_three(session)
        docs.delete_section(session, document_id=doc_id, anchor="beta", author="agent:claude")

    with transactional(factory) as session:
        revision_id = rev.head_for_entity(session, EntityKind.DOCUMENT, doc_id)
        assert revision_id is not None
        model = session.execute(
            select(RevisionModel).where(RevisionModel.revision_id == revision_id)
        ).scalar_one()

    assert model.author == "agent:claude"
    assert model.reason == "delete_section"
    diff = json.loads(model.diff)
    assert diff["op"] == "delete_section"
    assert (diff["anchor"], diff["heading"], diff["position"]) == ("beta", "Beta", 1)
    assert "-Body of beta." in diff["diff"]
    assert "/dev/null" in diff["diff"]


def test_reverting_a_section_delete_is_refused_explicitly(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ADO-213: the delete revision says «not revertible» itself, not via a
    LookupError on a row that is gone."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        doc_id = _seed_three(session)
        docs.delete_section(session, document_id=doc_id, anchor="beta", author="human:dakh")
        revision_id = rev.head_for_entity(session, EntityKind.DOCUMENT, doc_id)
        assert revision_id is not None

        with pytest.raises(rev.RevertNotSupportedError):
            rev.revert(session, revision_id, author="human:dakh")


def test_old_revision_of_a_deleted_section_never_reverts_onto_its_successor(
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    """ai-review #85 (major): `section.row_id` has no AUTOINCREMENT.

    Delete the section with the highest id and SQLite hands that id to the next
    new section, so the dead section's revisions now read as the new one's.
    Reverting such a revision used to patch the old body into a stranger.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        doc_id = _seed_three(session)
        gamma = docs.patch_section(
            session,
            document_id=doc_id,
            anchor="gamma",
            new_body="Gamma, second draft.",
            author="human:dakh",
        )
        assert gamma.row_id is not None
        gamma_patch = rev.head_for_entity(session, EntityKind.SECTION, gamma.row_id)
        assert gamma_patch is not None

        docs.delete_section(session, document_id=doc_id, anchor="gamma", author="human:dakh")
        delta = docs.add_section(
            session,
            document_id=doc_id,
            anchor="delta",
            heading="Delta",
            level=2,
            position=2,
            body="Body of delta.",
            author="human:dakh",
        )
        # The premise of the defect: the id really was reused.
        assert delta.row_id == gamma.row_id

        with pytest.raises(rev.RevertNotSupportedError):
            rev.revert(session, gamma_patch, author="human:dakh")

        bodies = {s.anchor: s.body for s in docs.get_sections(session, doc_id)}
    assert bodies["delta"] == "Body of delta."


def test_delete_section_emits_an_activity_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        doc_id = _seed_three(session)
        docs.delete_section(session, document_id=doc_id, anchor="beta", author="agent:claude")

    with transactional(factory) as session:
        event = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "doc.section_deleted")
        ).scalar_one()

    assert event.scope_id == "handbook#beta"
    # ADR-012: derived from `author` through the single derivation point.
    assert event.actor_kind == "agent"
    assert event.payload["heading"] == "Beta"
    assert event.payload["position"] == 1


def test_delete_section_rejects_an_unknown_anchor(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        doc_id = _seed_three(session)
        with pytest.raises(docs.SectionNotFoundError, match="handbook#nope"):
            docs.delete_section(session, document_id=doc_id, anchor="nope", author="human:dakh")


def test_delete_section_rejects_an_unknown_document(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_three(session)
        with pytest.raises(docs.DocumentNotFoundError):
            docs.delete_section(session, document_id=99999, anchor="alpha", author="human:dakh")


def test_delete_section_takes_its_links_with_it(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Parsed link rows hang off the section; leaving them would orphan them."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        assert doc.row_id is not None
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="alpha",
            heading="Alpha",
            level=2,
            position=0,
            body="See [[handbook#alpha]].",
            author="human:dakh",
        )
        doc_id = doc.row_id

    with transactional(factory) as session:
        before = session.execute(select(LinkModel)).scalars().all()
        assert before, "фикстура бесполезна, если ссылка не распарсилась"
        docs.delete_section(session, document_id=doc_id, anchor="alpha", author="human:dakh")

    with transactional(factory) as session:
        assert session.execute(select(LinkModel)).scalars().all() == []
