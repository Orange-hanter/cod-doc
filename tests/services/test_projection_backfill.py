"""ADO-022: `backfill_projection_fidelity` — the cure the export guard names.

Rows written before migration `0025_projection_fidelity` have NULL
`frontmatter_raw` / `title_in_body`, which the renderer reads as "nothing to
preserve". Backfill recovers those two columns from the files on disk — and
only those two: unlike `doc import` it must not push the file's frontmatter
back over metadata that changed in the DB and was never exported.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import import_service
from cod_doc.services import projection_service as proj

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

RAW = "---\ntype: guide\nstatus: draft\nowner: docs\n---\n\n# Title\n\nBody.\n"


@pytest.fixture
def root_path(tmp_path: Path) -> Path:
    p = tmp_path / "mirror"
    p.mkdir()
    return p


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed_project(session: Session, root: Path) -> int:
    now = datetime.now(UTC)
    model = ProjectModel(slug="p", title="P", root_path=str(root), config_json={})
    model.created = now
    model.updated = now
    session.add(model)
    session.flush()
    return model.row_id


def _seed_legacy(
    session: Session,
    root: Path,
    *,
    rel_path: str = "d.md",
    raw: str = RAW,
    on_disk: bool = True,
) -> tuple[int, int]:
    """Import `raw`, optionally put it on disk, then age the row to pre-0025."""
    project_id = _seed_project(session, root)
    doc = import_service.import_markdown(
        session,
        project_id=project_id,
        doc_key=rel_path.removesuffix(".md"),
        raw_markdown=raw,
        author="human:test",
    ).document
    assert doc.row_id is not None
    model = session.get(DocumentModel, doc.row_id)
    assert model is not None
    model.path = rel_path
    model.frontmatter_raw = None
    model.title_in_body = None
    model.content_sha256_head = _sha256(raw)
    model.projection_hash = _sha256(f"pre-ADO-010 render of {raw}")
    session.flush()

    if on_disk:
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(raw, encoding="utf-8")
    return project_id, doc.row_id


def test_backfill_recovers_both_columns_from_disk(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id, doc_id = _seed_legacy(session, root_path)
        report = proj.backfill_projection_fidelity(session, project_id, root_path=root_path)

        model = session.get(DocumentModel, doc_id)
        assert model is not None
        assert model.frontmatter_raw == "type: guide\nstatus: draft\nowner: docs"
        assert model.title_in_body is True

    assert report.scanned == 1
    assert report.filled == 1
    assert report.items[0].action is proj.FidelityBackfillAction.FILLED


def test_backfill_records_an_absent_frontmatter_as_empty_not_null(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """`''` is what tells the renderer "this file had none — invent nothing"."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id, doc_id = _seed_legacy(session, root_path, raw="## Section\n\nBody.\n")
        proj.backfill_projection_fidelity(session, project_id, root_path=root_path)

        model = session.get(DocumentModel, doc_id)
        assert model is not None
        assert model.frontmatter_raw == ""
        assert model.title_in_body is False


def test_backfill_does_not_revert_db_side_metadata(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """The reason the cure is not `doc import`.

    An import would apply `status: draft` from the file back onto a row that
    was accepted in the DB but never exported. Backfill touches only the shape,
    so the DB stays authoritative and the export carries the new status out —
    in the source's key order.
    """
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id, doc_id = _seed_legacy(session, root_path)
        model = session.get(DocumentModel, doc_id)
        assert model is not None
        model.status = "active"
        session.flush()

        proj.backfill_projection_fidelity(session, project_id, root_path=root_path)

        model = session.get(DocumentModel, doc_id)
        assert model is not None
        assert model.status == "active"
        rendered = proj.render_markdown(session, doc_id)

    assert "status: active" in rendered
    assert rendered.index("type:") < rendered.index("status:") < rendered.index("owner:")


def test_backfill_leaves_a_row_with_no_file_alone(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id, doc_id = _seed_legacy(session, root_path, on_disk=False)
        report = proj.backfill_projection_fidelity(session, project_id, root_path=root_path)

        model = session.get(DocumentModel, doc_id)
        assert model is not None
        assert model.frontmatter_raw is None
        assert model.title_in_body is None

    assert report.file_missing == 1
    assert report.filled == 0
    assert report.items[0].action is proj.FidelityBackfillAction.FILE_MISSING


def test_backfill_skips_a_file_that_is_our_own_last_export(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """A rendered file has no authored shape to recover — leave the row alone."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id, doc_id = _seed_legacy(session, root_path)
        model = session.get(DocumentModel, doc_id)
        assert model is not None
        model.projection_hash = _sha256(RAW)
        session.flush()

        report = proj.backfill_projection_fidelity(session, project_id, root_path=root_path)

        model = session.get(DocumentModel, doc_id)
        assert model is not None
        assert model.frontmatter_raw is None

    assert report.skipped == 1
    assert report.filled == 0


def test_backfill_is_idempotent(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id, _doc_id = _seed_legacy(session, root_path)
        first = proj.backfill_projection_fidelity(session, project_id, root_path=root_path)
        second = proj.backfill_projection_fidelity(session, project_id, root_path=root_path)

    assert first.filled == 1
    assert second.scanned == 0
    assert second.filled == 0
    assert second.items == []


def test_backfill_dry_run_writes_nothing(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id, doc_id = _seed_legacy(session, root_path)
        report = proj.backfill_projection_fidelity(
            session, project_id, root_path=root_path, dry_run=True
        )

        model = session.get(DocumentModel, doc_id)
        assert model is not None
        assert model.frontmatter_raw is None
        assert model.title_in_body is None

    assert report.filled == 1
    assert report.items[0].action is proj.FidelityBackfillAction.FILLED
