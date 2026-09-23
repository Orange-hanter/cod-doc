"""ADO-213: `import_or_update_markdown` — orphaned sections, named and removable.

The bug this file pins down: the update path walked the *file's* sections and
patched or appended each one, and had no path at all for a section that left
the file. Remove a heading from markdown, run `doc import`, and the section
stayed in the DB forever — while new sections piled up at the end in an order
the file disagrees with. `_set_content_sha` then pinned the file hash and
`detect_drift` answered `in_sync` from then on, so nothing ever flagged it.
Live evidence at the time: `MASTER` of project Restate held 9 sections against
5 in `MASTER.md`; `docs/mcp-integration` in cod-doc's own DB held 36 against 14.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import doc_service, import_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

THREE_SECTIONS = """---
title: Master
type: guide
status: active
owner: human:dakh
---
# Master

Preamble.

## Executive Summary

Summary text.

## Context Map

Stale map.

## Validation

Validation text.
"""

# Same file with `Context Map` removed and a new section appended. Re-importing
# this is the exact shape of the Restate cutover that produced the orphans.
TWO_SECTIONS_PLUS_NEW = """---
title: Master
type: guide
status: active
owner: human:dakh
---
# Master

Preamble.

## Executive Summary

Summary text v2.

## Snapshot

Fresh snapshot.

## Validation

Validation text.
"""


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug="demo", title="Demo", root_path="/tmp/demo", config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _import(
    session: Session,
    project_id: int,
    raw: str,
    *,
    replace: bool = False,
    force: bool = False,
) -> import_service.ImportReport:
    return import_service.import_or_update_markdown(
        session,
        project_id=project_id,
        doc_key="master",
        raw_markdown=raw,
        author="human:test",
        source_sha256=_sha(raw),
        replace=replace,
        force=force,
    )


def _anchors(session: Session, document_id: int) -> list[str]:
    return [s.anchor for s in doc_service.get_sections(session, document_id)]


def _file_anchors(raw: str) -> list[str]:
    return [s.anchor for s in import_service.parse_markdown(raw).sections]


# --------------------------------------------------------------------------- #
# The bug itself                                                               #
# --------------------------------------------------------------------------- #


def test_default_mode_keeps_the_orphan_and_says_so(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """`doc import` stays additive — but stops being silent about it.

    Dropping a DB section because someone handed the importer a partial file
    is worse than carrying an orphan, so the default must not delete. What it
    must not do any more is keep quiet.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        first = _import(session, project_id, THREE_SECTIONS)
        doc_id = first.document.row_id
        assert doc_id is not None

        report = _import(session, project_id, TWO_SECTIONS_PLUS_NEW)

        assert report.orphan_sections == ["context-map"]
        assert report.deleted_sections == []
        assert report.reordered is False
        assert "context-map" in _anchors(session, doc_id)
        assert report.to_dict()["orphan_sections"] == ["context-map"]


def test_replace_mode_deletes_the_section_that_left_the_file(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """The regression, end to end: import, drop a heading, re-import, gone."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        first = _import(session, project_id, THREE_SECTIONS)
        doc_id = first.document.row_id
        assert doc_id is not None
        assert _anchors(session, doc_id) == ["executive-summary", "context-map", "validation"]

        report = _import(session, project_id, TWO_SECTIONS_PLUS_NEW, replace=True)

        assert report.orphan_sections == ["context-map"]
        assert report.deleted_sections == ["context-map"]
        assert "context-map" not in _anchors(session, doc_id)


def test_replace_mode_brings_db_order_to_file_order(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """The second half of the bug: appended sections landed at the end.

    `Snapshot` sits between `Executive Summary` and `Validation` in the file,
    but the additive path can only append, so without the reorder it lands
    after `Validation` and the DB body reads in an order the file disagrees
    with.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        first = _import(session, project_id, THREE_SECTIONS)
        doc_id = first.document.row_id
        assert doc_id is not None

        additive = _import(session, project_id, TWO_SECTIONS_PLUS_NEW)
        assert additive.reordered is False
        assert _anchors(session, doc_id) == [
            "executive-summary",
            "context-map",
            "validation",
            "snapshot",
        ]

        report = _import(session, project_id, TWO_SECTIONS_PLUS_NEW, replace=True)

        assert report.reordered is True
        assert _anchors(session, doc_id) == _file_anchors(TWO_SECTIONS_PLUS_NEW)
        assert [s.position for s in doc_service.get_sections(session, doc_id)] == [0, 1, 2]


def test_replace_mode_makes_the_rendered_body_match_the_file(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        first = _import(session, project_id, THREE_SECTIONS)
        doc_id = first.document.row_id
        assert doc_id is not None
        _import(session, project_id, TWO_SECTIONS_PLUS_NEW, replace=True)
        body = doc_service.render_body(session, doc_id)

    assert body is not None
    assert "Stale map." not in body
    assert body.index("Fresh snapshot.") < body.index("Validation text.")


def test_replace_mode_is_idempotent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A second replace over the same file must find nothing to do."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        _import(session, project_id, THREE_SECTIONS)
        _import(session, project_id, TWO_SECTIONS_PLUS_NEW, replace=True)

        again = _import(session, project_id, TWO_SECTIONS_PLUS_NEW, replace=True)

        assert again.orphan_sections == []
        assert again.deleted_sections == []
        assert again.reordered is False


# --------------------------------------------------------------------------- #
# Trace and scope                                                              #
# --------------------------------------------------------------------------- #


def test_replace_mode_leaves_a_trace(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ADO-040: the deletion and the reorder are both write-paths."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        _import(session, project_id, THREE_SECTIONS)
        _import(session, project_id, TWO_SECTIONS_PLUS_NEW, replace=True)

    with transactional(factory) as session:
        deleted = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "doc.section_deleted")
        ).scalar_one()
        reordered = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "doc.sections_reordered")
        ).scalar_one()

    assert deleted.scope_id == "master#context-map"
    assert reordered.scope_id == "master"
    assert reordered.payload["moved"]


def test_a_db_authored_section_is_not_an_orphan(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Only level-2 sections count: `## ` is all `parse_markdown` can produce.

    A section at another level was authored in the DB and the file was never
    able to carry it, so the file's silence about it is not evidence.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        first = _import(session, project_id, THREE_SECTIONS)
        doc_id = first.document.row_id
        assert doc_id is not None
        doc_service.add_section(
            session,
            document_id=doc_id,
            anchor="db-only",
            heading="DB only",
            level=3,
            position=99,
            body="Authored in the DB.",
            author="human:test",
        )

        report = _import(session, project_id, THREE_SECTIONS, replace=True)

        assert report.orphan_sections == []
        assert report.deleted_sections == []
        assert "db-only" in _anchors(session, doc_id)


def test_replace_on_a_brand_new_document_reports_nothing(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """The create path has no DB sections to orphan — the fields stay empty."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        report = _import(session, project_id, THREE_SECTIONS, replace=True)

    assert report.created is True
    assert report.orphan_sections == []
    assert report.deleted_sections == []
    assert report.reordered is False


# --------------------------------------------------------------------------- #
# ADO-213: identity is anchor *then* heading, and `--replace` has a floor       #
# --------------------------------------------------------------------------- #

# The shape `scenario_service.export` writes: the anchor is hand-assigned
# (`scn-001`), the heading carries the same id plus prose, and `_slugify` of
# that heading gives `scn-001--completing-a-task-…` — a different string.
HAND_AUTHORED_ANCHOR = "scn-001"
HAND_AUTHORED_HEADING = "SCN-001 — Completing a task is visible in plan_progress"

SCENARIO_FILE = f"""---
title: Master
type: guide
status: active
owner: human:dakh
---
# Master

Preamble.

## Executive Summary

Summary text.

## {HAND_AUTHORED_HEADING}

Rewritten body.
"""

NO_SECTIONS_AT_ALL = """---
title: Master
type: guide
status: active
owner: human:dakh
---
"""


ONE_SECTION_ONLY = """---
title: Master
type: guide
status: active
owner: human:dakh
---
# Master

Preamble.

## Executive Summary

Summary text.
"""


def _seed_scenario_doc(session: Session, project_id: int) -> tuple[int, int]:
    """Document with one file-derived section and one hand-anchored one."""
    first = _import(session, project_id, ONE_SECTION_ONLY)
    doc_id = first.document.row_id
    assert doc_id is not None
    section = doc_service.add_section(
        session,
        document_id=doc_id,
        anchor=HAND_AUTHORED_ANCHOR,
        heading=HAND_AUTHORED_HEADING,
        level=2,
        position=1,
        body="Original body.",
        author="human:test",
    )
    assert section.row_id is not None
    return doc_id, section.row_id


def test_a_hand_authored_anchor_is_not_an_orphan(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ADO-213: the file has the heading, so the section is in the file.

    Anchor-only identity called it missing, because `_slugify` of the heading
    is not the anchor anybody stored. On the live cod-doc DB that single
    mistake produced 16 documents / 80 sections of false orphans — the whole
    `docs/system/scenarios/*` corpus.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        _seed_scenario_doc(session, project_id)

        report = _import(session, project_id, SCENARIO_FILE)

        assert report.orphan_sections == []
        assert report.deleted_sections == []


def test_replace_patches_a_hand_authored_anchor_in_place(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ADO-213: matched by heading, so patched — never deleted and re-added.

    The delete-and-recreate path detached the revision lineage (`revision
    revert` then raises `LookupError`), moved the section under an anchor no
    `#scn-001` reference points at, and cascaded its `doc_comment` rows away.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        doc_id, section_row_id = _seed_scenario_doc(session, project_id)

        report = _import(session, project_id, SCENARIO_FILE, replace=True)

        assert report.deleted_sections == []
        assert _anchors(session, doc_id) == ["executive-summary", HAND_AUTHORED_ANCHOR]
        survivor = next(
            s for s in doc_service.get_sections(session, doc_id) if s.anchor == HAND_AUTHORED_ANCHOR
        )
        # Same row, so the revision chain and every `#scn-001` link still land.
        assert survivor.row_id == section_row_id
        assert survivor.body == "Rewritten body."
        assert (
            session.execute(
                select(ActivityEventModel).where(ActivityEventModel.kind == "doc.section_deleted")
            ).first()
            is None
        )


def test_replace_refuses_to_empty_a_document_from_a_sectionless_file(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ADO-213: a truncated write is indistinguishable from "the body is empty".

    `click.Path(exists=True)` happily accepts a zero-byte file, and a
    half-saved editor buffer parses to frontmatter and nothing else. Both used
    to delete every section, then pin the file hash — and the emptied document
    read `in_sync` from then on.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        doc_id = _import(session, project_id, THREE_SECTIONS).document.row_id
        assert doc_id is not None

    for raw in (NO_SECTIONS_AT_ALL, ""):
        with (
            pytest.raises(import_service.ReplaceWouldEmptyError),
            transactional(factory) as session,
        ):
            _import(session, project_id, raw, replace=True)

    with transactional(factory) as session:
        assert _anchors(session, doc_id) == _file_anchors(THREE_SECTIONS)


def test_replace_with_force_empties_the_document_when_asked(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """The refusal is a floor, not a wall: `force=True` is the way through."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        doc_id = _import(session, project_id, THREE_SECTIONS).document.row_id
        assert doc_id is not None

        report = _import(session, project_id, NO_SECTIONS_AT_ALL, replace=True, force=True)

        assert report.deleted_sections == _file_anchors(THREE_SECTIONS)
        assert _anchors(session, doc_id) == []


def test_reorder_leaves_a_section_the_file_cannot_carry_where_it_is(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ADO-213: sorting *all* rows pushed a level-3 section to the document tail.

    That is a content move — it changes what `document_body` renders — made on
    no evidence at all, since the file was never able to carry the section.
    Only the level-2 rows take part now, shuffled between the slots they
    already hold.
    """
    two_sections = ONE_SECTION_ONLY + "\n## Validation\n\nValidation text.\n"
    reordered_file = (
        ONE_SECTION_ONLY.replace(
            "## Executive Summary\n\nSummary text.\n",
            "## Validation\n\nValidation text.\n",
        )
        + "\n## Executive Summary\n\nSummary text.\n"
    )
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        doc_id = _import(session, project_id, ONE_SECTION_ONLY).document.row_id
        assert doc_id is not None
        doc_service.add_section(
            session,
            document_id=doc_id,
            anchor="db-only",
            heading="DB only",
            level=3,
            position=1,
            body="Authored in the DB.",
            author="human:test",
        )
        _import(session, project_id, two_sections)
        before = {s.anchor: s.position for s in doc_service.get_sections(session, doc_id)}
        assert before == {"executive-summary": 0, "db-only": 1, "validation": 2}

        report = _import(session, project_id, reordered_file, replace=True)

        after = {s.anchor: s.position for s in doc_service.get_sections(session, doc_id)}
        assert report.reordered is True
        assert after["db-only"] == before["db-only"]
        assert after == {"validation": 0, "db-only": 1, "executive-summary": 2}
