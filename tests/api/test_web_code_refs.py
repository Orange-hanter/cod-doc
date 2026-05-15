"""OBI-021: /p/<slug>/code-refs index + /preview hover-loader."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, LinkModel, SectionModel
from cod_doc.infra.repositories import ProjectRepository

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def code_refs_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "crp"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)
    (repo / "real.py").write_text("def hi():\n    print('hi')\n")

    entry = ProjectEntry(name="crp", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps
    deps.set_config(cfg)
    Project(entry).init()

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="crp", title="P", root_path=str(repo), config={})
        )
        proj.created = now; proj.updated = now
        session.flush()
        doc = DocumentModel(
            project_id=proj.row_id, doc_key="x/y", path="x/y.md",
            type="guide", status="active", title="D",
            sensitivity="internal",
        )
        doc.created = now; doc.last_updated = now
        session.add(doc); session.flush()
        sec = SectionModel(
            document_id=doc.row_id, anchor="s", heading="S",
            level=2, position=0, body="", content_hash="0",
        )
        session.add(sec); session.flush()
        # One resolved + one broken code-ref.
        session.add(LinkModel(
            project_id=proj.row_id, from_section_id=sec.row_id,
            raw="[hi](real.py)", kind="code",
            to_file_path="real.py", to_symbol=None,
            resolved=True,
        ))
        session.add(LinkModel(
            project_id=proj.row_id, from_section_id=sec.row_id,
            raw="[ghost](missing.py)", kind="code",
            to_file_path="missing.py", to_symbol=None,
            resolved=False, broken_reason="file not found",
        ))
    engine.dispose()

    from cod_doc.api.server import app
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_index_lists_refs_grouped_by_file(code_refs_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = code_refs_client
    r = client.get(f"/p/{entry.name}/code-refs")
    assert r.status_code == 200
    assert "real.py" in r.text
    assert "missing.py" in r.text
    assert "1 resolved" in r.text
    assert "1 broken" in r.text


def test_index_shows_status_icons(code_refs_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = code_refs_client
    r = client.get(f"/p/{entry.name}/code-refs")
    assert "✅" in r.text  # resolved
    assert "⚠️" in r.text  # broken


def test_preview_returns_first_lines_of_real_file(code_refs_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = code_refs_client
    r = client.get(f"/p/{entry.name}/code-refs/preview?path=real.py")
    assert r.status_code == 200
    data = r.json()
    assert data["missing"] is False
    assert "def hi():" in data["lines"]
    assert "    print('hi')" in data["lines"]


def test_preview_missing_file_returns_missing_true(code_refs_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = code_refs_client
    r = client.get(f"/p/{entry.name}/code-refs/preview?path=ghost.py")
    assert r.status_code == 200
    assert r.json()["missing"] is True


def test_preview_rejects_path_escape(code_refs_client) -> None:  # type: ignore[no-untyped-def]
    """Path traversal must be refused — security invariant."""
    client, entry = code_refs_client
    r = client.get(f"/p/{entry.name}/code-refs/preview?path=../../../etc/passwd")
    assert r.status_code == 400


def test_code_refs_tab_in_navigation(code_refs_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = code_refs_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert f'href="/p/{entry.name}/code-refs"' in r.text
