"""ADO-010 (audit finding F7): import → export must not damage the document.

The audit found `doc export` gluing the preamble onto the first heading,
dropping the H1, and rewriting `type`. These tests pin the repaired contract
against **real repository documents** — the corpus the pilots will meet — plus
the guards that stop an export from overwriting content cod-doc did not write.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import import_service
from cod_doc.services import projection_service as proj

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]

# One document per shape that used to break the round-trip:
#   audit-report / capability — types the DB enum cannot store (ADO-015), so the
#     file must keep the type it was written with instead of being rewritten to
#     `module-spec`;
#   execution-plan — carries `title:` in frontmatter *and* an H1;
#   guide / standard / architecture / vision — flow-style lists, unquoted dates,
#     long preambles ahead of the first `##`;
#   HANDBOOK — no frontmatter at all: nothing may be invented for it.
REPO_DOCS = [
    "docs/system/audit/2026-07-29-state-of-the-project.md",
    "docs/system/capabilities/project-bootstrap.md",
    "docs/system/roadmap/cod-doc-task-plan.md",
    "docs/adoption-playbook.md",
    "docs/system/standards/frontmatter.md",
    "docs/system/ARCHITECTURE.md",
    "docs/system/VISION.md",
    "docs/HANDBOOK.md",
]


@pytest.fixture
def root_path(tmp_path: Path) -> Path:
    p = tmp_path / "mirror"
    p.mkdir()
    return p


def _seed_project(session: Session, root: Path) -> int:
    now = datetime.now(UTC)
    model = ProjectModel(slug="p", title="P", root_path=str(root), config_json={})
    model.created = now
    model.updated = now
    session.add(model)
    session.flush()
    return model.row_id


def _import_file(session: Session, project_id: int, rel_path: str, raw: str) -> int:
    """Import markdown as the document living at `rel_path`."""
    doc = import_service.import_markdown(
        session,
        project_id=project_id,
        doc_key=rel_path.removesuffix(".md"),
        raw_markdown=raw,
        author="human:test",
    )
    assert doc.row_id is not None
    model = session.get(DocumentModel, doc.row_id)
    assert model is not None
    model.path = rel_path
    session.flush()
    return doc.row_id


# ============================================================================ #
# Byte-identical round-trip on real repository documents                        #
# ============================================================================ #


@pytest.mark.parametrize("rel_path", REPO_DOCS)
def test_repo_doc_roundtrip_is_byte_identical(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
    rel_path: str,
) -> None:
    """import → export reproduces the source file byte for byte."""
    source = REPO_ROOT / rel_path
    raw = source.read_text(encoding="utf-8")
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, rel_path, raw)
        proj.export_document(session, doc_id, root_path=root_path, force_write=True)

    written = (root_path / rel_path).read_text(encoding="utf-8")
    assert written == raw


def test_export_is_a_fixed_point(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """Re-importing an exported file and exporting again changes nothing.

    Fidelity on the first pass is not enough: the projection has to be stable
    under repetition, or `doc drift` reports churn forever.
    """
    rel_path = "docs/adoption-playbook.md"
    raw = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, rel_path, raw)
        proj.export_document(session, doc_id, root_path=root_path, force_write=True)
        first = (root_path / rel_path).read_text(encoding="utf-8")

        proj.import_document(
            session,
            project_id,
            root_path / rel_path,
            author="human:test",
            root_path=root_path,
        )
        proj.export_document(session, doc_id, root_path=root_path, force=True, force_write=True)

    assert (root_path / rel_path).read_text(encoding="utf-8") == first


# ============================================================================ #
# The three F7 defects, pinned individually                                     #
# ============================================================================ #


def test_preamble_is_separated_from_first_heading(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """F7 defect 1: `…заранее.## 1. Зачем` — blockquote glued to an H2."""
    raw = "---\ntype: guide\nstatus: active\n---\n\n# T\n\n> Preamble line.\n\n## First\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        rendered = proj.render_markdown(session, doc_id)

    assert "> Preamble line.\n\n## First" in rendered
    assert "Preamble line.## First" not in rendered


def test_h1_survives_the_round_trip(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """F7 defect 2: the H1 was parsed into `title` and never rendered back."""
    raw = "---\ntype: guide\nstatus: active\n---\n\n# Kept Heading\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        rendered = proj.render_markdown(session, doc_id)

    assert rendered == raw


def test_unknown_type_is_not_rewritten(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """F7 defect 3: `type: capability` came back as `module-spec` (see ADO-015).

    The DB still coerces the unknown value — that is ADO-015's problem — but the
    file must not be rewritten to the coerced type.
    """
    raw = "---\ntype: capability\nstatus: active\n---\n\n# C\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        rendered = proj.render_markdown(session, doc_id)

    assert "type: capability" in rendered
    assert "module-spec" not in rendered


def test_nothing_is_invented_for_a_bare_markdown_file(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """A file with neither frontmatter nor H1 gets neither back."""
    raw = "## Section\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "bare.md", raw)
        rendered = proj.render_markdown(session, doc_id)

    assert rendered == raw


def test_frontmatter_is_reserialised_when_the_db_diverges(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """A DB-side status change reaches the file, keeping the source key order."""
    raw = "---\ntype: guide\nstatus: draft\nowner: docs\n---\n\n# T\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        model = session.get(DocumentModel, doc_id)
        assert model is not None
        model.status = "active"
        session.flush()
        rendered = proj.render_markdown(session, doc_id)

    assert "status: active" in rendered
    assert rendered.index("type:") < rendered.index("status:") < rendered.index("owner:")


# ============================================================================ #
# Export guards (stage 1)                                                       #
# ============================================================================ #


def test_dry_run_reports_the_diff_and_writes_nothing(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    raw = "---\ntype: guide\nstatus: active\n---\n\n# T\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)
    target = root_path / "d.md"
    target.write_text("hand-written\n", encoding="utf-8")

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        result = proj.export_document(session, doc_id, root_path=root_path, dry_run=True)

    assert result.written is False
    assert result.diff is not None
    assert "-hand-written" in result.diff
    assert "+# T" in result.diff
    assert target.read_text(encoding="utf-8") == "hand-written\n"


def test_export_refuses_to_overwrite_an_unknown_file(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """The file on disk matches neither the last export nor the last import."""
    raw = "---\ntype: guide\nstatus: active\n---\n\n# T\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)
    target = root_path / "d.md"
    target.write_text("someone's unsaved work\n", encoding="utf-8")

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        with pytest.raises(proj.ExportGuardError, match="edited in place"):
            proj.export_document(session, doc_id, root_path=root_path)

        proj.export_document(session, doc_id, root_path=root_path, force_write=True)

    assert target.read_text(encoding="utf-8") == raw


def test_export_refuses_a_foreign_checkout_when_asked_to(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """`own_checkout_only=True` (what CLI and MCP pass) blocks foreign roots."""
    raw = "---\ntype: guide\nstatus: active\n---\n\n# T\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        with pytest.raises(proj.ExportGuardError, match="own"):
            proj.export_document(session, doc_id, root_path=root_path, own_checkout_only=True)

        result = proj.export_document(
            session,
            doc_id,
            root_path=root_path,
            own_checkout_only=True,
            force_write=True,
        )

    assert result.written is True
