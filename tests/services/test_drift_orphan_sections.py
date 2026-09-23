"""ADO-213: `detect_drift` reports sections the file no longer has.

This class of divergence was invisible to all four `DriftStatus` values. The
statuses partition *content-hash* states, and an orphaned section survives
every one of them: `doc accept` pins `content_sha256_head`, the file keeps
matching the pin, and `detect_drift` answers `in_sync` while the DB body
carries headings the file dropped. The signal therefore sits beside the
status, exactly as `metadata_mismatch` does for frontmatter (ADO-092) — not as
a fifth status.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import import_service
from cod_doc.services import projection_service as proj
from cod_doc.services.projection_service._types import ORPHAN_SECTION_COUNT_KEY

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

TWO_SECTIONS = """---
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
"""

ONE_SECTION = """---
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


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    model = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    model.created = now
    model.updated = now
    session.add(model)
    session.flush()
    return model.row_id


def _accepted_document(session: Session, root_path: Path, *, file_text: str, db_text: str) -> int:
    """A document imported from `db_text`, with `file_text` on disk and accepted.

    "Accepted" is the state the bug hides behind: `content_sha256_head` holds
    the hash of what is on disk, so no content-hash comparison can object.
    """
    project_id = _seed_project(session)
    report = import_service.import_or_update_markdown(
        session,
        project_id=project_id,
        doc_key="master",
        raw_markdown=db_text,
        author="human:test",
        source_sha256=_sha(db_text),
        path="master.md",
    )
    doc_id = report.document.row_id
    assert doc_id is not None

    (root_path / "master.md").write_text(file_text, encoding="utf-8")
    model = session.get(DocumentModel, doc_id)
    assert model is not None
    model.path = "master.md"
    model.content_sha256_head = _sha(file_text)
    session.flush()
    return doc_id


def test_orphan_section_is_reported_under_in_sync(engine_with_schema, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The whole point: `in_sync` and an orphaned section at the same time."""
    factory = make_session_factory(engine_with_schema)
    root = tmp_path / "repo"
    root.mkdir()
    with transactional(factory) as session:
        doc_id = _accepted_document(session, root, file_text=ONE_SECTION, db_text=TWO_SECTIONS)
        report = proj.detect_drift(session, doc_id, root_path=root)

    assert report.status is proj.DriftStatus.IN_SYNC
    assert report.orphan_sections == ("context-map",)


def test_no_orphans_when_the_file_carries_every_section(engine_with_schema, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The signal has to stay rare — a matching file reports nothing."""
    factory = make_session_factory(engine_with_schema)
    root = tmp_path / "repo"
    root.mkdir()
    with transactional(factory) as session:
        doc_id = _accepted_document(session, root, file_text=TWO_SECTIONS, db_text=TWO_SECTIONS)
        report = proj.detect_drift(session, doc_id, root_path=root)

    assert report.orphan_sections == ()


def test_a_section_only_the_file_has_is_not_an_orphan(engine_with_schema, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Direction matters: the DB missing a heading is a pending import, not this."""
    factory = make_session_factory(engine_with_schema)
    root = tmp_path / "repo"
    root.mkdir()
    with transactional(factory) as session:
        doc_id = _accepted_document(session, root, file_text=TWO_SECTIONS, db_text=ONE_SECTION)
        report = proj.detect_drift(session, doc_id, root_path=root)

    assert report.orphan_sections == ()


def test_db_authored_deeper_section_is_not_an_orphan(engine_with_schema, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Level 2 only — the file never had a way to carry an `###` section."""
    factory = make_session_factory(engine_with_schema)
    root = tmp_path / "repo"
    root.mkdir()
    with transactional(factory) as session:
        doc_id = _accepted_document(session, root, file_text=TWO_SECTIONS, db_text=TWO_SECTIONS)
        docs.add_section(
            session,
            document_id=doc_id,
            anchor="db-only",
            heading="DB only",
            level=3,
            position=9,
            body="Authored in the DB.",
            author="human:test",
        )
        report = proj.detect_drift(session, doc_id, root_path=root)

    assert report.orphan_sections == ()


def test_a_missing_file_reports_no_orphans(engine_with_schema, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """No file, nothing to compare against — `missing` already says it all."""
    factory = make_session_factory(engine_with_schema)
    root = tmp_path / "repo"
    root.mkdir()
    with transactional(factory) as session:
        doc_id = _accepted_document(session, root, file_text=ONE_SECTION, db_text=TWO_SECTIONS)
        (root / "master.md").unlink()
        report = proj.detect_drift(session, doc_id, root_path=root)

    assert report.status is proj.DriftStatus.MISSING
    assert report.orphan_sections == ()


def test_project_drift_counts_orphans_and_lists_the_document(
    engine_with_schema, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    """An `in_sync` document with an orphan still has to reach `issues`."""
    factory = make_session_factory(engine_with_schema)
    root = tmp_path / "repo"
    root.mkdir()
    with transactional(factory) as session:
        _accepted_document(session, root, file_text=ONE_SECTION, db_text=TWO_SECTIONS)
        report = proj.detect_project_drift(session, 1, root_path=root)

    assert report.counts[ORPHAN_SECTION_COUNT_KEY] == 1
    assert [item.doc_key for item in report.issues] == ["master"]
    assert report.issues[0].report.status is proj.DriftStatus.IN_SYNC
    assert report.issues[0].report.orphan_sections == ("context-map",)


def test_replace_import_clears_the_orphan_signal(engine_with_schema, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The cure the CLI points at actually works."""
    factory = make_session_factory(engine_with_schema)
    root = tmp_path / "repo"
    root.mkdir()
    with transactional(factory) as session:
        doc_id = _accepted_document(session, root, file_text=ONE_SECTION, db_text=TWO_SECTIONS)
        proj.import_document(
            session,
            1,
            root / "master.md",
            author="human:test",
            root_path=root,
            replace=True,
        )
        report = proj.detect_drift(session, doc_id, root_path=root)

    assert report.orphan_sections == ()


# ADO-213: the anchor `scenario_service.export` writes by hand, against the
# anchor `_slugify` derives from the heading it wrote beside it. They differ.
SCENARIO_HEADING = "SCN-001 — Completing a task is visible in plan_progress"

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

## {SCENARIO_HEADING}

Scenario body.
"""


def test_a_hand_authored_anchor_is_not_an_orphan(engine_with_schema, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """ADO-213: the heading is right there in the file — the section is not gone.

    Identity was the stored anchor against the anchor `_slugify` re-derives
    from the file's heading, and nothing ever guaranteed the two agree.
    `scenario_service.export` stores `scn-001` and writes
    `## SCN-001 — …`, which re-derives to `scn-001--completing-a-task-…`.
    Measured on a copy of the live cod-doc DB, that mismatch alone accounted
    for 16 of the 29 documents and 80 of the 146 sections the signal reported.
    """
    factory = make_session_factory(engine_with_schema)
    root = tmp_path / "repo"
    root.mkdir()
    with transactional(factory) as session:
        doc_id = _accepted_document(session, root, file_text=SCENARIO_FILE, db_text=ONE_SECTION)
        docs.add_section(
            session,
            document_id=doc_id,
            anchor="scn-001",
            heading=SCENARIO_HEADING,
            level=2,
            position=1,
            body="Scenario body.",
            author="human:test",
        )
        report = proj.detect_drift(session, doc_id, root_path=root)

    assert report.orphan_sections == ()


def test_a_heading_the_file_really_dropped_is_still_an_orphan(  # type: ignore[no-untyped-def]
    engine_with_schema, tmp_path: Path
) -> None:
    """The heading fallback must not blunt the signal it is guarding."""
    factory = make_session_factory(engine_with_schema)
    root = tmp_path / "repo"
    root.mkdir()
    with transactional(factory) as session:
        doc_id = _accepted_document(session, root, file_text=SCENARIO_FILE, db_text=ONE_SECTION)
        docs.add_section(
            session,
            document_id=doc_id,
            anchor="scn-001",
            heading=SCENARIO_HEADING,
            level=2,
            position=1,
            body="Scenario body.",
            author="human:test",
        )
        docs.add_section(
            session,
            document_id=doc_id,
            anchor="long-gone",
            heading="A heading nobody kept",
            level=2,
            position=2,
            body="Stale.",
            author="human:test",
        )
        report = proj.detect_drift(session, doc_id, root_path=root)

    assert report.orphan_sections == ("long-gone",)
