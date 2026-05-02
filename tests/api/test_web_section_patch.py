"""WEB-012: HTMX inline section patch + optimistic concurrency."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    Sensitivity,
)
from cod_doc.domain.entities import (
    Project as ProjectEntity,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as revisions

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_upgrade(db_url: str) -> None:
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=True,
        env={"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url},
        capture_output=True,
    )


@pytest.fixture
def section_client(tmp_path: Path):
    repo = tmp_path / "sec-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    _alembic_upgrade(f"sqlite:///{db_path}")

    entry = ProjectEntry(name="demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    section_row_id = None
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        doc = docs.create(
            session,
            project_id=proj.row_id,
            doc_key="modules/M1/notes",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            title="M1 Notes",
            author="human:dakh",
            owner="human:dakh",
            sensitivity=Sensitivity.INTERNAL,
            preamble="Top.",
        )
        sec = docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="overview",
            heading="Overview",
            level=2,
            position=0,
            body="Initial body **bold**.",
            author="human:dakh",
        )
        section_row_id = sec.row_id
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry, section_row_id


# ── Edit form (GET) ──────────────────────────────────────────────────────


def test_edit_form_renders_textarea_with_current_body(section_client) -> None:
    client, entry, _ = section_client
    r = client.get(
        f"/p/{entry.name}/docs/modules/M1/notes/sections/overview/edit",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "<textarea" in r.text
    # Current body pre-filled
    assert "Initial body **bold**." in r.text
    # Hidden field with head revision (non-empty since add_section wrote one)
    assert 'name="expected_parent_revision_id"' in r.text
    assert 'value="' in r.text


def test_edit_form_404_unknown_anchor(section_client) -> None:
    client, entry, _ = section_client
    r = client.get(
        f"/p/{entry.name}/docs/modules/M1/notes/sections/no-such/edit",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


def test_edit_form_404_unknown_doc(section_client) -> None:
    client, entry, _ = section_client
    r = client.get(
        f"/p/{entry.name}/docs/no/such/doc/sections/overview/edit",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


# ── Cancel (GET .../view) ────────────────────────────────────────────────


def test_cancel_returns_view_fragment(section_client) -> None:
    client, entry, _ = section_client
    r = client.get(
        f"/p/{entry.name}/docs/modules/M1/notes/sections/overview/view",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert '<section id="section-overview"' in r.text
    # Body rendered (markdown → HTML)
    assert "<strong>bold</strong>" in r.text
    # No edit form
    assert "<textarea" not in r.text


# ── Patch (POST) ─────────────────────────────────────────────────────────


def _head_for_section(db_path: Path, section_row_id: int) -> str | None:
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        head = revisions.head_for_entity(session, EntityKind.SECTION, section_row_id)
    engine.dispose()
    return head


def test_patch_htmx_swaps_view_with_new_body(section_client) -> None:
    client, entry, section_row_id = section_client
    db_path = Path(entry.cod_doc_dir) / "state.db"
    head = _head_for_section(db_path, section_row_id)

    new_body = "Edited body — *italics*."
    r = client.post(
        f"/p/{entry.name}/docs/modules/M1/notes/sections/overview",
        data={"body": new_body, "expected_parent_revision_id": head or ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    # Returns the view-fragment with rendered new body
    assert '<section id="section-overview"' in r.text
    assert "<em>italics</em>" in r.text
    # Persisted: GET back the doc shows the new body
    r2 = client.get(f"/p/{entry.name}/docs/modules/M1/notes")
    assert "Edited body" in r2.text


def test_patch_form_post_redirects_with_anchor(section_client) -> None:
    client, entry, section_row_id = section_client
    db_path = Path(entry.cod_doc_dir) / "state.db"
    head = _head_for_section(db_path, section_row_id)

    r = client.post(
        f"/p/{entry.name}/docs/modules/M1/notes/sections/overview",
        data={"body": "Form-post body.", "expected_parent_revision_id": head or ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Anchor preserved in redirect
    assert r.headers["location"].endswith("#overview")


def test_patch_conflict_when_stale_revision_id(section_client) -> None:
    """expected_parent_revision_id mismatch → ConflictWebError → alert OOB."""
    client, entry, _ = section_client

    r = client.post(
        f"/p/{entry.name}/docs/modules/M1/notes/sections/overview",
        data={
            "body": "Conflicting edit.",
            # Garbage parent → service raises RevisionConflictError
            "expected_parent_revision_id": "01ZZZZZZZZZZZZZZZZZZZZZZZZ",
        },
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 409
    assert "alert-warning" in r.text
    assert "Конфликт ревизий" in r.text


def test_patch_no_op_when_body_unchanged(section_client) -> None:
    """Same-body patch is a no-op at service level; returns view fragment."""
    client, entry, section_row_id = section_client
    db_path = Path(entry.cod_doc_dir) / "state.db"
    head = _head_for_section(db_path, section_row_id)

    r = client.post(
        f"/p/{entry.name}/docs/modules/M1/notes/sections/overview",
        data={
            "body": "Initial body **bold**.",  # unchanged
            "expected_parent_revision_id": head or "",
        },
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert '<section id="section-overview"' in r.text


def test_patch_404_unknown_doc(section_client) -> None:
    client, entry, _ = section_client
    r = client.post(
        f"/p/{entry.name}/docs/no/such/doc/sections/overview",
        data={"body": "x", "expected_parent_revision_id": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


def test_patch_404_unknown_anchor(section_client) -> None:
    client, entry, _ = section_client
    r = client.post(
        f"/p/{entry.name}/docs/modules/M1/notes/sections/no-such",
        data={"body": "x", "expected_parent_revision_id": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


# ── doc_show carries head revision into edit form ────────────────────────


def test_doc_show_embeds_edit_button(section_client) -> None:
    client, entry, _ = section_client
    r = client.get(f"/p/{entry.name}/docs/modules/M1/notes")
    assert r.status_code == 200
    # Edit ✎ button wired to the form endpoint
    assert "section-edit-btn" in r.text
    assert (
        'hx-get="/p/demo/docs/modules/M1/notes/sections/overview/edit"' in r.text
    )
